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

// Clear the composer back to its resting state. Kept in one place because the
// three steps must stay in sync — the height reset in particular, since the
// textarea auto-grows and would otherwise stay tall after sending.
const resetInput = () => {
    chatInput.value = "";
    chatInput.style.height = "38px";
    sendChatBtn.classList.remove("active");
};

// Set the composer's text programmatically. Assigning .value does NOT fire the
// "input" listener that auto-resizes the box and enables the send button, so
// dispatching the event by hand is what keeps dictated text sendable.
const setInputValue = (text) => {
    chatInput.value = text;
    chatInput.dispatchEvent(new Event("input", { bubbles: true }));
};

const startBlankChat = () => {
    chatbox.innerHTML = "";
    chatbox.style.display = "none";
    showWelcome();
    chatHistory = [];
    resetInput();
    // Leaving the conversation also leaves any guided workflow — otherwise the
    // next message would still be routed to a questionnaire that is no longer
    // on screen. The saved run itself is kept, so the launcher chip offers to
    // resume it rather than losing the answers.
    if (workflowMode && window.WorkflowClient) {
        workflowMode = null;
        window.WorkflowClient.setActive(null);
    }
    // Defined further down the file; by the time this runs, module load has
    // finished and it is initialized.
    renderWorkflowBanner(null);
    document.querySelectorAll(".workflow-choices").forEach(el => el.remove());
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

// Id of the guided workflow currently running, or null for ordinary chat.
// Declared here because handleChat branches on it.
let workflowMode = null;

const handleChat = (textOverride) => {
    // textOverride lets callers (voice dictation, guided-workflow choice
    // buttons) send text without round-tripping it through the textarea.
    // Checked with typeof, not against undefined: handleChat is also used
    // directly as a click listener, which would otherwise pass a MouseEvent
    // here and blow up on .trim().
    const source = typeof textOverride === "string" ? textOverride : chatInput.value;
    userMessage = source.trim();
    if (!userMessage) return;

    // A workflow answer goes to the workflow API, not to /chat: it must not be
    // spam-checked, retrieved against, logged to the analytics collection, or
    // scored for faithfulness.
    if (workflowMode) {
        handleWorkflowTurn(userMessage);
        return;
    }

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

    resetInput();

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
sendChatBtn.addEventListener("click", () => handleChat());
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
// A workflow in progress owns the chatbox instead, and restores itself in the
// workflow block below — so this bails out and leaves the element alone.
(() => {
    if (window.WorkflowClient && window.WorkflowClient.isActive()) return;
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

/* ---------------------------------------------------------------------
   Voice input (dictation)

   Speech recognition runs entirely in the browser via window.VoiceInput
   (frontend/voice_input.js) — no server round trip, no second API key.
   Where the browser has no Web Speech API the button is never revealed and
   everything below is inert, so typing is unaffected.

   Dictated text lands in the composer for review rather than sending itself:
   recognition mishears often enough that auto-sending would ship errors the
   user never got to catch.
   --------------------------------------------------------------------- */
const micBtn = document.getElementById("mic-btn");
const voiceStatus = document.getElementById("voice-status");

const voiceTranslations = SITE_CONFIG.voiceTranslations || {};

const voiceStrings = (lang) =>
    voiceTranslations[lang] || voiceTranslations["English"] || {};

let recognizer = null;
// Text already in the box when dictation started. Interim results are appended
// to this rather than to the live value, so each interim update REPLACES the
// previous guess instead of stacking copies of it.
let dictationBase = "";
let voiceStatusTimer = null;

const setVoiceStatus = (text, isError) => {
    if (!voiceStatus) return;
    if (voiceStatusTimer) {
        clearTimeout(voiceStatusTimer);
        voiceStatusTimer = null;
    }
    voiceStatus.textContent = text || "";
    voiceStatus.classList.toggle("is-error", Boolean(isError));
    // The status line borrows the disclaimer's slot, so the disclaimer has to
    // step aside while a message is showing.
    if (chatbotEl) chatbotEl.classList.toggle("voice-speaking", Boolean(text));
    // Errors are transient; "Listening…" is cleared by the caller on stop.
    if (text && isError) {
        voiceStatusTimer = setTimeout(() => setVoiceStatus("", false), 5000);
    }
};

const currentLang = () => (langSelect ? langSelect.value : "English");

const updateVoiceText = (lang) => {
    if (!micBtn) return;
    const t = voiceStrings(lang);
    const recording = micBtn.classList.contains("recording");
    const label = recording ? t.stop : t.start;
    if (label) {
        micBtn.setAttribute("aria-label", label);
        micBtn.setAttribute("title", label);
    }
    if (recording && t.listening) setVoiceStatus(t.listening, false);
};

const stopDictation = () => {
    if (recognizer && recognizer.isActive()) recognizer.stop();
};

const startDictation = () => {
    const t = voiceStrings(currentLang());

    if (!recognizer) {
        recognizer = window.VoiceInput.create({
            lang: currentLang(),
            onInterim: (text) => {
                if (!text) return;
                setInputValue((dictationBase + " " + text).trim());
            },
            onFinal: (text) => {
                dictationBase = (dictationBase + " " + text).trim();
                setInputValue(dictationBase);
            },
            onError: (key) => {
                const strings = voiceStrings(currentLang());
                setVoiceStatus(strings[key] || strings.other || "", true);
            },
            onEnd: () => {
                micBtn.classList.remove("recording");
                micBtn.setAttribute("aria-pressed", "false");
                updateVoiceText(currentLang());
                // Leave an error message up; otherwise clear "Listening…".
                if (!voiceStatus.classList.contains("is-error")) {
                    setVoiceStatus("", false);
                }
                chatInput.focus();
            },
        });
    }
    if (!recognizer) return;

    // Continue from whatever is already typed rather than clobbering it.
    dictationBase = chatInput.value.trim();
    recognizer.setLang(currentLang());
    recognizer.start();
    micBtn.classList.add("recording");
    micBtn.setAttribute("aria-pressed", "true");
    updateVoiceText(currentLang());
    setVoiceStatus(t.listening || "", false);

    // Disclose once per browser that recognition is the browser's, not ours.
    try {
        const seenKey = `${STORAGE_PREFIX}_voice_privacy_seen`;
        if (t.privacy && !localStorage.getItem(seenKey)) {
            localStorage.setItem(seenKey, "1");
            setTimeout(() => {
                if (recognizer && recognizer.isActive()) return;
                setVoiceStatus(t.privacy, false);
                voiceStatusTimer = setTimeout(() => setVoiceStatus("", false), 6000);
            }, 400);
        }
    } catch (_) { /* private mode: skip the notice rather than break dictation */ }
};

if (micBtn && window.VoiceInput && window.VoiceInput.isSupported()) {
    micBtn.hidden = false;
    updateVoiceText(currentLang());
    micBtn.addEventListener("click", () => {
        // start()/stop() must run inside the click handler: iOS Safari only
        // grants microphone access on the same tick as the user gesture.
        if (recognizer && recognizer.isActive()) stopDictation();
        else startDictation();
    });
    // Sending mid-dictation would keep the recognizer running against an empty
    // box and append the next phrase to nothing.
    sendChatBtn.addEventListener("click", stopDictation);
    chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) stopDictation();
    });
    if (langSelect) {
        langSelect.addEventListener("change", () => {
            if (recognizer) recognizer.setLang(langSelect.value);
            updateVoiceText(langSelect.value);
        });
    }
}

/* ---------------------------------------------------------------------
   Guided workflows

   A workflow is a questionnaire the assistant walks the visitor through,
   defined by a JSON spec on the server. The turn loop lives in
   window.WorkflowClient (frontend/workflow_client.js); everything here is
   presentation.

   Two constraints shape this code:

   * Workflow turns are stored in the transcript as ordinary {role, content}
     pairs, which is exactly what renderChatIntoBox already understands. So a
     reload replays the conversation losslessly with no schema change — and
     crucially without bumping ChatHistoryStore's SCHEMA_VERSION, which would
     silently discard every saved chat a visitor already has.
   * A run is deliberately NOT a ChatHistoryStore chat. If it were, its turns
     would be sent to /chat as `history` and trigger a title generation on an
     interview prompt.
   --------------------------------------------------------------------- */
const welcomeWorkflows = document.getElementById("welcome-workflows");
const workflowBanner = document.getElementById("workflow-banner");
const workflowBannerText = document.getElementById("workflow-banner-text");
const workflowExitBtn = document.getElementById("workflow-exit-btn");

const workflowTranslations = SITE_CONFIG.workflowTranslations || {};
const workflowStrings = (lang) =>
    workflowTranslations[lang] || workflowTranslations["English"] || {};

if (window.WorkflowClient) {
    window.WorkflowClient.configure({
        apiBase: SITE_CONFIG.apiBase || "",
        storagePrefix: STORAGE_PREFIX,
    });
}

const renderWorkflowBanner = (progress) => {
    if (!workflowBanner) return;
    const t = workflowStrings(currentLang());
    if (!workflowMode || !progress) {
        workflowBanner.hidden = true;
        return;
    }
    workflowBanner.hidden = false;
    workflowBannerText.textContent =
        window.WorkflowClient.progressLabel(progress, t.progress);
    if (workflowExitBtn) workflowExitBtn.textContent = t.exit || "Exit";
};

// Buttons live in their own row rather than inside the message bubble so the
// transcript itself stays plain {role, content} and replays without them.
const renderWorkflowChoices = (choices) => {
    document.querySelectorAll(".workflow-choices").forEach(el => el.remove());
    if (!choices || !choices.length) return;

    const row = document.createElement("div");
    row.className = "workflow-choices";
    choices.forEach(choice => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "workflow-choice-btn";
        btn.textContent = choice.label;
        btn.addEventListener("click", () => {
            // Retire the whole group: the question has moved on, and a second
            // click would answer a question no longer being asked.
            row.querySelectorAll("button").forEach(b => { b.disabled = true; });
            handleWorkflowTurn(choice.value);
        });
        row.appendChild(btn);
    });
    chatbox.appendChild(row);
};

