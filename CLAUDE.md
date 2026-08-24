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

# One-time migration for a DB built before the OpenRouter embedding migration
python Database/migrate_embedding_config.py --dry-run
python Database/migrate_embedding_config.py

# Refresh specific URLs in the DB (or all if none given)
python Database/update_db.py [URL ...]
python Database/update_db.py --dry-run
python Database/update_db.py --collection full_database URL

# Validate a workflow spec without starting the server
python -c "import sys; sys.path.insert(0,'agent_chatbot'); sys.path.insert(0,'.'); \
import workflow_specs as ws; print(ws.load_spec('workflows/contact_intake.json')[1])"

# Run all tests
pytest tests/
node --test 'tests/frontend/test_*.mjs'   # widget unit tests (no npm install needed)

# Run a single test file or test
pytest tests/test_chatbot.py
pytest tests/test_server.py::TestChatEndpoint::test_missing_message_returns_400
```

## Environment Variables (`.env` in repo root)

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | The only provider key: chat (`qwen/qwen3.5-397b-a17b`), analysis, and embeddings (`google/gemini-embedding-001`) |
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

A guided workflow takes a **separate path** and shares none of that pipeline:

```
Browser iframe  →  workflow_client.js  →  Flask /api/workflow/turn
                   (holds the state)   →  workflow_engine.handle_turn
                                       →  judge_answer (one cheap LLM call)
                                       →  deterministic advance + render
