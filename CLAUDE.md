# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt        # serving only (Flask server, snapshot restore)
pip install -r requirements-crawl.txt  # adds the crawl/rebuild pipeline (browserless —
                                       # httpx + trafilatura + markdownify, no playwright;
                                       # pypdf + python-docx for linked documents)
pip install -r requirements-dev.txt    # adds pytest (tests also need requirements-crawl)

# Run the Flask chat server (port 5000 by default)
cd agent_chatbot && python server.py

# Run the analysis agent (reads DB logs, writes a markdown report, emails it)
cd agent_analysis && python analysis_agent.py

# Rebuild the ChromaDB knowledge base from the live website (one click)
python Database/create_db.py
python Database/create_db.py --dry-run      # crawl + report only, no DB writes
python Database/create_db.py --max-pages 8  # smoke test on the first 8 sitemap seeds
python Database/create_db.py --prune        # also delete sources no longer crawled
python Database/create_db.py --no-links     # sitemap only, don't follow on-page links
python Database/create_db.py --max-depth 1 --max-link-pages 200
python Database/create_db.py --no-images --no-documents

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

**All site-specific values live in two config files.** `site_config.py` (repo root) holds every backend value — site identity, crawl targets and exclusions, prompt facts/rules, and canned response strings; `frontend/site_config.js` holds the widget's branding, translations, API base, and localStorage prefix. No organization name, URL, or policy should ever be hardcoded elsewhere; tests import the same constants. The canned `DEFERRAL_MESSAGE` is load-bearing: `server.py` sends it and `dashboard_data.py` matches it to classify unanswered questions.

Calleo is deliberately organization-agnostic: it targets schools, NGOs, and any other site with public content. Keep prompts, comments, and defaults neutral — say "site" or "organization", not "school". The repo ships configured for a fictional **Example Site** so the suite is deterministic.

**All LLM and embedding calls go through `llm_client.py` (repo root).** It is the single provider seam — the only module that imports the OpenAI SDK (pointed at OpenRouter) or constructs the Gemini embedding function, and the only place model ids and OpenRouter `extra_body` reasoning syntax appear. Use `llm_client.chat(messages, role=..., temperature=..., reasoning=...)` for completions and `llm_client.get_embedding_function()` for the shared Gemini embedding function. Roles map to env-overridable models: `chat`→Qwen (`CHAT_MODEL`), `analysis`/`judge`→DeepSeek (`ANALYSIS_MODEL`/`JUDGE_MODEL`), embeddings→`gemini-embedding-001` (`EMBED_MODEL`). `reasoning` is semantic: `None` omits the field, `"off"` disables reasoning, `"high"` sets high effort; `temperature=None` is omitted from the request. Both the chat client and the embedding function are lazy singletons, and the `openai` import is deferred into the client factory so embedding-only consumers (the DB build/refresh pipeline, one-off scripts) never import `openai` or need `OPENROUTER_API_KEY`. `chat()` raises on failure — each caller keeps its own fallback. **This module is the seam to rewrite for a future Vertex AI migration; no call site should need to change.**

**The chatbot iframe is `chatbot_iframe.html`**, which loads `chatbot.js` and `chatbot.css`. It posts to the `/chat` endpoint.

**ChromaDB collections in use by the server** (`agent_chatbot/server.py`):
- `full_database` — the main knowledge base queried on every non-greeting request
- `conversations` — legacy collection (no longer written to)
- `full_database_conversations` — logs `/chat` interactions; also the source for the analysis agent

**Chunk metadata convention:** every crawled chunk carries `{"source": url, "title": page_title, "extractor": strategy, "kind": ...}` plus `"section": breadcrumb` when the page was split on headings. Chunk text is prefixed with a contextual header (`Document: <title>` / `Source: <url>` / `Section: <breadcrumb>`) so retrieval never loses page context. Manual entries added via `insert_content.py` have only `{"source": ...}` and are preserved by rebuilds unless `--prune` is passed.

`kind` is one of three, and the choice of `source` for each is load-bearing:

| kind | id | `source` | extra metadata |
|---|---|---|---|
| `page` | `{url}_chunk_{j}` | the page URL | — |
| `document` | `{doc_url}_chunk_{j}` | **the document URL** — a PDF is itself the destination worth linking a user to | `extractor` is `pdf`/`docx` |
| `image` | `{page_url}_image_{sha1(image_url)[:10]}` | **the host page URL, never the image URL** | `image_url`, `image_alt` |

