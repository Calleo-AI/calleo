"""Structural tests for the chatbot embed snippet.

frontend/embed-snippet.html is the code pasted into the site's CMS —
anything missing from it is missing from the live page. These tests pin
the hint popup block so it cannot silently drop out of the snippet again.
"""

from pathlib import Path

from bs4 import BeautifulSoup

SNIPPET_PATH = Path(__file__).parent.parent / "frontend" / "embed-snippet.html"
SNIPPET_HTML = SNIPPET_PATH.read_text(encoding="utf-8")
SOUP = BeautifulSoup(SNIPPET_HTML, "html.parser")


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
