#!/bin/bash
# setup.sh — provision a fresh Linux VM to run Calleo.
#
# Run once after cloning the repo:
#   chmod +x setup.sh && ./setup.sh
#
# After this script finishes you MUST fill in your API keys in .env before
# starting the server or building the database.

set -e
REPO="$(cd "$(dirname "$0")" && pwd)"

# ── helpers ────────────────────────────────────────────────────────────────────
info()  { echo "[INFO]  $*"; }
ok()    { echo "[OK]    $*"; }
warn()  { echo "[WARN]  $*"; }
die()   { echo "[ERROR] $*" >&2; exit 1; }

# ── 1. OS check ────────────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
    die "This script targets Linux. On Windows/macOS set up the virtualenv manually."
fi

# ── 2. System packages ─────────────────────────────────────────────────────────
info "Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git curl ca-certificates

# Prefer Python 3.11 if available (chromadb + grpc are happiest there)
PYTHON=$(command -v python3.11 || command -v python3)
info "Using Python: $($PYTHON --version)"

PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")
if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
    die "Python 3.10+ is required. Found $($PYTHON --version)."
fi

# ── 3. Virtual environment ─────────────────────────────────────────────────────
VENV="$REPO/venv"
if [[ ! -d "$VENV" ]]; then
    info "Creating virtual environment at $VENV …"
    "$PYTHON" -m venv "$VENV"
    ok "Virtual environment created."
else
    ok "Virtual environment already exists — skipping creation."
fi

PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

# ── 4. Python dependencies ─────────────────────────────────────────────────────
info "Installing Python dependencies (this may take a minute)…"
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet -r "$REPO/requirements.txt"
"$PIP" install --quiet -r "$REPO/requirements-crawl.txt"
ok "Python dependencies installed."

# ── 5. .env file ───────────────────────────────────────────────────────────────
ENV_FILE="$REPO/.env"
if [[ -f "$ENV_FILE" ]]; then
    ok ".env already exists — skipping template creation."
else
    info "Creating .env from .env.example…"
    cp "$REPO/.env.example" "$ENV_FILE"
    # Default CHROMA_DB_PATH: a sibling directory that lives outside the repo
    CHROMA_DEFAULT="/home/$(whoami)/site_chatbot_db"
    sed -i "s|^CHROMA_DB_PATH=.*|CHROMA_DB_PATH=$CHROMA_DEFAULT|" "$ENV_FILE"
    warn ".env created with placeholder values."
    warn "Fill in OPENROUTER_API_KEY and CHROMA_DB_PATH before continuing."
fi

# ── 6. ChromaDB storage directory ──────────────────────────────────────────────
# Resolve CHROMA_DB_PATH from .env and pre-create the directory.
CHROMA_DB_PATH=$(grep -E '^CHROMA_DB_PATH=' "$ENV_FILE" | cut -d= -f2-)
if [[ -n "$CHROMA_DB_PATH" && "$CHROMA_DB_PATH" != *"your_"* ]]; then
    mkdir -p "$CHROMA_DB_PATH"
    ok "ChromaDB directory ready: $CHROMA_DB_PATH"
else
    warn "CHROMA_DB_PATH not yet set. The directory will need to be created before running create_db.py."
fi

# ── 7. Cron jobs ───────────────────────────────────────────────────────────────
info "Setting up cron jobs…"

PIDFILE="$REPO/gunicorn.pid"
WEEKLY_CMD="0 3 * * 0  $REPO/scripts/weekly_rebuild.sh >> $REPO/Database/rebuild.log 2>&1"
# The analysis report covers a trailing 7-day window, so it is emailed weekly
# (Sundays 7 AM) — not daily. NOTE: the guard below only checks whether *any*
# analysis_agent.py cron exists; it will not rewrite an older daily entry. To
# switch an existing install from daily to weekly, edit the crontab by hand
# (crontab -e) and change `0 7 * * *` to `0 7 * * 0`.
WEEKLY_ANALYSIS_CMD="0 7 * * 0   $VENV/bin/python $REPO/agent_analysis/analysis_agent.py >> $REPO/agent_analysis/analysis.log 2>&1"

# Add only if not already present
( crontab -l 2>/dev/null | grep -qF "weekly_rebuild.sh" ) \
    && ok "Weekly rebuild cron already installed." \
    || { (crontab -l 2>/dev/null; echo "$WEEKLY_CMD") | crontab -; ok "Weekly rebuild cron added (Sun 3 AM)."; }

( crontab -l 2>/dev/null | grep -qF "analysis_agent.py" ) \
    && ok "Weekly analysis cron already installed." \
    || { (crontab -l 2>/dev/null; echo "$WEEKLY_ANALYSIS_CMD") | crontab -; ok "Weekly analysis cron added (Sun 7 AM)."; }

# ── 8. Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
echo "  Setup complete!"
echo "══════════════════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Fill in your API keys in:"
echo "       $ENV_FILE"
echo ""
echo "  2. Build the ChromaDB knowledge base (first-time only):"
echo "       $PY $REPO/Database/create_db.py"
echo ""
echo "  3. Start the chatbot server:"
echo "       cd $REPO/agent_chatbot"
echo "       $VENV/bin/gunicorn server:app --bind 0.0.0.0:5000 --pid $PIDFILE --daemon"
echo ""
echo "  4. (Optional) Run the analysis agent manually:"
echo "       $PY $REPO/agent_analysis/analysis_agent.py"
echo ""
echo "  Cron jobs registered:"
echo "    • Weekly DB rebuild: Sunday 3 AM"
echo "    • Weekly analysis email: Sunday 7 AM"
echo ""