Keying images to their host page is what makes `swap_into_live()` and `update_db.py` work unchanged: a re-crawled page atomically replaces its own image chunks, and an answer's source link points at a readable page rather than a bare `.jpg`. Chunks with no `kind` key (legacy rows, manual entries) are treated as `page` everywhere — read it with `.get("kind", "page")`, never bare.

**Images are indexed as text, not pixels.** The embedding model is text-only, so `images.py` synthesizes a searchable body from alt text, `<figcaption>`, the nearest preceding heading and the cleaned filename, and writes the image URL into the chunk *body* as well as its metadata — the retrieval path hands raw document text to the model, so a URL that lives only in metadata can never be cited. An image with no human-written description is skipped rather than indexed empty; a filename alone never earns an embedding.

**`make_prompt` in `agent_chatbot/chatbot.py`** is the single function that builds the entire system prompt. It accepts `query`, `relevant_passage`, `history=[]`, and `language="English"`. The language instruction is injected just before the `ANSWER:` label so the model always responds in the user-selected language regardless of what language the user typed in.

**Language selector** is in the iframe header. The selected value is sent as `"language"` in every API request JSON body and passed through `server.py → make_prompt`. Welcome screen text translates immediately on change via `updateWelcomeText()` in `chatbot.js`, which holds translations for all 7 supported languages.

**Rate limiting + spam detection** in `server.py` runs before any LLM call:
1. Flask-Limiter (20 req/min) keyed on a SHA-256 client fingerprint (IP + User-Agent + Accept headers)
2. Custom `spam_tracker` catches gibberish (<50% alphanumeric), duplicate messages (same message ≥3× in 5 min), and rapid-fire (≥10 messages in 60 s)

### Analysis agent (`agent_analysis/`)

`analysis_agent.py` fetches all documents from `full_database_conversations`, parses `"User: ...\nAI: ..."` log format, sends the batch to Qwen for trend analysis, and writes a dated markdown report to `agent_analysis/analysis_reports/`. `email_report.py` then emails the report as an attachment.

### Database build pipeline (`Database/`)

The crawl/extraction pipeline is fully deterministic — no LLM calls:

