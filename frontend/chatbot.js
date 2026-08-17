// All site-specific values (name, contact email, API host, translations)
// come from frontend/site_config.js, which must be loaded first.
const SITE_CONFIG = window.SITE_CONFIG;
if (!SITE_CONFIG) {
    throw new Error("site_config.js must be loaded before chatbot.js — see chatbot_iframe.html");
}
const CONTACT_EMAIL = SITE_CONFIG.contactEmail;
const STORAGE_PREFIX = SITE_CONFIG.storagePrefix || "site_chatbot";

const chatbotToggler = document.querySelector(".chatbot-toggler");
const chatbot = document.querySelector(".chatbot");

// Attention-grabbing glow: pulse INTENSELY three times on load, then settle
// to the normal icon. Also cancelled the moment the user first opens the chat.
(function startTogglerGlow() {
    if (!chatbotToggler) return;
    chatbotToggler.classList.add("glowing");
    // Remove the class once the 3-iteration animation finishes, so the toggler
    // returns cleanly to its resting box-shadow.
    let settled = false;
    function settle() {
        if (settled) return;
        settled = true;
        chatbotToggler.classList.remove("glowing");
        chatbotToggler.removeEventListener("animationend", onEnd);
    }
    function onEnd(e) {
        if (e.animationName === "chatTogglerGlow") settle();
    }
    chatbotToggler.addEventListener("animationend", onEnd);
    // Expose a canceller so the click handler can stop the glow early.
    window.__cancelTogglerGlow = settle;
})();
const chatInput = document.querySelector(".chat-input textarea");
const sendChatBtn = document.querySelector("#send-btn");
const chatbox = document.querySelector(".chatbox");
const closeBtn = document.querySelector(".close-btn");
const newChatBtn = document.querySelector(".new-chat-btn");
const langSelect = document.getElementById("lang-select");

const historyBtn  = document.querySelector(".history-btn");
const sidebar     = document.querySelector(".chat-sidebar");
const sidebarList = document.querySelector(".chat-sidebar-list");
const sidebarEmpty = document.querySelector(".chat-sidebar-empty");
const sidebarClear         = document.querySelector(".chat-sidebar-clear");
const sidebarConfirm       = document.getElementById("chat-sidebar-confirm");
const sidebarConfirmYes    = sidebarConfirm?.querySelector(".chat-sidebar-confirm-yes");
const sidebarConfirmCancel = sidebarConfirm?.querySelector(".chat-sidebar-confirm-cancel");
const chatbotEl   = document.querySelector(".chatbot");
const scrim       = document.querySelector(".chat-scrim");

let activeChatId = null;          // id of the chat currently shown
let sidebarOpen  = false;         // UI state

let userMessage = null;
let chatHistory = []; // Store conversation history
const welcomeScreen = document.getElementById('welcome-screen');
const contactBanner = document.getElementById('contact-banner');
const contactBannerClose = document.getElementById('contact-banner-close');
let contactBannerShown = false;

const welcomeTranslations = {};
for (const [lang, t] of Object.entries(SITE_CONFIG.welcomeTranslations)) {
    welcomeTranslations[lang] = {
        ...t,
        intro: t.intro.replace("{site}", SITE_CONFIG.siteName),
    };
}

const updateBannerText = (lang) => {
    const bannerTextEl = document.getElementById('contact-banner-text');
    if (!bannerTextEl) return;
    const t = welcomeTranslations[lang] || welcomeTranslations['English'];
    bannerTextEl.innerHTML = `${t.contactPre} <a href="mailto:${CONTACT_EMAIL}" class="contact-email">${CONTACT_EMAIL}</a> ${t.contactPost}`;
};

const updateDisclaimerText = (lang) => {
    const disclaimerEl = document.getElementById('chat-disclaimer');
    if (!disclaimerEl) return;
    const t = welcomeTranslations[lang] || welcomeTranslations['English'];
    disclaimerEl.textContent = t.disclaimer;
};

