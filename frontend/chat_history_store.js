// ---------------------------------------------------------------------------
// ChatHistoryStore — localStorage-backed persistence for chat transcripts.
//
// Published as `window.ChatHistoryStore`. DOM-free so it can be unit-tested
// in Node by evaluating the file in a scope that provides `window`.
// ---------------------------------------------------------------------------
(function (window) {
"use strict";

const ChatHistoryStore = (() => {
    // Key namespace comes from site_config.js when present (browser); the
    // Node unit tests evaluate this file with a bare `window`, so fall back.
    const PREFIX = (window.SITE_CONFIG && window.SITE_CONFIG.storagePrefix) || "site_chatbot";
    const KEY = `${PREFIX}_chat_history`;
    const ACTIVE_KEY = `${PREFIX}_active_chat_id`;
    const MAX_CHATS = 50;
    const SCHEMA_VERSION = 1;
    const PLACEHOLDER_TITLE = "…";

    const listeners = new Set();
    let inMemoryFallback = null; // populated when localStorage writes throw

    const safeGetAll = () => {
        if (inMemoryFallback) return inMemoryFallback;
        try {
            const raw = localStorage.getItem(KEY);
            if (!raw) return [];
            const parsed = JSON.parse(raw);
            if (!Array.isArray(parsed)) return [];
            return parsed.filter(c => c && c.schemaVersion === SCHEMA_VERSION);
        } catch (e) {
            console.warn("[ChatHistoryStore] corrupted storage, resetting:", e);
            return [];
        }
    };

    const persist = (chats) => {
        if (inMemoryFallback) { inMemoryFallback = chats; return; }
        try {
            localStorage.setItem(KEY, JSON.stringify(chats));
        } catch (e) {
            // Quota: prune oldest until it fits, else fall back to memory.
            let pruned = chats.slice();
            while (pruned.length > 1) {
                pruned.pop();
                try { localStorage.setItem(KEY, JSON.stringify(pruned)); return; }
                catch (_) { /* keep pruning */ }
            }
            console.warn("[ChatHistoryStore] storage write failed, switching to memory");
            inMemoryFallback = chats;
        }
    };

    const notify = () => listeners.forEach(cb => { try { cb(); } catch (_) {} });

    const newId = () =>
        `chat_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;

    const list = () =>
        safeGetAll().slice().sort((a, b) => b.updatedAt - a.updatedAt);

    const get = (id) => safeGetAll().find(c => c.id === id) || null;

    const create = (firstUserMessage, language) => {
        const now = Date.now();
        const chat = {
            id: newId(),
            schemaVersion: SCHEMA_VERSION,
            title: PLACEHOLDER_TITLE,
            createdAt: now,
            updatedAt: now,
            language,
            history: [{ role: "user", content: firstUserMessage }],
        };
        let all = safeGetAll();
        all.push(chat);
        // Enforce cap by updatedAt ascending, drop oldest.
        if (all.length > MAX_CHATS) {
            all.sort((a, b) => a.updatedAt - b.updatedAt);
            all = all.slice(all.length - MAX_CHATS);
        }
        persist(all);
        notify();
        return chat;
    };

    const appendMessage = (id, msg) => {
        const all = safeGetAll();
        const chat = all.find(c => c.id === id);
        if (!chat) return null;
        chat.history.push(msg);
        chat.updatedAt = Date.now();
        persist(all);
        notify();
        return chat;
    };

    const setTitle = (id, title) => {
        const all = safeGetAll();
        const chat = all.find(c => c.id === id);
        if (!chat) return;
        chat.title = title;
        persist(all);
        notify();
    };

    const remove = (id) => {
        const all = safeGetAll().filter(c => c.id !== id);
        persist(all);
        notify();
    };

    const clearAll = () => { persist([]); notify(); };

    const subscribe = (cb) => { listeners.add(cb); return () => listeners.delete(cb); };

    const getActiveId = () => {
        try { return localStorage.getItem(ACTIVE_KEY); } catch (_) { return null; }
    };
    const setActiveId = (id) => {
        try {
            if (id) localStorage.setItem(ACTIVE_KEY, id);
            else    localStorage.removeItem(ACTIVE_KEY);
        } catch (_) { /* ignore */ }
    };

    // Cross-tab sync: when another tab writes to localStorage, refresh listeners.
    if (typeof window !== "undefined" && window.addEventListener) {
        window.addEventListener("storage", (e) => {
            if (e.key === KEY) notify();
        });
    }

    return { list, get, create, appendMessage, setTitle, remove, clearAll,
             subscribe, getActiveId, setActiveId };
})();

window.ChatHistoryStore = ChatHistoryStore;

})(typeof window !== "undefined" ? window : globalThis);