- `discovery.py` — fetches the site's XML sitemap from `site_config.SITEMAP_URL` (Blackbaud sites often serve it at `/sitemap`, NOT `/sitemap.xml`), normalizes URLs (strips `/page/` duplicates, fragments, query strings) and applies the config's `EXCLUDED_URL_PATTERNS` (careers, fundraising, dated news/trips, robots-disallowed paths). Blackbaud sitemaps generate `<lastmod>` values at request time, making them useless for change detection. **`classify_url()` is the single gate** every URL passes through — sitemap seeds and harvested links alike — returning `page`, `document`, or `''`. `is_excluded()` is checked first and always wins, so re-adding `r"\.pdf$"` to `EXCLUDED_URL_PATTERNS` is still a hard ban; `CRAWL_DOCUMENTS = False` is the blanket opt-out.
- `links.py` — pure `<a href>` harvesting from raw HTML: resolves relative hrefs (honoring `<base href>`), drops `mailto:`/`tel:`/`javascript:`/fragment-only/`rel=nofollow`, keeps same-site links only, and canonicalizes scheme+host onto `SITE_ROOT` so `http://`, `https://`, www and bare forms all dedupe to one URL. Deliberately policy-free — exclusion is `classify_url`'s job. Note it reads raw HTML, *not* chrome-stripped HTML: nav menus are where links live.
- `frontier.py` — the BFS. Walks outward from the seeds to `CRAWL_MAX_DEPTH`, capped at `CRAWL_MAX_PAGES` link-discovered pages (seeds are exempt — the sitemap is authoritative and `--max-pages` already caps it). Documents are fetched but never expanded. `fetch` is injected, so the whole traversal unit-tests against a dict-backed fake with no network. **Link expansion costs no extra requests** — links come out of HTML already fetched.
- `extraction.py` — pure-function extraction chain over raw HTML: (1) BeautifulSoup selector extraction for the Blackbaud CMS markup (`div.page-row` regions, dropping `span1-7` promo sidebars and `.element-invisible` a11y stubs that produce "List of N items." junk) → (2) trafilatura (recall-favoring markdown). HTML→markdown via markdownify with `strip=["a","img"]` — links and images are harvested out-of-band by `links.py`/`images.py`, so putting them back here would only inject noise into every passage. `chrome_free_soup()` is the shared nav/footer/menu stripper, reused by `images.py`.
- `images.py` — `harvest()` → image records → `build_chunks()` → text chunks, per the "images are indexed as text" convention above. Runs on `chrome_free_soup()` output so header logos and menu sprites are gone before any deny pattern applies; further gates on `EXCLUDED_IMAGE_PATTERNS`, declared dimensions, alt stopwords and `IMAGE_MIN_ALT_CHARS`, capped by `IMAGE_MAX_PER_PAGE` and deduped site-wide on (image URL + description).
- `documents.py` — PDF (`pypdf`) and DOCX (`python-docx`) bytes → markdown, with DOCX heading styles mapped to `#`/`##`/`###` and multi-page PDFs marked `## Page N` so `chunking.py` still has real split points. Both imports are lazy, matching the trafilatura/markdownify pattern: the serving VM installs `requirements.txt` only and must never need them. A missing dependency raises `RuntimeError`, which `build_chunks` turns into one `extract_failed` document rather than a dead rebuild. No OCR — a scanned PDF yields no text and is reported `thin`, not indexed.
- `chunking.py` — heading-aware chunking: pages ≤2000 chars stay whole; larger pages split on `#`/`##`/`###` with small sections merged into neighbors; every chunk gets the contextual header described above.
- `pipeline.py` — `crawl_pages(urls, kinds=None)` (browserless httpx fetch, 4 concurrent, retry/backoff; HTTP 4xx and Blackbaud soft-404s classified as `dead_url`) and `build_chunks(pages, index_images=None)` (pages → chunks + per-page stats). Documents take a streamed fetch path capped at `DOCUMENT_MAX_BYTES` on both `Content-Length` and the accumulator, and pages reject non-HTML content types instead of decoding binary into a `str`. `kinds` is optional and inferred by extension when absent, so single-argument callers keep working and a stored `.pdf` source still refreshes correctly. Statuses: `ok | thin | dead_url | fetch_failed | too_large | unsupported_type | extract_failed`.
- `create_db.py` — one-click full rebuild: snapshot → crawl → build into a staging collection → validation gate → embedding-preserving swap into the live collection → `rebuild_report.txt`. The live collection is never touched if validation fails. **Every quality gate measures `content_chunks()` — page prose only.** Padding the total with hundreds of image chunks must never let a collapse in text extraction slip past the `MIN_TOTAL_CHUNKS` floor, and an image's alt text must never be what satisfies a key-page needle check. A separate `MAX_IMAGE_CHUNK_RATIO` gate fails the build if images become the bulk of it.
- `update_db.py` — per-URL refresh on the same pipeline. Crawl-first semantics: existing chunks are only deleted after a successful re-crawl. Refreshing is *source-atomic* — a page's image chunks are replaced along with its prose — so run it with the same `INDEX_IMAGES` setting the build used. It never discovers anything; new pages and newly linked documents arrive only via `create_db.py`'s link crawl.
- `snapshot_db.py` — snapshot/rollback (used by `create_db.py` and the server's empty-DB auto-restore).
- Other scripts (`clear.py`, `query_chunks.py`, `delete_chunk.py`, `insert_content.py`, etc.) are one-off maintenance utilities.

## Testing

Tests live in `tests/` and use pytest with `unittest.mock`. The pattern for each test module:

1. Set dummy env vars (`OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `CHROMA_DB_PATH`) before importing the module under test.
2. Patch `chatbot.get_chroma_db` so module-level DB initialisation doesn't hit real ChromaDB.
3. Test pure functions directly; mock LLM calls with `MagicMock` / `AsyncMock`.

`discovery.py`, `links.py`, `chunking.py`, `extraction.py`, and `images.py` are pure and tested without mocks; `tests/fixtures/*.html` are small hand-written Blackbaud-shaped pages (`gallery-with-images.html` drives the link and image tests). `test_frontier.py` drives the BFS with a dict-backed `FakeSite` and `asyncio.run` — the house pattern from `test_update_db.py`, which is why the suite needs no `pytest-asyncio`. `test_documents.py` builds DOCX files in memory with `python-docx` rather than committing binaries, and monkeypatches the PDF reader. `test_create_db.py` / `test_update_db.py` use a shared `FakeCollection` stand-in instead of real ChromaDB. `tests/conftest.py` imports pyarrow first to pin Windows DLL load order — without it, collection crashes with an access violation when chromadb/grpc load before pyarrow.
