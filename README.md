# Calleo

*Calleo* — Latin for "I am clever." Gives your school or NGO website a mind.

Calleo is a self-hosted RAG chatbot template for schools and NGOs. It crawls
your organization's site into a ChromaDB vector database, answers visitor
questions through an embeddable chat widget (multi languages), and ships with an
analytics dashboard, automated faithfulness scoring, and a weekly analysis
agent that emails a trend report.

Nothing in the pipeline assumes a school: if your content lives on a public
website, Calleo can answer questions about it — admissions and programs for a
school, or services, eligibility, and donation info for an NGO. Point it at
your sitemap and fill in two config files.

Built by students at Crescent School, Toronto, with the support of faculty staff and the IT department. It is the first of our open-source projects, with many more to come (we hope). Battle-tested in production on Crescent School's website; released under the MIT license.

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
| `site_config.py` | **Edit me** — all organization-specific backend config |
| `frontend/site_config.js` | **Edit me** — all organization-specific widget config |
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
git clone https://github.com/Calleo-AI/calleo.git
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

1. **Fill in `.env`** — `OPENROUTER_API_KEY`, `CHROMA_DB_PATH`
   (see `.env.example` for every option). OpenRouter is the only provider:
   chat, analysis, and embeddings all go through it.
2. **Configure your organization** — edit the config files below.
3. **Build the knowledge base**: `python Database/create_db.py`
   (try `--dry-run` first to preview the crawl without writing anything).
4. **Run the server**: `cd agent_chatbot && python server.py`
   then open `http://localhost:5000/chatbot_iframe.html`.

## Configure for your organization

The repo ships configured for a fictional **Example Site** so everything runs
out of the box. Two config files carry every organization-specific value, and a
third holds branding colors. Nothing in the config, the crawler, or the prompts
assumes a particular kind of organization — `SITE_NAME = "Rivertown Food Bank"`
works exactly as well as a school name.

### `site_config.py` (backend)

| Field | What it controls |
|---|---|
| `SITE_NAME` / `SITE_SHORT_NAME` | Your organization's name — prompts, canned replies, report headers |
| `SITE_ROOT` / `SITEMAP_URL` | Where the crawler discovers pages (Blackbaud sites often serve `/sitemap`, not `/sitemap.xml`) |
| `ROBOTS_DISALLOWED_PATHS` | Top-level paths from your robots.txt |
| `EXCLUDED_URL_PATTERNS` | Regexes for off-topic pages (careers, donation checkout, dated news…) |
| `USER_AGENT` | Crawler User-Agent header |
| `TITLE_SUFFIX_RE` | Strips your site's suffix from page titles |
| `KEY_PAGE_CHECKS` | Rebuild sanity gate: these pages must contain these words |
| `SITE_FACTS` | Authoritative facts injected into every prompt (also grounds the faithfulness judge) |
| `CUSTOM_PROMPT_RULES` | Rules specific to your organization, prepended to the system prompt |
| `DESIGNED_BY` | Credit line when users ask who built the bot (`""` to omit) |
| `GREETING_MESSAGE` / `DEFERRAL_MESSAGE` / … | Canned responses (single source of truth — the dashboard classifies unanswered questions by matching `DEFERRAL_MESSAGE`) |

### `frontend/site_config.js` (chat widget)

| Field | What it controls |
|---|---|
| `siteName` | Welcome text (`{site}` placeholder in translations) |
| `contactEmail` | Contact banner + welcome screen |
| `apiBase` | Chat server origin (`""` = same origin) |
| `storagePrefix` | localStorage namespace |
| `welcomeTranslations` | Welcome text in all 7 languages |

### `frontend/chatbot.css` (branding)

The `:root` block at the top defines `--brand-*` color variables — swap in your
own palette. `frontend/embed-snippet.html` and `dashboard.html` each have
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
python Database/snapshot_db.py --rollback --collection full_database   # restore the last snapshot
pytest tests/                                # run the test suite
```

## Environment variables

Everything lives in `.env` (see `.env.example`): `OPENROUTER_API_KEY` is the
only required key — chat, analysis, and embeddings (`google/gemini-embedding-001`)
all go through OpenRouter; `CHROMA_DB_PATH` should point outside the repo; email
vars are optional; `CHAT_MODEL` / `ANALYSIS_MODEL` / `JUDGE_MODEL` /
`EMBED_MODEL` override the default models per role — every LLM call goes
through `llm_client.py`, so switching providers is a one-file change.

### Upgrading a database built before the OpenRouter migration

Collections created when embeddings came from the Google API have that provider
recorded in their ChromaDB config and refuse to open now. Rewrite the recorded
provider once — the stored vectors come from the same `gemini-embedding-001`
model and are kept as-is:

```bash
python Database/migrate_embedding_config.py --dry-run   # preview
python Database/migrate_embedding_config.py
```

## Testing

```bash
pytest tests/                                  # 261 tests, no network needed
node --test tests/frontend/test_chat_history_store.mjs
```

Tests assert against the shipped Example Site config; if you change
`site_config.py`, a few config-reflecting assertions will reflect your values.

## Contributing

PRs welcome. Run `pytest tests/` before submitting. Please keep
organization-specific values out of code — they belong in the config files.

## License

[MIT](LICENSE)
