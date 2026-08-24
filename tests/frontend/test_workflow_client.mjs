// Unit tests for frontend/workflow_client.js.
//
// fetch is injected rather than stubbed globally, so these run with no network
// and no server. The interesting surface is normalizeTurn (which has to survive
// a hostile or partial response) and the localStorage persistence that makes
// "stop and resume across sittings" work.
//
// Run: node --test tests/frontend/test_workflow_client.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { installBrowserGlobals } from "./localstorage_shim.mjs";

installBrowserGlobals();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(
    path.join(__dirname, "..", "..", "frontend", "workflow_client.js"), "utf8");

new Function("window", source)(globalThis.window);
const { WorkflowClient } = globalThis.window;

/** Build a fetch stub that returns the given payloads in order. */
function stubFetch(responses) {
    const calls = [];
    const queue = responses.slice();
    const impl = (url, options) => {
        calls.push({
            url,
            body: options && options.body ? JSON.parse(options.body) : null,
        });
        const next = queue.length > 1 ? queue.shift() : queue[0];
        return Promise.resolve({
            ok: next.ok !== false,
            status: next.status || 200,
            json: () => Promise.resolve(next.json),
        });
    };
    return { impl, calls };
}

function turnPayload(overrides) {
    return Object.assign({
        state: { v: 1, workflow_id: "wf", spec_hash: "abc123def456", cursor: {} },
        messages: [{ role: "model", content: "First question?" }],
        choices: [{ label: "Skip", value: "skip" }],
        progress: { section_index: 0, section_total: 3, section_title: "One",
                    answered: 0, total: 6, percent: 0 },
        status: "in_progress",
        document: null,
    }, overrides || {});
}

function reset(responses) {
    localStorage.clear();
    WorkflowClient.__resetCache();
    const stub = stubFetch(responses || [{ json: turnPayload() }]);
    WorkflowClient.configure({
        apiBase: "", storagePrefix: "test_prefix", fetchImpl: stub.impl,
    });
    return stub;
}

// ---------------------------------------------------------------------------
// Module surface
// ---------------------------------------------------------------------------

test("WorkflowClient is published on window", () => {
    assert.ok(WorkflowClient);
});

// ---------------------------------------------------------------------------
// normalizeTurn — must survive anything the server sends
// ---------------------------------------------------------------------------

test("normalizeTurn passes through a complete payload", () => {
    const turn = WorkflowClient.normalizeTurn(turnPayload());
    assert.equal(turn.messages.length, 1);
    assert.equal(turn.progress.section_total, 3);
    assert.equal(turn.status, "in_progress");
});

test("normalizeTurn fills in every field the renderer indexes", () => {
    const turn = WorkflowClient.normalizeTurn({});
    assert.deepEqual(turn.messages, []);
    assert.deepEqual(turn.choices, []);
    assert.equal(turn.progress.percent, 0);
    assert.equal(turn.progress.section_title, "");
    assert.equal(turn.status, "in_progress");
    assert.equal(turn.document, null);
});

test("normalizeTurn tolerates null and non-object input", () => {
    for (const input of [null, undefined, "nope", 42]) {
        const turn = WorkflowClient.normalizeTurn(input);
        assert.deepEqual(turn.messages, []);
    }
});

test("normalizeTurn drops malformed messages", () => {
    const turn = WorkflowClient.normalizeTurn({
        messages: [null, { role: "model" }, { content: 5 }, { role: "model", content: "ok" }],
    });
    assert.deepEqual(turn.messages, [{ role: "model", content: "ok" }]);
});

test("normalizeTurn coerces an unknown role to model", () => {
    const turn = WorkflowClient.normalizeTurn({
        messages: [{ role: "system", content: "x" }],
    });
    assert.equal(turn.messages[0].role, "model");
});

test("normalizeTurn ignores a non-array choices field", () => {
    assert.deepEqual(WorkflowClient.normalizeTurn({ choices: "skip" }).choices, []);
});

// ---------------------------------------------------------------------------
// start / send
// ---------------------------------------------------------------------------

test("start posts the workflow id and language", async () => {
    const stub = reset();
    await WorkflowClient.start("wf", "French");
    assert.equal(stub.calls[0].url, "/api/workflow/start");
    assert.deepEqual(stub.calls[0].body, { workflow_id: "wf", language: "French" });
});

