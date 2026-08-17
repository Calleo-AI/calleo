"""Structural tests for the chatbot embed snippet.

frontend/embed-snippet.html is the code pasted into the site's CMS —
anything missing from it is missing from the live page. These tests pin
the hint popup block so it cannot silently drop out of the snippet again.
"""

import re
from pathlib import Path

from bs4 import BeautifulSoup

FRONTEND = Path(__file__).parent.parent / "frontend"
SNIPPET_PATH = FRONTEND / "embed-snippet.html"
SNIPPET_HTML = SNIPPET_PATH.read_text(encoding="utf-8")
SOUP = BeautifulSoup(SNIPPET_HTML, "html.parser")

WIDGET_CSS = (FRONTEND / "chatbot.css").read_text(encoding="utf-8")
WIDGET_JS = (FRONTEND / "chatbot.js").read_text(encoding="utf-8")


def test_widget_iframe_present():
    iframe = SOUP.find("iframe", id="site-chatbot-iframe")
    assert iframe is not None
    # Ships with the EDIT ME placeholder host; deployers point it at theirs.
    assert iframe["src"] == "https://YOUR-CHATBOT-HOST.example/chatbot_iframe.html"
    styles = "\n".join(tag.get_text() for tag in SOUP.find_all("style"))
    # hint popup (z-index 10001) must stack above the widget iframe
    assert "z-index: 10000" in styles


def test_hint_popup_markup_present():
    popup = SOUP.find(id="chatbot-hint-popup")
    assert popup is not None
    assert "Have a question? Try our chatbot!" in popup.get_text()
    assert popup.find("button", id="chatbot-hint-close") is not None
    assert popup.find(id="chatbot-hint-arrow") is not None


def test_hint_popup_styles_present():
    styles = "\n".join(tag.get_text() for tag in SOUP.find_all("style"))
    assert "#chatbot-hint-popup" in styles
    assert "@keyframes chatbotHintBounce" in styles
    # must sit above the widget iframe, which uses z-index 10000
    assert "z-index: 10001" in styles


def test_hint_popup_dismiss_script_present():
    scripts = "\n".join(tag.get_text() for tag in SOUP.find_all("script"))
    assert "chatbot-hint-close" in scripts
    assert "chatbot-hint-hidden" in scripts
    # auto-dismiss when chatbot.js reports the chat window was opened
    assert "event.data.type === 'toggle'" in scripts
    assert "event.data.showing" in scripts


# --------------------------------------------------------------------------
# History sidebar alignment contract
#
# Three files each hold a piece of the same geometry and none of them can
# import from the others (the snippet is pasted into a CMS; the CSS and JS are
# separate assets). When the numbers drift apart the sidebar stops sitting
# flush against the chat window — which is exactly what happened: the snippet
# widened the iframe by 200px, the sidebar claimed only 200 of them, and the
# chat panel's 25px left inset was left over as a visible transparent gap.
# These tests fail loudly if any one of the three is changed alone.
# --------------------------------------------------------------------------

SIDEBAR_REVEAL_PX = 200
PANEL_INSET_LEFT_PX = 25


def test_snippet_widens_iframe_by_the_sidebar_reveal():
    scripts = "\n".join(tag.get_text() for tag in SOUP.find_all("script"))
    assert f"iframe.style.width = (curW + {SIDEBAR_REVEAL_PX}) + 'px'" in scripts
    assert f"curW - {SIDEBAR_REVEAL_PX}" in scripts
    # ...and shifts the iframe's left edge out by the same amount, so the
    # right-pinned chat panel does not move.
    assert f"curLeft - {SIDEBAR_REVEAL_PX}" in scripts
    assert f"curLeft + {SIDEBAR_REVEAL_PX}" in scripts


def test_css_reveal_matches_the_snippet():
    assert f"--sidebar-reveal: {SIDEBAR_REVEAL_PX}px;" in WIDGET_CSS
    assert f"--panel-inset-left: {PANEL_INSET_LEFT_PX}px;" in WIDGET_CSS


def test_js_reveal_matches_the_snippet():
    assert re.search(
        rf"const SIDEBAR_REVEAL = {SIDEBAR_REVEAL_PX};", WIDGET_JS
    ), "chatbot.js SIDEBAR_REVEAL must match the snippet's iframe widening"


def test_sidebar_covers_the_reveal_plus_the_panel_inset():
    # The panel's left edge sits --panel-inset-left further in than the
    # iframe's, so a sidebar only as wide as the reveal leaves that inset as a
    # transparent gap at the seam. It has to cover both.
    assert (
        "width: calc(var(--sidebar-reveal) + var(--panel-inset-left));" in WIDGET_CSS
    )


def test_panel_width_is_identical_open_and_closed():
    # Both states subtract the same 30px of panel inset; the open state
    # additionally gives back the reveal, which belongs entirely to the
    # sidebar. Two different inset figures here is what caused the 25px gap
    # and the accompanying width snap.
    assert (
        "window.innerWidth - 30 - (sidebarOpen ? SIDEBAR_REVEAL : 0)" in WIDGET_JS
    )


def test_desktop_sidebar_has_no_slide_of_its_own():
    # On desktop the reveal IS the animation: the sidebar rides the iframe's
    # moving left edge. A transform here would compose with that motion (same
    # duration and easing, opposite direction) and cancel it out.
    sidebar_rule = WIDGET_CSS.split(".chat-sidebar {", 1)[1].split("}", 1)[0]
    assert "transform" not in sidebar_rule
    # Mobile is a full-screen overlay the parent never widens, so it keeps one.
    assert "body.mobile.sidebar-open .chat-sidebar {" in WIDGET_CSS
    mobile_rule = WIDGET_CSS.split("body.mobile.sidebar-open .chat-sidebar {", 1)[1]
    assert "transform: translateX(0);" in mobile_rule.split("}", 1)[0]