const updateWelcomeText = (lang) => {
    const welcomeTextEl = document.querySelector('.welcome-text');
    const welcomeContactEl = document.querySelector('.welcome-contact');
    if (!welcomeTextEl) return;
    const t = welcomeTranslations[lang] || welcomeTranslations['English'];
    welcomeTextEl.innerHTML = `${t.greeting} <span class="wave-emoji">👋</span><br>${t.intro}<br><span class="welcome-tagline">${t.question}</span>`;
    if (welcomeContactEl) {
        welcomeContactEl.innerHTML = `${t.contactPre} <a href="mailto:${CONTACT_EMAIL}" class="contact-email">${CONTACT_EMAIL}</a> ${t.contactPost}`;
    }
    const waveEmoji = welcomeTextEl.querySelector('.wave-emoji');
    if (waveEmoji) {
        waveEmoji.classList.remove('waving');
        void waveEmoji.offsetWidth;
        waveEmoji.classList.add('waving');
    }
};

const formatChatTime = (ms) => {
    const d = new Date(ms);
    const opts = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
    return d.toLocaleString(undefined, opts);
};

const renderSidebar = () => {
    if (!sidebarList) return;
    const chats = ChatHistoryStore.list();
    sidebarList.innerHTML = "";

    if (chats.length === 0) {
        sidebarEmpty.style.display = "";
        sidebarList.style.display = "none";
        sidebarClear.style.display = "none";
        return;
    }

    sidebarEmpty.style.display = "none";
    sidebarList.style.display = "";
    sidebarClear.style.display = "";

    chats.forEach(chat => {
        const li = document.createElement("li");
        li.className = "chat-sidebar-item";
        if (chat.id === activeChatId) li.classList.add("active");
        li.dataset.chatId = chat.id;

        const titleDiv = document.createElement("div");
        titleDiv.className = "chat-sidebar-item-title";
        titleDiv.textContent = chat.title || "…";

        const timeDiv = document.createElement("div");
        timeDiv.className = "chat-sidebar-item-time";
        timeDiv.textContent = formatChatTime(chat.updatedAt);

        const delBtn = document.createElement("button");
        delBtn.className = "chat-sidebar-delete";
        delBtn.type = "button";
        delBtn.setAttribute("aria-label", "Delete chat");
        delBtn.textContent = "×";

        li.appendChild(titleDiv);
        li.appendChild(timeDiv);
        li.appendChild(delBtn);
        sidebarList.appendChild(li);
    });
};

// Px the parent page widens the iframe on the LEFT when the sidebar opens.
// Must match --sidebar-reveal in chatbot.css and the +/-200 in
// embed-snippet.html (which is pasted into a CMS and can't share the constant).
const SIDEBAR_REVEAL = 200;

// While true, syncChatWidth() holds --chat-w at its current value so the chat
// panel (and everything inside it) keeps a constant size while the sidebar
// opens or closes and the parent page is mid-resizing the iframe. Without this
// a recompute mid-transition would read a half-widened window.innerWidth and
// briefly shrink the panel.
let sidebarAnimating = false;
let sidebarAnimTimer = null;

const setSidebarOpen = (open) => {
    sidebarOpen = open;
    if (!chatbotEl || !sidebar) return;
    // Ask the parent page to widen the iframe by SIDEBAR_REVEAL on the LEFT so
    // the sidebar has space to be revealed into. The chat panel itself never
    // moves or resizes: it's pinned to the iframe's right edge with a fixed
    // width (--chat-w) that is the same number open or closed, and --chat-w is
    // frozen for the duration so nothing recomputes mid-transition. The sidebar
    // rides the iframe's moving left edge and is wiped into view from behind
    // the panel, like a card emerging from behind it.
    // Sent BEFORE toggling the class so the parent begins its 0.35s iframe
    // resize in the same frame the panel's corners start flattening — desyncing
    // the two by even one frame shows up as visible jitter at the boundary.
    window.parent.postMessage({ type: "sidebar-toggle", open }, "*");
    if (open) {
        sidebar.hidden = false;
        // The scrim is display:none while closed so it can never swallow a tap
        // meant for the panel — on desktop it stays hidden throughout.
        if (scrim) scrim.hidden = false;
        void sidebar.offsetHeight;
        // Toggle on <body>, NOT .chatbot: the sidebar is a sibling of the panel
        // and .chatbot's transform would clip a fixed child. On desktop the
        // class only flattens the panel's left corners; on mobile — where the
        // parent never widens the iframe — it also drives the drawer's slide
        // and fades the scrim in.
        // Deferred to the next frame so that slide starts in lockstep with the
        // parent's iframe resize (same duration/easing).
        requestAnimationFrame(() => {
            if (sidebarOpen) document.body.classList.add("sidebar-open");
        });
    } else {
        document.body.classList.remove("sidebar-open");
        setTimeout(() => {
            if (sidebarOpen) return;
            sidebar.hidden = true;
            if (scrim) scrim.hidden = true;
        }, 370);
    }

    sidebarAnimating = true;
    if (sidebarAnimTimer) clearTimeout(sidebarAnimTimer);
    sidebarAnimTimer = setTimeout(() => {
        sidebarAnimating = false;
        sidebarAnimTimer = null;
        syncChatWidth();
    }, 370);
};