test("start persists the run and marks it active", async () => {
    reset();
    await WorkflowClient.start("wf", "English");
    assert.equal(WorkflowClient.activeWorkflowId(), "wf");
    const run = WorkflowClient.getRun("wf");
    assert.equal(run.workflowId, "wf");
    assert.equal(run.messages.length, 1);
    assert.equal(run.status, "in_progress");
});

test("send posts the persisted state back to the server", async () => {
    const stub = reset();
    await WorkflowClient.start("wf", "English");
    await WorkflowClient.send("wf", "Jo Blake", "English");
    const body = stub.calls[1].body;
    assert.equal(stub.calls[1].url, "/api/workflow/turn");
    assert.equal(body.message, "Jo Blake");
    assert.equal(body.state.spec_hash, "abc123def456",
        "the round-tripped state is what the stateless server needs");
});

test("send records the user turn in the transcript", async () => {
    reset();
    await WorkflowClient.start("wf", "English");
    await WorkflowClient.send("wf", "Jo Blake", "English");
    const run = WorkflowClient.getRun("wf");
    const roles = run.messages.map(m => m.role);
    assert.deepEqual(roles, ["model", "user", "model"]);
    assert.equal(run.messages[1].content, "Jo Blake");
});

test("the transcript is plain {role, content} so the chat renderer can replay it", async () => {
    reset();
    await WorkflowClient.start("wf", "English");
    await WorkflowClient.send("wf", "Hi", "English");
    for (const message of WorkflowClient.getRun("wf").messages) {
        assert.deepEqual(Object.keys(message).sort(), ["content", "role"]);
    }
});

test("send rejects when there is no run in progress", async () => {
    reset();
    await assert.rejects(() => WorkflowClient.send("wf", "hi", "English"),
        /no run in progress/);
});

test("start clears a previous transcript for the same workflow", async () => {
    reset();
    await WorkflowClient.start("wf", "English");
    await WorkflowClient.send("wf", "first", "English");
    await WorkflowClient.start("wf", "English");
    const run = WorkflowClient.getRun("wf");
    assert.equal(run.messages.length, 1, "a restart begins a fresh transcript");
});

// ---------------------------------------------------------------------------
// Completion
// ---------------------------------------------------------------------------

test("completing a run stores the document and clears the active marker", async () => {
    reset([{ json: turnPayload({
        status: "complete",
        document: { format: "markdown", filename: "out.md", body: "# Done" },
    }) }]);
    await WorkflowClient.start("wf", "English");
    assert.equal(WorkflowClient.activeWorkflowId(), null,
        "a finished run must not resume on next load");
    assert.equal(WorkflowClient.getRun("wf").document.body, "# Done");
    assert.equal(WorkflowClient.isActive(), false);
});

test("submit posts the state and the email flag", async () => {
    const stub = reset([
        { json: turnPayload() },
        { json: { saved: true, emailed: false } },
    ]);
    await WorkflowClient.start("wf", "English");
    const result = await WorkflowClient.submit("wf", true);
    assert.equal(stub.calls[1].url, "/api/workflow/submit");
    assert.equal(stub.calls[1].body.email, true);
    assert.equal(result.saved, true);
});

// ---------------------------------------------------------------------------
// Persistence and resume
// ---------------------------------------------------------------------------

test("a run survives a simulated reload", async () => {
    reset();
    await WorkflowClient.start("wf", "English");
    // Simulate a page reload: same storage, freshly evaluated module.
    new Function("window", source)(globalThis.window);
    const Reloaded = globalThis.window.WorkflowClient;
    Reloaded.configure({ apiBase: "", storagePrefix: "test_prefix" });
    assert.equal(Reloaded.activeWorkflowId(), "wf");
    assert.equal(Reloaded.getRun("wf").messages.length, 1);
});

test("a run written by an older version is discarded, not misread", () => {
    localStorage.clear();
    WorkflowClient.__resetCache();
    WorkflowClient.configure({ storagePrefix: "test_prefix" });
    localStorage.setItem("test_prefix_workflow_runs",
        JSON.stringify({ wf: { v: 0, workflowId: "wf", messages: [] } }));
    assert.equal(WorkflowClient.getRun("wf"), null);
});

test("corrupted run storage resets instead of throwing", () => {
    localStorage.clear();
    WorkflowClient.__resetCache();
    WorkflowClient.configure({ storagePrefix: "test_prefix" });
    localStorage.setItem("test_prefix_workflow_runs", "{not json");
    assert.doesNotThrow(() => WorkflowClient.getRun("wf"));
    assert.equal(WorkflowClient.getRun("wf"), null);
});