const appendWorkflowMessage = (content) => {
    const li = createChatLi("", "incoming");
    li.querySelector("p").innerHTML = formatMessage(content);
    chatbox.appendChild(li);
    return li;
};

const downloadDocument = (doc) => {
    const blob = new Blob([doc.body], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = doc.filename || "response.md";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    // Revoking immediately can cancel the download in some browsers.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const copyText = async (text) => {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (_) {
        // Older browsers, and any context where the clipboard permission was
        // not delegated to this iframe.
        try {
            const area = document.createElement("textarea");
            area.value = text;
            area.style.position = "fixed";
            area.style.opacity = "0";
            document.body.appendChild(area);
            area.select();
            const ok = document.execCommand("copy");
            document.body.removeChild(area);
            return ok;
        } catch (__) {
            return false;
        }
    }
};

const renderWorkflowDocument = (doc, workflowId) => {
    const t = workflowStrings(currentLang());
    const card = document.createElement("div");
    card.className = "workflow-doc";

    const body = document.createElement("div");
    body.className = "workflow-doc-body";
    body.innerHTML = formatMessage(doc.body);
    card.appendChild(body);

    const actions = document.createElement("div");
    actions.className = "workflow-doc-actions";

    const addButton = (label, onClick) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "workflow-choice-btn";
        btn.textContent = label;
        btn.addEventListener("click", () => onClick(btn));
        actions.appendChild(btn);
        return btn;
    };

    addButton(t.copy || "Copy", async (btn) => {
        const ok = await copyText(doc.body);
        btn.textContent = ok ? (t.copied || "Copied") : (t.copy || "Copy");
        setTimeout(() => { btn.textContent = t.copy || "Copy"; }, 2000);
    });

    addButton(t.download || "Download", () => downloadDocument(doc));

    addButton(t.email || "Email it", (btn) => {
        btn.disabled = true;
        window.WorkflowClient.submit(workflowId, true)
            .then(result => {
                btn.textContent = result && result.emailed
                    ? (t.emailed || "Sent")
                    : (t.emailFailed || "Couldn't send");
            })
            .catch(() => { btn.textContent = t.emailFailed || "Couldn't send"; });
    });

    card.appendChild(actions);
    chatbox.appendChild(card);
};