/* Keep the --chat-w CSS variable in sync with the chat panel's intended width.
   The panel is pinned to the iframe's right edge, so its width is the iframe
   viewport minus its own 25px + 5px insets — and, when the sidebar is open,
   minus the SIDEBAR_REVEAL px the parent added on the left purely for the
   sidebar. Both branches subtract the same insets, so the result is the SAME
   number open or closed: the panel never resizes or reflows when history is
   toggled, and its left edge lands exactly on the sidebar's right edge (the
   sidebar is SIDEBAR_REVEAL + 25px wide, so it covers the reveal and the inset
   between them). While a sidebar animation is in flight we skip updates
   entirely — window.innerWidth is mid-transition and would read short. */
const syncChatWidth = () => {
    if (!chatbotEl) return;
    if (sidebarAnimating) return;
    let panelW = window.innerWidth - 30 - (sidebarOpen ? SIDEBAR_REVEAL : 0);
    // Clamp to a sane minimum so an over-narrow window can't collapse content.
    if (panelW < 280) panelW = 280;
    chatbotEl.style.setProperty("--chat-w", panelW + "px");
};

const startBlankChat = () => {
    chatbox.innerHTML = "";
    chatbox.style.display = "none";
    showWelcome();
    chatHistory = [];
    chatInput.value = "";
    chatInput.style.height = "38px";
    sendChatBtn.classList.remove("active");
};

