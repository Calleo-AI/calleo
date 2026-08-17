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
# internal handbooks, photo galleries, etc. Applied to linked documents too.
# Note: file types are NOT excluded here — documents are classified by
# DOCUMENT_EXTENSIONS below and anything else is dropped automatically.
EXCLUDED_URL_PATTERNS = [
    r"/careers",
    r"/donate",
    r"news-detail",
    r"/site-map$",
]

# --- Link following --------------------------------------------------------
# The sitemap is only a seed list; the crawler also follows <a href> links found
# on the pages it fetches, so pages the sitemap omits still make it in. Link
# expansion costs no extra requests — links come out of HTML already fetched.

CRAWL_FOLLOW_LINKS = True   # set False to crawl the sitemap and nothing else
CRAWL_MAX_DEPTH = 2         # link hops away from a sitemap URL (0 = sitemap only)
CRAWL_MAX_PAGES = 1500      # ceiling on link-DISCOVERED pages (0 = unlimited).
                            # Sitemap seeds are never capped here — use --max-pages.

# --- Linked documents ------------------------------------------------------
# Linked files with these extensions are fetched and their text indexed like any
# other page (handbooks, fee schedules, forms). Requires requirements-crawl.txt.

CRAWL_DOCUMENTS = True
DOCUMENT_EXTENSIONS = (".pdf", ".docx")
DOCUMENT_MAX_BYTES = 20 * 1024 * 1024   # skip anything larger

# --- Images ----------------------------------------------------------------
# Images are indexed as TEXT records built from alt text, captions, the nearest
# heading and the filename — the embedding model is text-only, so an image with
# no descriptive text anywhere is unindexable and is skipped. The image URL is
# stored in chunk metadata AND in the chunk body so the assistant can cite it.

INDEX_IMAGES = True
IMAGE_MIN_ALT_CHARS = 15     # minimum descriptive text before an image is indexed
IMAGE_MAX_PER_PAGE = 25      # cap per page; keeps embedding cost predictable

# Regexes (matched against the lowercased image URL) for decorative assets that
# carry no information: site chrome, icons, tracking pixels, layout spacers.
EXCLUDED_IMAGE_PATTERNS = [
    r"logo",
    r"favicon",
    r"/icons?/",
    r"spacer",
    r"pixel",
    r"placeholder",
    r"\.svg(\?|$)",
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