const applyWorkflowTurn = (turn, workflowId) => {
    turn.messages.forEach(m => appendWorkflowMessage(m.content));

    if (turn.status === "complete" && turn.document) {
        renderWorkflowDocument(turn.document, workflowId);
        // Save the response server-side without emailing; the Email button
        // above is what opts into mail.
        window.WorkflowClient.submit(workflowId, false).catch(() => {});
        workflowMode = null;
        renderWorkflowBanner(null);
        renderWorkflowChoices([]);
    } else {
        renderWorkflowBanner(turn.progress);
        renderWorkflowChoices(turn.choices);
    }

    chatbox.scrollTo({ top: chatbox.scrollHeight, behavior: "smooth" });
};

function handleWorkflowTurn(text) {
    const workflowId = workflowMode;
    if (!workflowId) return;

    chatbox.appendChild(createChatLi(text, "outgoing"));
    resetInput();
    document.querySelectorAll(".workflow-choices").forEach(el => el.remove());

    const thinking = createChatLi("", "incoming");
    startThinkingAnimation(thinking.querySelector("p"));
    chatbox.appendChild(thinking);
    chatbox.scrollTo({ top: chatbox.scrollHeight, behavior: "smooth" });

    window.WorkflowClient.send(workflowId, text, currentLang())
        .then(turn => {
            thinking.remove();
            applyWorkflowTurn(turn, workflowId);
        })
        .catch(err => {
            thinking.remove();
            const t = workflowStrings(currentLang());
            const li = appendWorkflowMessage(t.failed || "Something went wrong.");
            li.querySelector("p").style.color = "#cc0000";
            console.warn("[workflow] turn failed:", err.message);
        });
}