const formatMessage = (text) => {
    // 0. Extract source line before processing to avoid regex conflicts
    let sourceBubbleHtml = '';
    const sourceMatch = text.match(/\n\nSource:\s+(https?:\/\/\S+)/);
    if (sourceMatch) {
        const rawUrl = sourceMatch[1];
        const safeUrl = rawUrl.replace(/&/g, '&amp;').replace(/"/g, '&quot;');

        // Derive a human-readable display name from the URL
        let displayName = 'Source';
        try {
            const urlObj = new URL(rawUrl);
            const pathSegments = urlObj.pathname.split('/').filter(s => s.trim());
            if (pathSegments.length > 0) {
                const lastSegment = pathSegments[pathSegments.length - 1];
                // Replace hyphens and underscores with spaces, then title-case each word
                displayName = lastSegment
                    .replace(/[-_]+/g, ' ')
                    .replace(/\b\w/g, c => c.toUpperCase());
            } else {
                // Fallback to domain name when there is no path
                displayName = urlObj.hostname.replace(/^www\./, '');
            }
        } catch {
            const domainMatch = rawUrl.match(/https?:\/\/(?:www\.)?([^\/\s?#]+)/);
            displayName = domainMatch ? domainMatch[1] : 'Source';
        }

        // Truncate long names
        if (displayName.length > 40) {
            displayName = displayName.slice(0, 37) + '...';
        }

        sourceBubbleHtml = `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="source-bubble"><svg width="9" height="9" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M10 2L2 10M10 2H5M10 2V7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>${displayName}</a>`;
        text = text.replace(/\n\nSource:\s+https?:\/\/\S+/, '');
    }

    // 1. Escape HTML to prevent XSS (must happen first)
    let safeText = text.replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");

    // 2. Parse markdown - block level elements (headers, lists)
    const lines = safeText.split('\n');
    const processedLines = [];
    let inUnorderedList = false;
    let inOrderedList = false;

    for (let i = 0; i < lines.length; i++) {
        let line = lines[i];

        // Check for headers (## text) - convert to h3 as specified
        const headerMatch = line.match(/^(#{1,6})\s+(.+)$/);
        if (headerMatch) {
            if (inUnorderedList) { processedLines.push('</ul>'); inUnorderedList = false; }
            if (inOrderedList) { processedLines.push('</ol>'); inOrderedList = false; }
            processedLines.push(`<h3>${headerMatch[2]}</h3>`);
            continue;
        }

        // Check for unordered list items (- item or * item at start of line)
        const unorderedMatch = line.match(/^[-*]\s+(.+)$/);
        if (unorderedMatch) {
            if (inOrderedList) { processedLines.push('</ol>'); inOrderedList = false; }
            if (!inUnorderedList) { processedLines.push('<ul>'); inUnorderedList = true; }
            processedLines.push(`<li>${unorderedMatch[1]}</li>`);
            continue;
        }

        // Check for ordered list items (1. item, 2. item, etc.)
        const orderedMatch = line.match(/^\d+\.\s+(.+)$/);
        if (orderedMatch) {
            if (inUnorderedList) { processedLines.push('</ul>'); inUnorderedList = false; }
            if (!inOrderedList) { processedLines.push('<ol>'); inOrderedList = true; }
            processedLines.push(`<li>${orderedMatch[1]}</li>`);
            continue;
        }

        // Regular line - close any open lists
        if (inUnorderedList) { processedLines.push('</ul>'); inUnorderedList = false; }
        if (inOrderedList) { processedLines.push('</ol>'); inOrderedList = false; }
        processedLines.push(line);
    }

    // Close any remaining open lists
    if (inUnorderedList) processedLines.push('</ul>');
    if (inOrderedList) processedLines.push('</ol>');

    safeText = processedLines.join('\n');

    // 3. Parse markdown - inline elements
    // Bold (**text**) - must be parsed before italics
    safeText = safeText.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italics (*text*) - single asterisks around text
    safeText = safeText.replace(/\*([^*\n]+?)\*/g, '<em>$1</em>');

    // 4. Linkify URLs (after markdown to avoid conflicts)
    safeText = safeText.replace(
        /(https?:\/\/[^\s<]+)/g,
        '<a href="$1" target="_blank" style="color: #006e4f; text-decoration: underline; max-width: 100%; display: inline-block; overflow: hidden; text-overflow: ellipsis; vertical-align: bottom; white-space: nowrap;">$1</a>'
    );

    // 5. Handle newlines
    safeText = safeText.replace(/\n/g, "<br>");
    // Clean up <br> after/before block elements
    safeText = safeText.replace(/<\/h3><br>/g, '</h3>');
    safeText = safeText.replace(/<\/li><br>/g, '</li>');
    safeText = safeText.replace(/<\/ul><br>/g, '</ul>');
    safeText = safeText.replace(/<\/ol><br>/g, '</ol>');
    safeText = safeText.replace(/<ul><br>/g, '<ul>');
    safeText = safeText.replace(/<ol><br>/g, '<ol>');

    // Append source bubble if present
    if (sourceBubbleHtml) {
        safeText += '<div class="source-line">' + sourceBubbleHtml + '</div>';
    }

    return safeText;
};

const renderChatIntoBox = (messages) => {
    chatbox.innerHTML = "";
    chatbox.style.display = "";
    messages.forEach(m => {
        if (m.role === "user") {
            chatbox.appendChild(createChatLi(m.content, "outgoing"));
        } else {
            const li = createChatLi("", "incoming");
            li.querySelector("p").innerHTML = formatMessage(m.content);
            chatbox.appendChild(li);
        }
    });
};

const loadChat = (chatId) => {
    const chat = ChatHistoryStore.get(chatId);
    if (!chat) return;
    activeChatId = chat.id;
    ChatHistoryStore.setActiveId(chat.id);
    chatHistory = chat.history.slice();
    welcomeScreen.classList.add("hidden");
    renderChatIntoBox(chat.history);
    renderSidebar();
};

const showWelcome = () => {
    if (!welcomeScreen) return;
    updateWelcomeText(langSelect ? langSelect.value : 'English');
    welcomeScreen.classList.remove('hidden');
    welcomeScreen.classList.remove('fade-in');
    void welcomeScreen.offsetWidth; // force reflow to restart animation
    welcomeScreen.classList.add('fade-in');
};


// after
const API_URL = (SITE_CONFIG.apiBase || "") + "/chat";

const SESSION_ID = localStorage.getItem(`${STORAGE_PREFIX}_session_id`) || crypto.randomUUID();
localStorage.setItem(`${STORAGE_PREFIX}_session_id`, SESSION_ID);

const TITLE_URL = API_URL.replace(/\/chat$/, "/generate-title");

const fetchTitleForChat = (chatId, message, language) => {
    fetch(TITLE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, language }),
    })
    .then(async r => {
        if (!r.ok) {
            console.warn(`[chat-title] ${r.status} from ${TITLE_URL}`);
            return null;
        }
        return r.json();
    })
    .then(data => {
        if (data && data.title) {
            ChatHistoryStore.setTitle(chatId, data.title);
        } else if (data) {
            console.warn("[chat-title] response missing 'title' field:", data);
        }
    })
    .catch(err => {
        console.warn("[chat-title] fetch failed:", err.message);
    });
};

const createChatLi = (message, className) => {
    // Create a chat <li> element with passed message and className
    const chatLi = document.createElement("li");
    chatLi.classList.add("chat", className);
    let chatContent = `<p></p>`;
    chatLi.innerHTML = chatContent;
    chatLi.querySelector("p").textContent = message;
    return chatLi;
}

const startThinkingAnimation = (messageElement) => {
    messageElement.innerHTML = '<span class="thinking-animation"><span class="dot"></span><span class="dot"></span><span class="dot"></span></span>';
}



const generateResponse = async (incomingChatLi) => {
    const messageElement = incomingChatLi.querySelector("p");

    try {
        // Send POST request to your Python Server
        const response = await fetch(API_URL, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message: userMessage,
                session_id: SESSION_ID,
                history: chatHistory, // Send history to server
                language: langSelect ? langSelect.value : "English"
            })
        });

        const data = await response.json();

        // Handle Rate Limit specifically
        if (response.status === 429) {
            throw new Error("You have sent too many messages recently. Please wait a minute before trying again.");
        }

        if (!response.ok) throw new Error(data.response || data.error || `Server Error (${response.status})`);

        // Update the "Thinking..." text with the real answer
        messageElement.innerHTML = formatMessage(data.response);

        // Trigger top-to-bottom reveal animation on the response
        incomingChatLi.classList.remove("response-reveal");
        void incomingChatLi.offsetHeight; // force reflow to restart animation
        incomingChatLi.classList.add("response-reveal");

        // Add AI response to history
        chatHistory.push({ role: "model", content: data.response });
        if (activeChatId) {
            ChatHistoryStore.appendMessage(activeChatId,
                { role: "model", content: data.response });
        }

        // Enrollment banner is persistent — no need to append scheduler options here

    } catch (error) {
        // Handle errors
        // If it's our custom rate limit error (or other server error msg), show that.
        // Otherwise, show generic connection error.
        if (error.message && (error.message.includes("429") || error.message.includes("Server Error"))) {
            messageElement.textContent = error.message;
        } else {
            messageElement.textContent = "Oops! I couldn't connect to the server. Make sure 'python server.py' is running.";
        }
        messageElement.style.color = "#cc0000";
    } finally {
        // No scroll — response stays below the visible area; user scrolls manually.
    }
}

