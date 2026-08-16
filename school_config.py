"""School configuration — edit this file to deploy the chatbot for your school.

Every school-specific value used by the Python backend lives here: the crawler
(Database/), the chat server (agent_chatbot/), and the analysis agent
(agent_analysis/). The frontend has its own sibling file, frontend/school_config.js.

The repo ships configured for a fictional "Example School" so everything runs
out of the box and the test suite is deterministic. Replace the values below
with your school's details.
"""

# --- Identity -------------------------------------------------------------

SCHOOL_NAME = "Example School"          # full name, used in prompts and canned replies
SCHOOL_SHORT_NAME = "Example"           # short form, used in report headers and the crawler UA

# --- Crawl / knowledge-base build (Database/) ------------------------------

SITE_ROOT = "https://www.example-school.org"

# Where your site serves its XML sitemap. Note: Blackbaud-hosted school sites
# often serve it at /sitemap (no .xml extension).
SITEMAP_URL = f"{SITE_ROOT}/sitemap.xml"

# Top-level paths your robots.txt disallows (anchored: "/app" won't match "/how-to-apply").
ROBOTS_DISALLOWED_PATHS = ["/api", "/app", "/calendar"]

# Regexes (matched against the lowercased URL) for pages that are off-topic for
# a prospective-family knowledge base: careers, donation forms, dated news,
# internal handbooks, photo galleries, etc.
EXCLUDED_URL_PATTERNS = [
    r"/careers",
    r"/donate",
    r"news-detail",
    r"/site-map$",
    r"\.pdf$",
]

# User-Agent header sent by the crawler.
USER_AGENT = f"Mozilla/5.0 ({SCHOOL_SHORT_NAME}AI knowledge-base refresh)"

# Regex that strips your site's suffix from page <title>s, e.g.
# "Tuition | Example School" -> "Tuition".
TITLE_SUFFIX_RE = rf"\s*[|\-–]\s*{SCHOOL_NAME}.*$"

# Rebuild validation gate: after a full crawl, each of these pages must contain
# the given needle word (case-insensitive) or the rebuild is aborted and the
# live collection left untouched. Pick 2-3 pages whose content must never
# silently vanish.
KEY_PAGE_CHECKS = {
    f"{SITE_ROOT}/admissions/tuition": "tuition",
    f"{SITE_ROOT}/admissions": "application",
}

# --- Chatbot persona and prompt (agent_chatbot/) ----------------------------

# Authoritative facts the assistant may state regardless of what retrieval
# returns. make_prompt injects them into the system prompt, and the
# faithfulness judge is handed the same text as valid grounding — without
# this, answers drawn from these facts would be mis-flagged as hallucinations.
SCHOOL_FACTS = """1. Founding year: 1901
2. Enrollment: 500 students
3. The student-teacher ratio is 10:1
4. 2026-2027 Tuition (All Grades) | $45,000
5. Example School is a co-educational K-12 day school."""

# Extra school-specific rules appended to the system prompt's generic rule
# list (numbered automatically). Use these for policies the model must never
# get wrong. Patterns that have worked well:
#   "Example School is co-educational; it admits all genders.",
#   "Never mention the Old Gym; the building is now called the Field House.",
#   "The current head of school is Alex Doe, appointed in 2020.",
#   "Applications for the 2027-2028 school year open on September 1, 2026.",
#     (time-sensitive rules like that last one need manual upkeep)
CUSTOM_PROMPT_RULES = [
    "Example School is a co-educational K-12 day school.",
]

# Credited in the prompt when users ask who built the assistant. Set to "" to omit.
DESIGNED_BY = "the students of the Example School coding club"

# --- Canned responses (single source of truth) ------------------------------
# server.py sends these verbatim, and dashboard_data.py matches DEFERRAL_MESSAGE
# to classify unanswered questions — keep them defined only here.

GREETING_MESSAGE = f"Hello! I am the {SCHOOL_NAME} AI Assistant, how can I help you?"

DEFERRAL_MESSAGE = (
    f"I specialize in information relevant to {SCHOOL_NAME}. "
    "Do you have a question about the school that I can assist you with?"
)

NO_INFO_MESSAGE = (
    "I don't have that specific information in my current files, "
    "but our Enrolment Team would love to answer this for you."
)

SPAM_GIBBERISH_MESSAGE = f"Please enter a valid question about {SCHOOL_NAME}."

CONTACT_EMAIL = "admissions@example-school.org"
