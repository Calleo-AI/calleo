# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt        # serving only (Flask server, snapshot restore)
pip install -r requirements-crawl.txt  # adds the crawl/rebuild pipeline (browserless —
                                       # httpx + trafilatura + markdownify, no playwright)
pip install -r requirements-dev.txt    # adds pytest (tests also need requirements-crawl)

# Run the Flask chat server (port 5000 by default)
cd agent_chatbot && python server.py

# Run the analysis agent (reads DB logs, writes a markdown report, emails it)
cd agent_analysis && python analysis_agent.py

# Rebuild the ChromaDB knowledge base from the live website (one click)
python Database/create_db.py
python Database/create_db.py --dry-run      # crawl + report only, no DB writes
python Database/create_db.py --max-pages 8  # smoke test on a subset
python Database/create_db.py --prune        # also delete sources no longer crawled

# Refresh specific URLs in the DB (or all if none given)
python Database/update_db.py [URL ...]
python Database/update_db.py --dry-run
python Database/update_db.py --collection full_database URL

# Run all tests
pytest tests/

# Run a single test file or test
pytest tests/test_chatbot.py
pytest tests/test_server.py::TestChatEndpoint::test_missing_message_returns_400
```

## Environment Variables (`.env` in repo root)

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Gemini embeddings (`gemini-embedding-001`) |
| `OPENROUTER_API_KEY` | Qwen model (`qwen/qwen3.5-397b-a17b`) for chat responses and analysis |
| `CHROMA_DB_PATH` | Absolute path to the ChromaDB persistent storage directory |
| `SENDER_EMAIL` / `SENDER_PASSWORD` / `RECIPIENT_EMAIL` | Email credentials for automated report delivery |
| `SMTP_SERVER` / `SMTP_PORT` | SMTP config (defaults: `smtp.gmail.com`, `587`) |

## Architecture

### Request flow

```
Browser iframe  →  chatbot.js  →  Flask /chat
                               →  get_relevant_documents (ChromaDB)
                               →  make_prompt (constructs system prompt)
                               →  Qwen via OpenRouter (generates answer)
                               →  logs to full_database_conversations