const handleChat = () => {
    userMessage = chatInput.value.trim();
    if (!userMessage) return;

    const language = langSelect ? langSelect.value : "English";
    const isFirstMessageOfChat = activeChatId === null;

    if (welcomeScreen && !welcomeScreen.classList.contains('hidden')) {
        welcomeScreen.classList.add('hidden');
        chatbox.style.display = '';
    }

    const outgoingChatLi = createChatLi(userMessage, "outgoing");
    chatbox.appendChild(outgoingChatLi);

    if (!contactBannerShown && sessionStorage.getItem('contactBannerDismissed') !== 'true') {
        updateBannerText(language);
        contactBanner.classList.add('visible');
        contactBannerShown = true;
    }

    chatHistory.push({ role: "user", content: userMessage });

    // Persist: create chat on first message, otherwise append.
    if (isFirstMessageOfChat) {
        const chat = ChatHistoryStore.create(userMessage, language);
        activeChatId = chat.id;
        ChatHistoryStore.setActiveId(chat.id);
        fetchTitleForChat(chat.id, userMessage, language);
    } else {
        ChatHistoryStore.appendMessage(activeChatId,
            { role: "user", content: userMessage });
    }

    chatInput.value = "";
    chatInput.style.height = "38px";
    sendChatBtn.classList.remove("active");

    const incomingChatLi = createChatLi("", "incoming");
    startThinkingAnimation(incomingChatLi.querySelector("p"));
    chatbox.appendChild(incomingChatLi);

    chatbox.scrollTo({
        top: outgoingChatLi.offsetTop - 15,
        behavior: 'smooth'
    });

    generateResponse(incomingChatLi);
}

