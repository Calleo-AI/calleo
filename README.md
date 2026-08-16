# Calleo

*Calleo* — Latin for "I am clever." Gives your school or NGO website a mind.

Calleo is a self-hosted RAG chatbot template for your websites. It crawls
your site into a ChromaDB vector database, answers user
questions through an embeddable chat widget (7 languages), and ships with an
analytics dashboard, automated faithfulness scoring, and a weekly analysis
agent that emails a trend report.

Built by students from Crescent School, Toronto, alongside the support from faculty and staff. It is the first of our opensource projects with many to come (hopefully). It is battle-tested in production on Crescent School's website; released under the MIT license.

## Architecture

```
Browser iframe  →  chatbot.js  →  Flask /chat
                               →  ChromaDB (full_database) — vector search
                               →  LLM via OpenRouter — generates answer
                               →  logs to full_database_conversations
                               →  faithfulness judge (async) audits answers
```

**Key modules:**

| Path | Purpose |
|---|---|
| `school_config.py` | **Edit me** — all school-specific backend config |
| `frontend/school_config.js` | **Edit me** — all school-specific widget config |
| `agent_chatbot/server.py` | Flask API (rate limiting, spam detection, chat endpoint) |
| `agent_chatbot/chatbot.py` | Prompt construction, ChromaDB retrieval |
| `agent_analysis/analysis_agent.py` | Weekly conversation-trend report + email |
| `agent_analysis/faithfulness_scorer.py` | LLM judge that audits answers against retrieved chunks |
| `Database/create_db.py` | Full knowledge-base rebuild with validation gate |
| `Database/update_db.py` | Incremental per-URL refresh |
| `llm_client.py` | Single provider seam for all LLM + embedding calls |
| `frontend/chatbot_iframe.html` | Embeddable iframe shell |
| `dashboard.html` | Analytics dashboard (deployable as a static site) |

## Quickstart

```bash
git clone https://github.com/Kevin09sun/calleo.git
cd calleo

# Linux VM: one-shot provisioning (venv, deps, .env template, cron jobs)
chmod +x setup.sh && ./setup.sh

# Or manually (any OS):
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt        # Flask server + ChromaDB
pip install -r requirements-crawl.txt  # DB build/refresh pipeline
pip install -r requirements-dev.txt    # pytest
cp .env.example .env
```

Then:

1. **Fill in `.env`** — `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `CHROMA_DB_PATH`
   (see `.env.example` for every option).
2. **Configure your school** — edit the two config files below.
3. **Build the knowledge base**: `python Database/create_db.py`
   (try `--dry-run` first to preview the crawl without writing anything).
4. **Run the server**: `cd agent_chatbot && python server.py`
   then open `http://localhost:5000/chatbot_iframe.html`.

## Configure for your school

The repo ships configured for a fictional **Example School** so everything runs
out of the box. Three files carry every school-specific value:

### `school_config.py` (backend)

| Field | What it controls |
|---|---|
| `SCHOOL_NAME` / `SCHOOL_SHORT_NAME` | Prompts, canned replies, report headers |
| `SITE_ROOT` / `SITEMAP_URL` | Where the crawler discovers pages (Blackbaud sites often serve `/sitemap`, not `/sitemap.xml`) |
| `ROBOTS_DISALLOWED_PATHS` | Top-level paths from your robots.txt |
| `EXCLUDED_URL_PATTERNS` | Regexes for off-topic pages (careers, donations, dated news…) |
| `USER_AGENT` | Crawler User-Agent header |
| `TITLE_SUFFIX_RE` | Strips your site's suffix from page titles |
| `KEY_PAGE_CHECKS` | Rebuild sanity gate: these pages must contain these words |
| `SCHOOL_FACTS` | Authoritative facts injected into every prompt (also grounds the faithfulness judge) |
| `CUSTOM_PROMPT_RULES` | School-specific rules for the system prompt |
| `DESIGNED_BY` | Credit line when users ask who built the bot (`""` to omit) |
| `GREETING_MESSAGE` / `DEFERRAL_MESSAGE` / … | Canned responses (single source of truth — the dashboard classifies unanswered questions by matching `DEFERRAL_MESSAGE`) |

### `frontend/school_config.js` (chat widget)

| Field | What it controls |
|---|---|
| `schoolName` | Welcome text (`{school}` placeholder in translations) |
| `contactEmail` | Contact banner + welcome screen |
| `apiBase` | Chat server origin (`""` = same origin) |
| `storagePrefix` | localStorage namespace |
| `welcomeTranslations` | Welcome text in all 7 languages |

### `frontend/chatbot.css` (branding)

The `:root` block at the top defines `--brand-*` color variables — swap in your
school's palette. `frontend/embed-snippet.html` and `dashboard.html` each have
`EDIT ME` comments for the values they can't share (embed host, hint-popup
color, dashboard API base).

## Embedding on your website

Paste the contents of `frontend/embed-snippet.html` into an HTML embed widget
on your site (verified with the Blackbaud CMS; any CMS that accepts raw HTML
works). Point its iframe `src` at wherever you host `chatbot_iframe.html` —
the Flask server serves it at `/chatbot_iframe.html`, or host the `frontend/`
folder on any static host.

## Dashboard

`dashboard.html` is a single-file analytics dashboard (traffic, topics,
unanswered questions, faithfulness scores) that reads the server's
`/api/dashboard*` endpoints. Serve it from the Flask server at `/dashboard`,
or deploy it as a static site — the included `vercel.json` deploys just the
dashboard to Vercel; set `apiBase` in its `__DASHBOARD_CONFIG__` to your chat
server's origin.

## Analysis agent & email reports (optional)

```bash
python agent_analysis/analysis_agent.py   # writes a dated markdown report + emails it
```

Configure `SENDER_EMAIL` / `SENDER_PASSWORD` / `RECIPIENT_EMAIL` (comma-separated
list supported) in `.env`. `setup.sh` registers weekly crons: DB rebuild Sunday
3 AM, analysis email Sunday 7 AM.

## Common commands

```bash
python Database/create_db.py                 # full rebuild (snapshot → crawl → validate → swap)
python Database/create_db.py --dry-run       # crawl + report only, no DB writes
python Database/create_db.py --max-pages 8   # quick smoke test
python Database/update_db.py <URL>           # refresh specific pages
python Database/snapshot_db.py --rollback    # restore the last snapshot
pytest tests/                                # run the test suite
```

## Environment variables

Everything lives in `.env` (see `.env.example`): `GEMINI_API_KEY` and
`OPENROUTER_API_KEY` are required; `CHROMA_DB_PATH` should point outside the
repo; email vars are optional; `CHAT_MODEL` / `ANALYSIS_MODEL` / `JUDGE_MODEL` /
`EMBED_MODEL` override the default models per role — every LLM call goes
through `llm_client.py`, so switching providers is a one-file change.

## Testing

```bash
pytest tests/                                  # 228 tests, no network needed
node --test tests/frontend/test_chat_history_store.mjs
```

Tests assert against the shipped Example School config; if you change
`school_config.py`, a few config-reflecting assertions will reflect your values.

## Contributing

PRs welcome. Run `pytest tests/` before submitting. Please keep school-specific
values out of code — they belong in the config files.

## License

[MIT](LICENSE)
