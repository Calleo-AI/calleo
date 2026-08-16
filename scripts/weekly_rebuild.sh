#!/bin/bash
# Weekly knowledge-base rebuild + server restart for the chatbot VM.
#
# Install with crontab -e (adjust the repo path):
#   0 3 * * 0 /home/<user>/calleo/scripts/weekly_rebuild.sh >> /home/<user>/calleo/Database/rebuild.log 2>&1
#
# set -e: if the rebuild fails (validation gate, quota, network), the script
# stops BEFORE the restart — the server keeps serving the previous data.
set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/venv/bin/python"
GUNICORN="$REPO/venv/bin/gunicorn"
PIDFILE="$REPO/gunicorn.pid"

echo "=== $(date -Iseconds) weekly rebuild start ==="
cd "$REPO"

# Rebuild from the live website. Exits non-zero (live collection untouched)
# if validation fails.
"$PY" Database/create_db.py

# Fresh post-rebuild snapshot = restore point for the server's empty-DB
# auto-restore and for snapshot_db.py --rollback.
"$PY" Database/snapshot_db.py --collection full_database

# Restart gunicorn so the server picks up the new collection (a running
# server holds a stale handle after the rebuild's delete/recreate).
# Kill by PID file, not pkill -f: pattern matching on command lines has
# previously taken down the cloudflared tunnel alongside gunicorn.
if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    sleep 2
fi
cd "$REPO/agent_chatbot"

# --daemon (NOT `nohup ... &`) is what makes the restart survive.
# Under cron, `nohup ... &` leaves gunicorn as a backgrounded child of the
# cron job; when this script exits, systemd reaps the cron job's cgroup and
# kills gunicorn with it — the server goes down every rebuild and never
# comes back. --daemon double-forks and re-parents gunicorn to init, so it
# outlives the cron session. --capture-output routes worker stdout/stderr
# to the error log (a daemonized gunicorn otherwise drops them).
"$GUNICORN" server:app --bind 0.0.0.0:5000 --pid "$PIDFILE" \
    --daemon --capture-output \
    --access-logfile "$REPO/gunicorn.log" \
    --error-logfile "$REPO/gunicorn.log"

# Confirm the restart actually took, so a failed bind shows up in the log
# instead of silently leaving the site down until Monday.
sleep 3
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "gunicorn restarted OK (pid $(cat "$PIDFILE"))"
else
    echo "ERROR: gunicorn did not come back up — check $REPO/gunicorn.log" >&2
    exit 1
fi

echo "=== $(date -Iseconds) weekly rebuild done ==="