// Toggle send button active state based on textarea content
chatInput.addEventListener("input", () => {
    // Adjust height dynamically based on content
    chatInput.style.height = "38px";
    let newHeight = chatInput.scrollHeight;
    if (newHeight > 180) newHeight = 180; // ensure max height is respected, but scroll kicks in
    chatInput.style.height = `${newHeight}px`;

    if (chatInput.value.trim()) {
        sendChatBtn.classList.add("active");
    } else {
        sendChatBtn.classList.remove("active");
    }
});

// Handle "Enter" key press
chatInput.addEventListener("keydown", (e) => {
    // If Enter key is pressed without Shift key
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleChat();
    }
});

// Event Listeners
sendChatBtn.addEventListener("click", handleChat);
chatbotToggler.addEventListener("click", () => {
    const isShowing = document.body.classList.toggle("show-chatbot");
    if (isShowing && typeof window.__cancelTogglerGlow === "function") {
        window.__cancelTogglerGlow();
    }
    if (!isShowing) setSidebarOpen(false);
    window.parent.postMessage({ type: "toggle", showing: isShowing }, "*");
    if (isShowing && welcomeScreen && !welcomeScreen.classList.contains('hidden')) {
        showWelcome();
    }
});

closeBtn.addEventListener("click", () => {
    document.body.classList.remove("show-chatbot");
    setSidebarOpen(false);
    window.parent.postMessage({ type: "toggle", showing: false }, "*");
});

// New Chat: clear the view but keep previous chat saved in the sidebar.
newChatBtn.addEventListener("click", () => {
    activeChatId = null;
    ChatHistoryStore.setActiveId(null);
    startBlankChat();
    renderSidebar();
});

// Dismiss contact banner for the rest of the session
if (contactBannerClose) {
    contactBannerClose.addEventListener('click', () => {
        contactBanner.style.display = 'none';
        sessionStorage.setItem('contactBannerDismissed', 'true');
    });
}

// Initialize disclaimer text in the user's selected language on first load.
updateDisclaimerText(langSelect ? langSelect.value : 'English');

// Language selector: update welcome text immediately if welcome screen is visible
if (langSelect) {
    langSelect.addEventListener("change", () => {
        if (welcomeScreen && !welcomeScreen.classList.contains('hidden')) {
            updateWelcomeText(langSelect.value);
        }
        updateBannerText(langSelect.value);
        updateDisclaimerText(langSelect.value);
    });
}

// Chat history wiring
ChatHistoryStore.subscribe(renderSidebar);
renderSidebar();

if (historyBtn) {
    historyBtn.addEventListener("click", () => setSidebarOpen(!sidebarOpen));
}

// The drawer covers the .history-btn that opened it on mobile, so the scrim is
// the only way back out. It is display:none and pointer-events:none unless the
// drawer is open on mobile, so this never fires on desktop.
if (scrim) {
    scrim.addEventListener("click", () => setSidebarOpen(false));
}

if (sidebarClear) {
    sidebarClear.addEventListener("click", () => {
        if (sidebarConfirm) sidebarConfirm.hidden = false;
    });
}

if (sidebarConfirmYes) {
    sidebarConfirmYes.addEventListener("click", () => {
        sidebarConfirm.hidden = true;
        ChatHistoryStore.clearAll();
        ChatHistoryStore.setActiveId(null);
        activeChatId = null;
        startBlankChat();
    });
}

if (sidebarConfirmCancel) {
    sidebarConfirmCancel.addEventListener("click", () => {
        sidebarConfirm.hidden = true;
    });
}

