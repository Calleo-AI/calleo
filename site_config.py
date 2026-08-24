"""Site configuration — edit this file to deploy the chatbot for your site.

Every site-specific value used by the Python backend lives here: the crawler
(Database/), the chat server (agent_chatbot/), and the analysis agent
(agent_analysis/). The frontend has its own sibling file, frontend/site_config.js.

Calleo is not specific to any one kind of organization — a school, an NGO, a
non-profit, or any other site with public content all configure the same way.
The repo ships configured for a fictional "Example Site" so everything runs
out of the box and the test suite is deterministic. Replace the values below
with your own details.
"""

# --- Identity -------------------------------------------------------------

SITE_NAME = "Example Site"              # full name, used in prompts and canned replies
SITE_SHORT_NAME = "Example"             # short form, used in report headers and the crawler UA

# --- Crawl / knowledge-base build (Database/) ------------------------------

SITE_ROOT = "https://www.example-site.org"

# Where your site serves its XML sitemap. Note: Blackbaud-hosted sites often
# serve it at /sitemap (no .xml extension).
SITEMAP_URL = f"{SITE_ROOT}/sitemap.xml"

# Top-level paths your robots.txt disallows (anchored: "/app" won't match "/how-to-apply").
ROBOTS_DISALLOWED_PATHS = ["/api", "/app", "/calendar"]

# Regexes (matched against the lowercased URL) for pages that are off-topic for
# a public-facing knowledge base: careers, donation forms, dated news,
# internal handbooks, photo galleries, etc.
EXCLUDED_URL_PATTERNS = [
    r"/careers",
    r"/donate",
    r"news-detail",
    r"/site-map$",
    r"\.pdf$",
]

# User-Agent header sent by the crawler.
USER_AGENT = f"Mozilla/5.0 ({SITE_SHORT_NAME}AI knowledge-base refresh)"

# Regex that strips your site's suffix from page <title>s, e.g.
# "Contact Us | Example Site" -> "Contact Us".
TITLE_SUFFIX_RE = rf"\s*[|\-–]\s*{SITE_NAME}.*$"

# Rebuild validation gate: after a full crawl, each of these pages must contain
# the given needle word (case-insensitive) or the rebuild is aborted and the
# live collection left untouched. Pick 2-3 pages whose content must never
# silently vanish — whatever those are for your site (a school might pick
# tuition and admissions; an NGO might pick programs and how-to-give).
KEY_PAGE_CHECKS = {
    f"{SITE_ROOT}/about": "mission",
    f"{SITE_ROOT}/contact": "contact",
}

# --- Chatbot persona and prompt (agent_chatbot/) ----------------------------

# Authoritative facts the assistant may state regardless of what retrieval
# returns. make_prompt injects them into the system prompt, and the
# faithfulness judge is handed the same text as valid grounding — without
# this, answers drawn from these facts would be mis-flagged as hallucinations.
SITE_FACTS = """1. Founding year: 1901
2. Located at 100 Main Street, Springfield
3. Example Site serves roughly 500 people each year
4. Office hours are Monday to Friday, 9 AM to 5 PM
5. Example Site is a registered non-profit organization."""

# Extra site-specific rules appended to the system prompt's generic rule list
# (numbered automatically). Use these for policies the model must never get
# wrong. Patterns that have worked well:
#   "Example Site is open to the public; there is no membership requirement.",
#   "Never mention the Old Gym; the building is now called the Field House.",
#   "The current executive director is Alex Doe, appointed in 2020.",
#   "Applications for the 2027-2028 program year open on September 1, 2026.",
#     (time-sensitive rules like that last one need manual upkeep)
CUSTOM_PROMPT_RULES = [
    "Example Site is a registered non-profit serving the Springfield community.",
]

# Credited in the prompt when users ask who built the assistant. Set to "" to omit.
DESIGNED_BY = "the Example Site web team"

# --- Canned responses (single source of truth) ------------------------------
# server.py sends these verbatim, and dashboard_data.py matches DEFERRAL_MESSAGE
# to classify unanswered questions — keep them defined only here.

GREETING_MESSAGE = f"Hello! I am the {SITE_NAME} AI Assistant, how can I help you?"

DEFERRAL_MESSAGE = (
    f"I specialize in information relevant to {SITE_NAME}. "
    f"Do you have a question about {SITE_NAME} that I can assist you with?"
)

NO_INFO_MESSAGE = (
    "I don't have that specific information in my current files, "
    "but our team would love to answer this for you."
)

SPAM_GIBBERISH_MESSAGE = f"Please enter a valid question about {SITE_NAME}."

CONTACT_EMAIL = "info@example-site.org"

# --- Guided workflows (agent_chatbot/workflow_engine.py) --------------------
# A "workflow" is a guided questionnaire the assistant walks a visitor through:
# sections of questions, asked one at a time, with follow-ups when an answer is
# vague, and a filled-in document at the end. Each one is a JSON file in
# WORKFLOWS_DIR — see workflows/README.md for the format.

WORKFLOWS_ENABLED = True

# Where spec files live. Relative paths resolve against the repo root; the
# WORKFLOWS_DIR env var overrides this entirely (use it to keep specs outside
# the repo). Set WORKFLOWS_ENABLED = False to hide the feature without deleting
# the files.
WORKFLOWS_DIR = "workflows"

# Completed responses are written here as markdown. Relative to the repo root;
# WORKFLOW_RESPONSES_DIR in the environment overrides it. There is deliberately
# no HTTP route that reads this back — responses can contain personal details
# and the dashboard API has no authentication.
WORKFLOW_RESPONSES_DIR = "workflow_responses"

# Hard limits. These are safety rails, not tuning knobs: the workflow state
# blob round-trips through the client on every request, so it is untrusted
# input and every bound below is enforced server-side on arrival.
WORKFLOW_MAX_STATE_BYTES = 128 * 1024   # reject a blob larger than this
WORKFLOW_MAX_TURNS = 150                # force-complete a runaway interview
WORKFLOW_MAX_RAW_CHARS = 1500           # truncate a single stored answer
WORKFLOW_MAX_VALUE_CHARS = 500          # truncate a single normalized value

# Canned engine replies. Kept here for the same reason DEFERRAL_MESSAGE is:
# one source of truth that tests assert against. A spec's "messages" block
# overrides any of these for that workflow.
WORKFLOW_ERROR_MESSAGE = (
    "Something went wrong handling that answer, but I've kept your progress. "
    "Let's carry on."
)
WORKFLOW_SPEC_CHANGED_MESSAGE = (
    "This questionnaire was updated since you started. I've kept the answers "
    "that still apply and picked up from there."
)
WORKFLOW_SKIPPED_MESSAGE = "No problem — noted as skipped."
WORKFLOW_DONT_KNOW_MESSAGE = "That's fine, \"don't know\" is a useful answer too."
WORKFLOW_STOPPED_MESSAGE = (
    "Saved. You can pick this up again any time from where you left off."
)
WORKFLOW_COMPLETE_MESSAGE = (
    "That's everything — thank you. Here is the filled-in summary."
)

# Subject line for emailed responses. {title} is the workflow's title.
WORKFLOW_EMAIL_SUBJECT = f"[{SITE_SHORT_NAME}] Completed questionnaire: {{title}}"
