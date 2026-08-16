import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { installBrowserGlobals } from "./localstorage_shim.mjs";

installBrowserGlobals();

// Load the store by reading the file and evaluating it inside a Function
// scope that provides `window` (where the IIFE publishes ChatHistoryStore).
// This avoids the cost of an ES-module refactor and keeps the frontend
// file usable as a plain <script src> in the browser.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const storePath = path.join(__dirname, "..", "..", "frontend", "chat_history_store.js");
const storeSource = fs.readFileSync(storePath, "utf8");
new Function("window", storeSource)(globalThis.window);
const { ChatHistoryStore } = globalThis.window;

test("ChatHistoryStore is exported on window", () => {
    assert.ok(ChatHistoryStore, "window.ChatHistoryStore should be defined after loading chat_history_store.js");
});

test("create inserts a chat with placeholder title and timestamps", () => {
    localStorage.clear();
    const chat = ChatHistoryStore.create("How much is tuition?", "English");
    assert.equal(typeof chat.id, "string");
    assert.equal(chat.title, "…");
    assert.equal(chat.language, "English");
    assert.equal(chat.history.length, 1);
    assert.equal(chat.history[0].role, "user");
    assert.equal(chat.history[0].content, "How much is tuition?");
    assert.ok(chat.createdAt <= Date.now());
    assert.equal(chat.schemaVersion, 1);
});

test("list returns newest first", async () => {
    localStorage.clear();
    const a = ChatHistoryStore.create("first", "English");
    await new Promise(r => setTimeout(r, 2));
    const b = ChatHistoryStore.create("second", "English");
    const list = ChatHistoryStore.list();
    assert.equal(list[0].id, b.id);
    assert.equal(list[1].id, a.id);
});

test("cap of 50 prunes oldest on 51st insert", async () => {
    localStorage.clear();
    ChatHistoryStore.create("msg 0", "English");
    await new Promise(r => setTimeout(r, 2));
    for (let i = 1; i < 51; i++) ChatHistoryStore.create(`msg ${i}`, "English");
    const list = ChatHistoryStore.list();
    assert.equal(list.length, 50);
    assert.ok(!list.some(c => c.history[0].content === "msg 0"));
});

test("appendMessage bumps updatedAt and appends", async () => {
    localStorage.clear();
    const chat = ChatHistoryStore.create("first", "English");
    const before = chat.updatedAt;
    await new Promise(r => setTimeout(r, 2));
    ChatHistoryStore.appendMessage(chat.id, { role: "model", content: "hi" });
    const after = ChatHistoryStore.get(chat.id);
    assert.equal(after.history.length, 2);
    assert.ok(after.updatedAt > before);
});

test("setTitle updates only the title", () => {
    localStorage.clear();
    const chat = ChatHistoryStore.create("first", "English");
    ChatHistoryStore.setTitle(chat.id, "Nice title");
    assert.equal(ChatHistoryStore.get(chat.id).title, "Nice title");
});

test("remove deletes only the target chat", () => {
    localStorage.clear();
    const a = ChatHistoryStore.create("a", "English");
    const b = ChatHistoryStore.create("b", "English");
    ChatHistoryStore.remove(a.id);
    assert.equal(ChatHistoryStore.get(a.id), null);
    assert.ok(ChatHistoryStore.get(b.id));
});

test("clearAll empties the store", () => {
    localStorage.clear();
    ChatHistoryStore.create("a", "English");
    ChatHistoryStore.create("b", "English");
    ChatHistoryStore.clearAll();
    assert.equal(ChatHistoryStore.list().length, 0);
});

test("corrupted JSON under storage key is recovered as empty store", () => {
    localStorage.clear();
    localStorage.setItem("site_chatbot_chat_history", "{not valid json");
    assert.deepEqual(ChatHistoryStore.list(), []);
});

test("subscribe fires on every mutation", () => {
    localStorage.clear();
    let count = 0;
    const unsub = ChatHistoryStore.subscribe(() => count++);
    ChatHistoryStore.create("a", "English");
    ChatHistoryStore.setTitle(ChatHistoryStore.list()[0].id, "x");
    ChatHistoryStore.clearAll();
    unsub();
    ChatHistoryStore.create("b", "English");  // should not count
    assert.equal(count, 3);
});