if (sidebarList) {
    sidebarList.addEventListener("click", (e) => {
        const delBtn = e.target.closest(".chat-sidebar-delete");
        const item   = e.target.closest(".chat-sidebar-item");
        if (!item) return;
        const chatId = item.dataset.chatId;

        if (delBtn) {
            e.stopPropagation();
            ChatHistoryStore.remove(chatId);
            if (chatId === activeChatId) {
                activeChatId = null;
                ChatHistoryStore.setActiveId(null);
                startBlankChat();
            }
            return;
        }

        loadChat(chatId);
        // On mobile the drawer covers the chat, so picking a conversation would
        // otherwise load it out of sight with no feedback. On desktop the rail
        // is beside the panel and stays put.
        if (isMobileMode) setSidebarOpen(false);
    });
}

// Mobile mode — parent page tells us when viewport is mobile-sized.
let isMobileMode = false;
window.addEventListener("message", (e) => {
    if (e.data && e.data.type === "set-mobile") {
        const changed = e.data.mobile !== isMobileMode;
        isMobileMode = e.data.mobile;
        if (chatbotEl) chatbotEl.classList.toggle("mobile", isMobileMode);
        document.body.classList.toggle("mobile", isMobileMode);
        // The desktop and mobile presentations of the history bar are different
        // objects (a rail the parent makes room for vs. a modal drawer it does
        // not). Carrying an open state across the flip strands it: the parent
        // resets its own isSidebarOpen on resize without telling us, so the bar
        // would render with no room for it and the next tap on .history-btn
        // would CLOSE something the user never saw open. Close it and re-measure.
        if (changed) {
            if (sidebarOpen) setSidebarOpen(false);
            syncChatWidth();
        }
    }
});

// Draggable chatbot window — drag by header (desktop only).
const chatbotHeader = document.querySelector(".chatbot header");

if (chatbotHeader) {
    chatbotHeader.addEventListener("mousedown", (e) => {
        if (isMobileMode) return;
        if (e.target.closest(".close-btn") || e.target.closest(".new-chat-btn") || e.target.closest(".history-btn") || e.target.closest(".lang-select")) {
            return;
        }
        e.preventDefault();
        window.parent.postMessage({
            type: "chatbot-drag-start",
            screenX: e.screenX,
            screenY: e.screenY
        }, "*");

        const onMove = (ev) => {
            window.parent.postMessage({
                type: "chatbot-drag-move",
                screenX: ev.screenX,
                screenY: ev.screenY
            }, "*");
        };
        const onUp = () => {
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
            window.parent.postMessage({ type: "chatbot-drag-end" }, "*");
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
    });
}

// Restore active chat on iframe load so a refresh doesn't lose the view.
(() => {
    const storedId = ChatHistoryStore.getActiveId();
    if (!storedId) return;
    const chat = ChatHistoryStore.get(storedId);
    if (!chat) {
        ChatHistoryStore.setActiveId(null);
        return;
    }
    activeChatId = chat.id;
    chatHistory = chat.history.slice();
    welcomeScreen.classList.add("hidden");
    renderChatIntoBox(chat.history);
    renderSidebar();
})();

/* ---------------------------------------------------------------------
   Resize handle (iOS-style, bottom-left corner)
   The iframe clips its contents, so a resize drag inside the iframe can't
   grow it on its own. We forward screenX/screenY to the parent page, which
   resizes the iframe itself — mirroring the header-drag pattern above.
   The handle is the bottom-left corner, so the window grows toward the
   top-right (drag left = wider, drag down = taller).
   --------------------------------------------------------------------- */
const resizeHandle = document.querySelector(".resize-handle");

if (resizeHandle) {
    resizeHandle.addEventListener("mousedown", (e) => {
        if (isMobileMode) return;
        e.preventDefault();
        e.stopPropagation(); // don't start a header drag
        resizeHandle.classList.add("dragging");
        window.parent.postMessage({
            type: "chatbot-resize-start",
            screenX: e.screenX,
            screenY: e.screenY
        }, "*");

        const onMove = (ev) => {
            window.parent.postMessage({
                type: "chatbot-resize-move",
                screenX: ev.screenX,
                screenY: ev.screenY
            }, "*");
        };
        const onUp = () => {
            resizeHandle.classList.remove("dragging");
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
            window.parent.postMessage({ type: "chatbot-resize-end" }, "*");
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
    });
}

// Keep --chat-w in sync as the parent resizes the iframe (and on first paint).
const syncRO = new ResizeObserver(() => syncChatWidth());
if (chatbotEl) syncRO.observe(chatbotEl);
window.addEventListener("load", syncChatWidth);
// Initial sync once layout has settled.
syncChatWidth();
