/**
 * Minimal localStorage + sessionStorage shim for Node tests.
 * Mimics the Web Storage API surface that ChatHistoryStore depends on.
 */
export class MemoryStorage {
    constructor() { this._data = new Map(); }
    get length()   { return this._data.size; }
    key(i)         { return Array.from(this._data.keys())[i] ?? null; }
    getItem(k)     { return this._data.has(k) ? this._data.get(k) : null; }
    setItem(k, v)  { this._data.set(k, String(v)); }
    removeItem(k)  { this._data.delete(k); }
    clear()        { this._data.clear(); }
}

export function installBrowserGlobals() {
    globalThis.localStorage   = new MemoryStorage();
    globalThis.sessionStorage = new MemoryStorage();

    // Minimal EventTarget-like window so code under test can register for
    // the "storage" event (cross-tab sync).
    const listeners = new Map();
    globalThis.window = {
        addEventListener(type, cb) {
            if (!listeners.has(type)) listeners.set(type, new Set());
            listeners.get(type).add(cb);
        },
        removeEventListener(type, cb) {
            listeners.get(type)?.delete(cb);
        },
        dispatchEvent(event) {
            (listeners.get(event.type) || []).forEach(cb => {
                try { cb(event); } catch (_) {}
            });
            return true;
        },
    };
}