```

### Key architectural facts

**All school-specific values live in two config files.** `school_config.py` (repo root) holds every backend value — school identity, crawl targets and exclusions, prompt facts/rules, and canned response strings; `frontend/school_config.js` holds the widget's branding, translations, API base, and localStorage prefix. No school name, URL, or policy should ever be hardcoded elsewhere; tests import the same constants. The canned `DEFERRAL_MESSAGE` is load-bearing: `server.py` sends it and `dashboard_data.py` matches it to classify unanswered questions.

**All LLM and embedding calls go through `llm_client.py` (repo root).** It is the single provider seam — the only module that imports the OpenAI SDK (pointed at OpenRouter) or constructs the Gemini embedding function, and the only place model ids and OpenRouter `extra_body` reasoning syntax appear. Use `llm_client.chat(messages, role=..., temperature=..., reasoning=...)` for completions and `llm_client.get_embedding_function()` for the shared Gemini embedding function. Roles map to env-overridable models: `chat`→Qwen (`CHAT_MODEL`), `analysis`/`judge`→DeepSeek (`ANALYSIS_MODEL`/`JUDGE_MODEL`), embeddings→`gemini-embedding-001` (`EMBED_MODEL`). `reasoning` is semantic: `None` omits the field, `"off"` disables reasoning, `"high"` sets high effort; `temperature=None` is omitted from the request. Both the chat client and the embedding function are lazy singletons, and the `openai` import is deferred into the client factory so embedding-only consumers (the DB build/refresh pipeline, one-off scripts) never import `openai` or need `OPENROUTER_API_KEY`. `chat()` raises on failure — each caller keeps its own fallback. **This module is the seam to rewrite for a future Vertex AI migration; no call site should need to change.**

**The chatbot iframe is `chatbot_iframe.html`**, which loads `chatbot.js` and `chatbot.css`. It posts to the `/chat` endpoint.

**ChromaDB collections in use by the server** (`agent_chatbot/server.py`):
- `full_database` — the main knowledge base queried on every non-greeting request
- `conversations` — legacy collection (no longer written to)
- `full_database_conversations` — logs `/chat` interactions; also the source for the analysis agent

**Chunk metadata convention:** every crawled chunk carries `{"source": url, "title": page_title, "extractor": strategy}` plus `"section": breadcrumb` when the page was split on headings. Chunk text is prefixed with a contextual header (`Document: <title>` / `Source: <url>` / `Section: <breadcrumb>`) so retrieval never loses page context. Manual entries added via `insert_content.py` have only `{"source": ...}` and are preserved by rebuilds unless `--prune` is passed.

**`make_prompt` in `agent_chatbot/chatbot.py`** is the single function that builds the entire system prompt. It accepts `query`, `relevant_passage`, `history=[]`, and `language="English"`. The language instruction is injected just before the `ANSWER:` label so the model always responds in the user-selected language regardless of what language the user typed in.

**Language selector** is in the iframe header. The selected value is sent as `"language"` in every API request JSON body and passed through `server.py → make_prompt`. Welcome screen text translates immediately on change via `updateWelcomeText()` in `chatbot.js`, which holds translations for all 7 supported languages.

**Rate limiting + spam detection** in `server.py` runs before any LLM call:
1. Flask-Limiter (20 req/min) keyed on a SHA-256 client fingerprint (IP + User-Agent + Accept headers)
2. Custom `spam_tracker` catches gibberish (<50% alphanumeric), duplicate messages (same message ≥3× in 5 min), and rapid-fire (≥10 messages in 60 s)

### Analysis agent (`agent_analysis/`)

`analysis_agent.py` fetches all documents from `full_database_conversations`, parses `"User: ...\nAI: ..."` log format, sends the batch to Qwen for trend analysis, and writes a dated markdown report to `agent_analysis/analysis_reports/`. `email_report.py` then emails the report as an attachment.

### Database build pipeline (`Database/`)

The crawl/extraction pipeline is fully deterministic — no LLM calls:

- `discovery.py` — fetches the site's XML sitemap from `school_config.SITEMAP_URL` (Blackbaud sites often serve it at `/sitemap`, NOT `/sitemap.xml`), normalizes URLs (strips `/page/` duplicates, fragments, query strings) and applies the config's `EXCLUDED_URL_PATTERNS` (careers, fundraising, dated news/trips, robots-disallowed paths). Blackbaud sitemaps generate `<lastmod>` values at request time, making them useless for change detection.
- `extraction.py` — pure-function extraction chain over raw HTML: (1) BeautifulSoup selector extraction for the Blackbaud CMS markup (`div.page-row` regions, dropping `span1-7` promo sidebars and `.element-invisible` a11y stubs that produce "List of N items." junk) → (2) trafilatura (recall-favoring markdown). HTML→markdown via markdownify. Escalates when output < `MIN_CHARS`; the winning strategy is recorded in chunk metadata as `extractor`.
- `chunking.py` — heading-aware chunking: pages ≤2000 chars stay whole; larger pages split on `#`/`##`/`###` with small sections merged into neighbors; every chunk gets the contextual header described above.
- `pipeline.py` — `crawl_pages()` (browserless httpx fetch, 4 concurrent, retry/backoff; HTTP 4xx and Blackbaud soft-404s classified as `dead_url`) and `build_chunks()` (pages → chunks + per-page stats).
- `create_db.py` — one-click full rebuild: snapshot → crawl → build into a staging collection → validation gate (≥90% page success, key-page content checks, min chunk count) → embedding-preserving swap into the live collection → `rebuild_report.txt`. The live collection is never touched if validation fails.
- `update_db.py` — per-URL refresh on the same pipeline. Crawl-first semantics: existing chunks are only deleted after a successful re-crawl.
- `snapshot_db.py` — snapshot/rollback (used by `create_db.py` and the server's empty-DB auto-restore).
- Other scripts (`clear.py`, `query_chunks.py`, `delete_chunk.py`, `insert_content.py`, etc.) are one-off maintenance utilities.

## Testing

Tests live in `tests/` and use pytest with `unittest.mock`. The pattern for each test module:

1. Set dummy env vars (`OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `CHROMA_DB_PATH`) before importing the module under test.
2. Patch `chatbot.get_chroma_db` so module-level DB initialisation doesn't hit real ChromaDB.
3. Test pure functions directly; mock LLM calls with `MagicMock` / `AsyncMock`.

`discovery.py`, `chunking.py`, and `extraction.py` are pure and tested without mocks; `tests/fixtures/*.html` are small hand-written Blackbaud-shaped pages for extraction tests. `test_create_db.py` / `test_update_db.py` use a shared `FakeCollection` stand-in instead of real ChromaDB. `tests/conftest.py` imports pyarrow first to pin Windows DLL load order — without it, collection crashes with an access violation when chromadb/grpc load before pyarrow.