test("clearRun removes the run and the active marker", async () => {
    reset();
    await WorkflowClient.start("wf", "English");
    WorkflowClient.clearRun("wf");
    assert.equal(WorkflowClient.getRun("wf"), null);
    assert.equal(WorkflowClient.activeWorkflowId(), null);
});

test("a failing localStorage write falls back to memory rather than losing the turn", async () => {
    reset();
    const original = localStorage.setItem.bind(localStorage);
    localStorage.setItem = () => { throw new Error("QuotaExceededError"); };
    try {
        await WorkflowClient.start("wf", "English");
        assert.equal(WorkflowClient.getRun("wf").workflowId, "wf");
    } finally {
        localStorage.setItem = original;
    }
    WorkflowClient.__resetCache();
});

test("listRuns returns runs newest first", async () => {
    reset();
    await WorkflowClient.start("wf", "English");
    const runs = WorkflowClient.listRuns();
    assert.equal(runs.length, 1);
    assert.equal(runs[0].workflowId, "wf");
});

// ---------------------------------------------------------------------------
// list
// ---------------------------------------------------------------------------

test("list returns the workflows array", async () => {
    reset([{ json: { workflows: [{ id: "wf", title: "A workflow" }] } }]);
    assert.deepEqual(await WorkflowClient.list(), [{ id: "wf", title: "A workflow" }]);
});

test("list caches so the launcher does not refetch on every render", async () => {
    const stub = reset([{ json: { workflows: [{ id: "wf" }] } }]);
    await WorkflowClient.list();
    await WorkflowClient.list();
    assert.equal(stub.calls.length, 1);
});

test("list returns an empty array when the request fails", async () => {
    localStorage.clear();
    WorkflowClient.__resetCache();
    WorkflowClient.configure({
        storagePrefix: "test_prefix",
        fetchImpl: () => Promise.reject(new Error("offline")),
    });
    assert.deepEqual(await WorkflowClient.list(), [],
        "no workflows configured is a normal state, not an error to surface");
});

test("list returns an empty array on a malformed body", async () => {
    reset([{ json: { nope: true } }]);
    assert.deepEqual(await WorkflowClient.list(), []);
});

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

test("an HTTP error without a state rejects", async () => {
    reset([{ ok: false, status: 400, json: { error: "state_version" } }]);
    await assert.rejects(() => WorkflowClient.start("wf", "English"), /state_version/);
});

test("a 409 carrying a migrated state resolves rather than rejecting", async () => {
    // The spec changed mid-run; the server returns the migrated state so the
    // interview can continue rather than being thrown away.
    reset([{ ok: false, status: 409, json: turnPayload({
        messages: [{ role: "model", content: "This questionnaire was updated." }],
    }) }]);
    const turn = await WorkflowClient.start("wf", "English");
    assert.equal(turn.messages[0].content, "This questionnaire was updated.");
});

test("an unparseable response body rejects with the status", async () => {
    localStorage.clear();
    WorkflowClient.__resetCache();
    WorkflowClient.configure({
        storagePrefix: "test_prefix",
        fetchImpl: () => Promise.resolve({
            ok: false, status: 500, json: () => Promise.reject(new Error("no body")),
        }),
    });
    await assert.rejects(() => WorkflowClient.start("wf", "English"), /HTTP 500/);
});

// ---------------------------------------------------------------------------
// progressLabel
// ---------------------------------------------------------------------------

test("progressLabel renders a one-based section number", () => {
    const label = WorkflowClient.progressLabel(
        { section_index: 1, section_total: 5, percent: 40 });
    assert.equal(label, "Section 2 of 5 · 40%");
});

test("progressLabel honours a translated template", () => {
    const label = WorkflowClient.progressLabel(
        { section_index: 0, section_total: 3, percent: 10 },
        "Sección {n} de {total} · {percent} %");
    assert.equal(label, "Sección 1 de 3 · 10 %");
});

test("progressLabel supports answered and question counts", () => {
    const label = WorkflowClient.progressLabel(
        { section_index: 0, section_total: 2, answered: 4, total: 9 },
        "{answered}/{questions}");
    assert.equal(label, "4/9");
});

test("progressLabel tolerates a missing progress object", () => {
    assert.equal(WorkflowClient.progressLabel(null), "Section 1 of 0 · 0%");
});