const startWorkflow = (workflowId) => {
    workflowMode = workflowId;
    chatbox.innerHTML = "";
    chatbox.style.display = "";
    if (welcomeScreen) welcomeScreen.classList.add("hidden");
    // A workflow is not a chat: keep it out of the sidebar history entirely.
    activeChatId = null;
    ChatHistoryStore.setActiveId(null);
    chatHistory = [];

    window.WorkflowClient.start(workflowId, currentLang())
        .then(turn => applyWorkflowTurn(turn, workflowId))
        .catch(err => {
            workflowMode = null;
            const t = workflowStrings(currentLang());
            appendWorkflowMessage(t.failed || "Something went wrong.");
            console.warn("[workflow] start failed:", err.message);
        });
};

const exitWorkflow = () => {
    if (workflowMode) window.WorkflowClient.setActive(null);
    workflowMode = null;
    renderWorkflowBanner(null);
    renderWorkflowChoices([]);
    startBlankChat();
};

if (workflowExitBtn) workflowExitBtn.addEventListener("click", exitWorkflow);

const renderWorkflowLaunchers = (workflows) => {
    if (!welcomeWorkflows) return;
    welcomeWorkflows.innerHTML = "";
    if (!workflows || !workflows.length) return;   // nothing configured: stay invisible

    const t = workflowStrings(currentLang());
    const heading = document.createElement("p");
    heading.className = "welcome-workflows-heading";
    heading.textContent = t.heading || "Or start a guided walkthrough:";
    welcomeWorkflows.appendChild(heading);

    workflows.forEach(workflow => {
        const existing = window.WorkflowClient.getRun(workflow.id);
        const resumable = existing && existing.status !== "complete";

        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "workflow-chip";

        const title = document.createElement("span");
        title.className = "workflow-chip-title";
        title.textContent = resumable
            ? `${t.resume || "Resume"}: ${workflow.title}`
            : workflow.title;

        const meta = document.createElement("span");
        meta.className = "workflow-chip-meta";
        const bits = [];
        if (workflow.estimated_minutes) {
            bits.push((t.minutes || "~{n} min").replace("{n}", workflow.estimated_minutes));
        }
        if (workflow.questions) bits.push(`${workflow.questions} questions`);
        meta.textContent = bits.join(" · ");

        chip.appendChild(title);
        if (meta.textContent) chip.appendChild(meta);
        chip.addEventListener("click", () => {
            if (resumable) resumeWorkflow(workflow.id);
            else startWorkflow(workflow.id);
        });
        welcomeWorkflows.appendChild(chip);
    });
};

function resumeWorkflow(workflowId) {
    const run = window.WorkflowClient.getRun(workflowId);
    if (!run) { startWorkflow(workflowId); return; }
    workflowMode = workflowId;
    window.WorkflowClient.setActive(workflowId);
    if (welcomeScreen) welcomeScreen.classList.add("hidden");
    chatbox.style.display = "";
    // The saved transcript is plain {role, content}, so the ordinary renderer
    // replays it. Only the live controls need rebuilding.
    renderChatIntoBox(run.messages || []);
    renderWorkflowBanner(run.progress);
    renderWorkflowChoices(run.choices);
    chatbox.scrollTo({ top: chatbox.scrollHeight });
}

if (window.WorkflowClient) {
    // Restore a run in progress before anything else paints the chatbox.
    const activeId = window.WorkflowClient.activeWorkflowId();
    if (activeId && window.WorkflowClient.getRun(activeId)) {
        resumeWorkflow(activeId);
    }
    window.WorkflowClient.list().then(renderWorkflowLaunchers);

    if (langSelect) {
        langSelect.addEventListener("change", () => {
            const run = workflowMode && window.WorkflowClient.getRun(workflowMode);
            renderWorkflowBanner(run ? run.progress : null);
            window.WorkflowClient.list().then(renderWorkflowLaunchers);
        });
    }
}
