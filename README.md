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

Guided workflows take a separate path — `/api/workflow/*` rather than `/chat` —
so a questionnaire answer is never spam-checked, retrieved against, logged to
the analytics collection, or scored for faithfulness. Voice input never reaches
the server at all: the browser transcribes it.

Every model call — chat, analysis, faithfulness judging, guided-workflow answer
judging, and the embeddings behind the vector search — goes to OpenRouter
through `llm_client.py`. One provider, one API key.

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
| `llm_client.py` | Single provider seam — every LLM + embedding call, all via OpenRouter |
| `agent_chatbot/workflow_engine.py` | Guided-questionnaire state machine (deterministic; the LLM only judges answers) |
| `agent_chatbot/workflow_specs.py` | Workflow spec loading and validation |
| `workflows/` | **Edit me** — one JSON file per guided questionnaire |
| `frontend/voice_input.js` | Browser-native speech-to-text for the chat widget |
| `frontend/workflow_client.js` | Client half of the guided-workflow turn loop |
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
| `WORKFLOWS_ENABLED` / `WORKFLOWS_DIR` | Guided questionnaires — see [`workflows/README.md`](workflows/README.md) |
| `WORKFLOW_RESPONSES_DIR` | Where completed questionnaire responses are written |

### `frontend/site_config.js` (chat widget)

| Field | What it controls |
|---|---|
| `siteName` | Welcome text (`{site}` placeholder in translations) |
| `contactEmail` | Contact banner + welcome screen |
| `apiBase` | Chat server origin (`""` = same origin) |
| `storagePrefix` | localStorage namespace |
| `welcomeTranslations` | Welcome text in all 7 languages |
| `speechLangs` | BCP-47 tag per language for voice input |
| `voiceTranslations` | Microphone button labels and error messages |
| `workflowTranslations` | Guided-workflow button labels and progress format |

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

## Voice input

The widget has a microphone button next to the composer. Speech is transcribed
by the browser's own Web Speech API — no server round trip, no second provider,
no extra API key, and nothing to configure. Dictated text lands in the input box
for the user to check and correct before sending, because recognition mishears
often enough that auto-sending would ship errors nobody got to catch.

| Browser | Support |
|---|---|
| Chrome (desktop, Android), Edge | Yes |
| Safari (macOS, iOS 14.5+) | Yes |
| Firefox | No Web Speech API — the button is never rendered, typing is unaffected |

Recognition follows the widget's language selector, mapped to BCP-47 tags in
`frontend/site_config.js` (`speechLangs`). Adjust the regional variants there —
`pt-PT` vs `pt-BR`, `ar-SA` vs `ar-EG` — to match your audience.

**If you embed the widget, re-paste `frontend/embed-snippet.html`.** The iframe
needs `allow="clipboard-write; microphone"`; browsers block speech recognition
in a cross-origin iframe unless the embedding page delegates the permission, and
the failure is silent until someone taps the mic on your live site.

Privacy: Chrome sends the audio to Google's servers for recognition; Safari
recognizes on-device. Neither path touches your backend. The widget discloses
this once, the first time a visitor starts dictation.

## Guided workflows

The bot can also *ask* the questions. A workflow is a questionnaire it walks a
visitor through — sections asked one at a time, follow-ups when an answer is
vague or an unsupported number, skip and "don't know" always available, pause
and resume across sittings, and a filled-in document at the end with Copy,
Download and Email buttons.

Each one is a single JSON file in `workflows/`. Two ship with the repo: a short
`contact_intake` and a full `process_interview`. Delete them, edit them, or add
your own — **[`workflows/README.md`](workflows/README.md) documents the format**.

Sequencing is deterministic Python; the model is called at most once per answer,
to judge sufficiency and normalize the answer into typed fields. The final
document is then pure string substitution, which is what makes "never invent a
number" structurally true rather than a request in a prompt — a value can only
appear if the participant actually said it. A model outage cannot strand anyone
mid-interview: an unparseable judgement accepts the answer and moves on.

Workflow state lives in the visitor's browser and travels with each request, so
the server stays stateless and multi-worker safe.

Completed responses are written to `WORKFLOW_RESPONSES_DIR` (default
`workflow_responses/`, gitignored) and can be emailed using the existing SMTP
config. There is deliberately no HTTP route that reads them back: they can
contain personal details and the dashboard API has no authentication.

Set `WORKFLOWS_ENABLED = False` in `site_config.py` to hide the feature. With no
specs configured the launcher renders nothing, so the widget looks exactly as it
did before.

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
python Database/migrate_embedding_config.py  # one-time upgrade for a pre-OpenRouter DB
pytest tests/                                # run the test suite
```

## Upgrading a database built before the OpenRouter migration

Embeddings used to come from the Google API directly. ChromaDB records the
embedding provider inside each collection, so a database built back then
refuses to open now:

```
ValueError: An embedding function already exists in the collection
configuration ... new: openrouter vs persisted: google_generative_ai
```

Rewrite the recorded provider once. The stored vectors come from the same
`gemini-embedding-001` model and are kept as-is — nothing is re-embedded:

```bash
python Database/migrate_embedding_config.py --dry-run   # preview
python Database/migrate_embedding_config.py
```

The script is idempotent, so a fresh database (or one already migrated) is a
no-op. Run it before anything else that touches the database: a full
`python Database/create_db.py` rebuild is not a way around it, since the rebuild
opens the live collection and hits the same error. Once migrated, everything
works; a rebuild afterwards is still worth doing eventually, because queries now
reach the model through OpenRouter rather than Google's `task_type`-annotated
API.

## Environment variables

Everything lives in `.env` (see `.env.example`): `OPENROUTER_API_KEY` is the
only required key — chat, analysis, and embeddings (`google/gemini-embedding-001`)
all go through OpenRouter; `CHROMA_DB_PATH` should point outside the repo; email
vars are optional; `CHAT_MODEL` / `ANALYSIS_MODEL` / `JUDGE_MODEL` /
`EMBED_MODEL` override the default models per role — every LLM call goes
through `llm_client.py`, so switching providers is a one-file change.

## Testing

```bash
pytest tests/                                  # 496 tests, no network needed
node --test 'tests/frontend/test_*.mjs'        # 70 widget tests
```

Tests assert against the shipped Example Site config; if you change
`site_config.py`, a few config-reflecting assertions will reflect your values.

## Contributing

PRs welcome. Run `pytest tests/` before submitting. Please keep
organization-specific values out of code — they belong in the config files.

## License

[MIT](LICENSE)