```

### Key architectural facts

**All site-specific values live in two config files.** `site_config.py` (repo root) holds every backend value — site identity, crawl targets and exclusions, prompt facts/rules, and canned response strings; `frontend/site_config.js` holds the widget's branding, translations, API base, and localStorage prefix. No organization name, URL, or policy should ever be hardcoded elsewhere; tests import the same constants. The canned `DEFERRAL_MESSAGE` is load-bearing: `server.py` sends it and `dashboard_data.py` matches it to classify unanswered questions.

Calleo is deliberately organization-agnostic: it targets schools, NGOs, and any other site with public content. Keep prompts, comments, and defaults neutral — say "site" or "organization", not "school". The repo ships configured for a fictional **Example Site** so the suite is deterministic.

**All LLM and embedding calls go through `llm_client.py` (repo root), and they all go to OpenRouter.** It is the single provider seam — the only module that imports the OpenAI SDK (pointed at OpenRouter), and the only place model ids and OpenRouter `extra_body` reasoning syntax appear. There is no second provider and no second API key: `OPENROUTER_API_KEY` covers chat, analysis, and embeddings. Use `llm_client.chat(messages, role=..., temperature=..., reasoning=...)` for completions and `llm_client.get_embedding_function()` for the shared embedding function. Roles map to env-overridable models: `chat`→Qwen (`CHAT_MODEL`), `analysis`/`judge`→DeepSeek (`ANALYSIS_MODEL`/`JUDGE_MODEL`), embeddings→`google/gemini-embedding-001` (`EMBED_MODEL`). `reasoning` is semantic: `None` omits the field, `"off"` disables reasoning, `"high"` sets high effort; `temperature=None` is omitted from the request. Both the client and the embedding function are lazy singletons, and the `openai` import is deferred into the client factory so importing the module stays cheap for model-id-only consumers. `chat()` raises on failure — each caller keeps its own fallback. **This module is the seam to rewrite for a future provider migration; no call site should need to change.**

`OpenRouterEmbeddingFunction` (in `llm_client.py`) is the ChromaDB-side half of that seam: it posts to OpenRouter's OpenAI-compatible `/embeddings` endpoint in batches of `EMBED_BATCH_SIZE` (transient failures are retried inside the shared SDK client, `MAX_RETRIES`) and declares `default_space() == "cosine"` (the metric the collections were built under). It registers itself with ChromaDB under the name `openrouter`, which is what gets persisted in each collection's config — so a database built before this migration records `google_generative_ai` instead and raises an embedding-function-conflict `ValueError` on open until `python Database/migrate_embedding_config.py` rewrites it (vectors are kept: same underlying model).

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

**Voice input is entirely client-side and touches no Python.** `frontend/voice_input.js` wraps the browser's Web Speech API (`SpeechRecognition` / `webkitSpeechRecognition`) as a DOM-free IIFE publishing `window.VoiceInput`, unit-tested in Node with a fake recognizer. This is deliberate: OpenRouter exposes no audio-transcription endpoint, so server-side STT would mean a second provider and a second key, breaking the single-key invariant above. Firefox has no Web Speech API, so `isSupported()` is false there and the mic button is never revealed. Two things are easy to break: the embed iframe must carry `allow="…; microphone"` (`frontend/embed-snippet.html`, asserted by `test_embed_snippet.py`) or dictation silently dies on every embed; and `chatInput.value = x` does NOT fire the `input` listener that resizes the box and enables the send button — use `setInputValue()`.

**Guided workflows (`agent_chatbot/workflow_engine.py` + `workflow_specs.py`, specs in `workflows/*.json`).** A questionnaire the assistant walks a visitor through. The load-bearing design choice: **the model never decides what happens next and never writes the final document.** Sequencing, follow-up budgets, skip handling, progress and rendering are deterministic Python; the LLM is called at most once per answer, to judge sufficiency AND normalize the answer into typed fields (`value`, `confidence`). Because normalization happens per turn, the final document is pure string substitution — which is what makes "never invent a number" structurally true, removes the prompt-injection sink, and leaves ~90% of the engine testable with no mocks. `parse_judge_output` **fails open**: unparseable JSON accepts the answer rather than re-asking a question the person already answered.

Workflow state is an opaque JSON blob the *client* holds and echoes back, exactly as `/chat` round-trips `history` — so the server stays stateless and multi-worker safe. That makes it untrusted input: `validate_state` re-checks every bound on arrival (spec hash, cursor range, blob size, per-answer truncation, enum coercion) and is the security boundary for the whole feature. A spec edited mid-run raises `WorkflowSpecChanged`, which the route answers with a 409 carrying a migrated state rather than losing the answers.

**Workflow routes are separate from `/chat` on purpose, not for tidiness.** `check_spam` would fire on ordinary answers — `is_gibberish("~40% / 60% (+-5)")` is True, and the duplicate rule blocks a third identical "I don't know", which is normal in an interview. Retrieval and the faithfulness judge are meaningless without retrieved chunks. And workflow turns must stay out of `full_database_conversations`, which `dashboard_data` reads unfiltered and which costs an embedding call per `add()`. Being separate routes means none of that is a conditional — it simply is not reached. `tests/test_workflow_endpoints.py` pins all four.

**Frontend asset routes are one allowlisted handler**, not one route per file (`_FRONTEND_FILES` / `_serve_frontend` in `server.py`). Add a new widget file to that set or it 404s. The catch-all `/<path:filename>` only ever serves names in the allowlist.

**Workflow transcripts are stored as ordinary `{role, content}` pairs** so `renderChatIntoBox` replays them untouched on reload — this avoids bumping `SCHEMA_VERSION` in `chat_history_store.js`, which silently discards every saved chat. A run is deliberately NOT a `ChatHistoryStore` chat; if it were, its turns would be sent to `/chat` as `history` and trigger `/generate-title` on an interview prompt.

### Analysis agent (`agent_analysis/`)

`analysis_agent.py` fetches all documents from `full_database_conversations`, parses `"User: ...\nAI: ..."` log format, sends the batch to Qwen for trend analysis, and writes a dated markdown report to `agent_analysis/analysis_reports/`. `email_report.py` then emails the report as an attachment.

### Database build pipeline (`Database/`)

The crawl/extraction pipeline is fully deterministic — no LLM calls:

- `discovery.py` — fetches the site's XML sitemap from `site_config.SITEMAP_URL` (Blackbaud sites often serve it at `/sitemap`, NOT `/sitemap.xml`), normalizes URLs (strips `/page/` duplicates, fragments, query strings) and applies the config's `EXCLUDED_URL_PATTERNS` (careers, fundraising, dated news/trips, robots-disallowed paths). Blackbaud sitemaps generate `<lastmod>` values at request time, making them useless for change detection.
- `extraction.py` — pure-function extraction chain over raw HTML: (1) BeautifulSoup selector extraction for the Blackbaud CMS markup (`div.page-row` regions, dropping `span1-7` promo sidebars and `.element-invisible` a11y stubs that produce "List of N items." junk) → (2) trafilatura (recall-favoring markdown). HTML→markdown via markdownify. Escalates when output < `MIN_CHARS`; the winning strategy is recorded in chunk metadata as `extractor`.
- `chunking.py` — heading-aware chunking: pages ≤2000 chars stay whole; larger pages split on `#`/`##`/`###` with small sections merged into neighbors; every chunk gets the contextual header described above.
- `pipeline.py` — `crawl_pages()` (browserless httpx fetch, 4 concurrent, retry/backoff; HTTP 4xx and Blackbaud soft-404s classified as `dead_url`) and `build_chunks()` (pages → chunks + per-page stats).
- `create_db.py` — one-click full rebuild: snapshot → crawl → build into a staging collection → validation gate (≥90% page success, key-page content checks, min chunk count) → embedding-preserving swap into the live collection → `rebuild_report.txt`. The live collection is never touched if validation fails.
- `update_db.py` — per-URL refresh on the same pipeline. Crawl-first semantics: existing chunks are only deleted after a successful re-crawl.
- `snapshot_db.py` — snapshot/rollback (used by `create_db.py` and the server's empty-DB auto-restore).
- `migrate_embedding_config.py` — one-time, idempotent rewrite of a collection's persisted embedding-function config from the old Google provider to `openrouter`. Edits `chroma.sqlite3` directly; stored vectors are untouched.
- Other scripts (`clear.py`, `query_chunks.py`, `delete_chunk.py`, `insert_content.py`, etc.) are one-off maintenance utilities.

## Testing

Tests live in `tests/` and use pytest with `unittest.mock`. The pattern for each test module:

1. Set dummy env vars (`OPENROUTER_API_KEY`, `CHROMA_DB_PATH`) before importing the module under test.
2. Patch `chatbot.get_chroma_db` so module-level DB initialisation doesn't hit real ChromaDB.
3. Test pure functions directly; mock LLM calls with `MagicMock` / `AsyncMock`.

`workflow_specs.py` and most of `workflow_engine.py` are pure and tested without mocks (`test_workflow_specs.py`, `test_workflow_engine.py`); the judge is injectable, so the whole state machine is driven without a provider. `test_workflow_endpoints.py` disables rate limiting via `server.limiter.enabled = False` — Flask-Limiter resolves `RATELIMIT_ENABLED` once at `init_app`, so setting the config key afterwards has no effect. Frontend logic follows the `chat_history_store.js` pattern: a DOM-free IIFE loaded in Node via `new Function("window", src)`, run with `node --test`.

`discovery.py`, `chunking.py`, and `extraction.py` are pure and tested without mocks; `tests/fixtures/*.html` are small hand-written Blackbaud-shaped pages for extraction tests. `test_create_db.py` / `test_update_db.py` use a shared `FakeCollection` stand-in instead of real ChromaDB. `tests/conftest.py` imports pyarrow first to pin Windows DLL load order — without it, collection crashes with an access violation when chromadb/grpc load before pyarrow.
