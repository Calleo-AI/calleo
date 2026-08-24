// ---------------------------------------------------------------------------
// WorkflowClient — drives a guided questionnaire against the workflow API.
//
// Published as `window.WorkflowClient`. DOM-free (and with an injectable fetch)
// so it can be unit-tested in Node the same way chat_history_store.js is.
//
// The server holds no session: the workflow state blob travels with every
// request, exactly as chat history does. This module owns that round trip and
// mirrors each run into localStorage, which is what lets someone close the tab
// mid-interview and resume on the same question days later.
// ---------------------------------------------------------------------------
(function (window) {
"use strict";

const WorkflowClient = (() => {
    const RUN_VERSION = 1;

    let apiBase = "";
    let storagePrefix = "site_chatbot";
    let fetchImpl = null;

    // Populated when localStorage writes throw (private mode, quota), so a run
    // still works for the life of the page instead of failing outright.
    let memoryRuns = null;

    const configure = (options) => {
        const opts = options || {};
        if (opts.apiBase !== undefined) apiBase = opts.apiBase || "";
        if (opts.storagePrefix) storagePrefix = opts.storagePrefix;
        if (opts.fetchImpl) fetchImpl = opts.fetchImpl;
    };

    const doFetch = (...args) => {
        const impl = fetchImpl || (typeof window.fetch === "function" ? window.fetch.bind(window) : null);
        if (!impl) return Promise.reject(new Error("no fetch available"));
        return impl(...args);
    };

    const runsKey = () => `${storagePrefix}_workflow_runs`;
    const activeKey = () => `${storagePrefix}_workflow_active`;
    const docsKey = () => `${storagePrefix}_workflow_documents`;

    const readRuns = () => {
        if (memoryRuns) return memoryRuns;
        try {
            const raw = localStorage.getItem(runsKey());
            if (!raw) return {};
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== "object") return {};
            // Drop anything written by an older shape rather than trying to
            // interpret it — a half-understood run is worse than a fresh start.
            const out = {};
            for (const [id, run] of Object.entries(parsed)) {
                if (run && run.v === RUN_VERSION) out[id] = run;
            }
            return out;
        } catch (e) {
            console.warn("[WorkflowClient] corrupted run storage, resetting:", e);
            return {};
        }
    };

    const writeRuns = (runs) => {
        if (memoryRuns) { memoryRuns = runs; return; }
        try {
            localStorage.setItem(runsKey(), JSON.stringify(runs));
        } catch (e) {
            console.warn("[WorkflowClient] run storage write failed, switching to memory");
            memoryRuns = runs;
        }
    };

    /**
     * Coerce a server response into a complete turn shape.
     *
     * Every consumer downstream indexes into these fields, so a response that
     * is partial (an error envelope, a truncated body, an older server) must
     * not produce undefined dereferences deep in the render path.
     */
    const normalizeTurn = (json) => {
        const data = json && typeof json === "object" ? json : {};
        const messages = Array.isArray(data.messages)
            ? data.messages
                .filter(m => m && typeof m.content === "string")
                .map(m => ({ role: m.role === "user" ? "user" : "model", content: m.content }))
            : [];
        const progress = data.progress && typeof data.progress === "object" ? data.progress : {};
        return {
            state: data.state || null,
            messages,
            choices: Array.isArray(data.choices) ? data.choices : [],
            progress: {
                section_index: progress.section_index || 0,
                section_total: progress.section_total || 0,
                section_title: progress.section_title || "",
                answered: progress.answered || 0,
                total: progress.total || 0,
                percent: progress.percent || 0,
            },
            status: data.status || "in_progress",
            document: data.document || null,
        };
    };

    const getRun = (workflowId) => readRuns()[workflowId] || null;

    const saveRun = (workflowId, turn, extraMessages) => {
        const runs = readRuns();
        const existing = runs[workflowId];
        // The transcript is stored as plain {role, content} pairs so the widget's
        // existing renderer can replay it untouched on reload.
        const transcript = (existing && Array.isArray(existing.messages))
            ? existing.messages.slice()
            : [];
        (extraMessages || []).forEach(m => transcript.push(m));
        turn.messages.forEach(m => transcript.push(m));

        runs[workflowId] = {
            v: RUN_VERSION,
            workflowId,
            state: turn.state,
            messages: transcript,
            choices: turn.choices,
            progress: turn.progress,
            status: turn.status,
            document: turn.document || (existing && existing.document) || null,
            updatedAt: Date.now(),
        };
        writeRuns(runs);
        setActive(turn.status === "complete" ? null : workflowId);
        return runs[workflowId];
    };

    const clearRun = (workflowId) => {
        const runs = readRuns();
        delete runs[workflowId];
        writeRuns(runs);
        if (activeWorkflowId() === workflowId) setActive(null);
    };

    const listRuns = () => Object.values(readRuns()).sort((a, b) => b.updatedAt - a.updatedAt);

    const setActive = (workflowId) => {
        try {
            if (workflowId) localStorage.setItem(activeKey(), workflowId);
            else localStorage.removeItem(activeKey());
        } catch (_) { /* ignore */ }
    };

    const activeWorkflowId = () => {
        try { return localStorage.getItem(activeKey()); } catch (_) { return null; }
    };

    const isActive = () => Boolean(activeWorkflowId() && getRun(activeWorkflowId()));

    const postJson = (path, body) =>
        doFetch(apiBase + path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        }).then(async (response) => {
            let json = null;
            try { json = await response.json(); } catch (_) { json = null; }
            if (!response.ok && !(json && json.state)) {
                const err = new Error((json && json.error) || `HTTP ${response.status}`);
                err.status = response.status;
                throw err;
            }
            return normalizeTurn(json);
        });

    let cachedList = null;

    const list = () => {
        if (cachedList) return Promise.resolve(cachedList);
        return doFetch(apiBase + "/api/workflows")
            .then(r => (r.ok ? r.json() : { workflows: [] }))
            .then(data => {
                cachedList = Array.isArray(data && data.workflows) ? data.workflows : [];
                return cachedList;
            })
            .catch(() => []);   // no workflows configured is a normal state, not an error
    };

    const start = (workflowId, language) =>
        postJson("/api/workflow/start", { workflow_id: workflowId, language })
            .then(turn => {
                // A fresh start replaces any previous transcript for this workflow.
                const runs = readRuns();
                delete runs[workflowId];
                writeRuns(runs);
                saveRun(workflowId, turn);
                return turn;
            });

    const send = (workflowId, text, language) => {
        const run = getRun(workflowId);
        if (!run) return Promise.reject(new Error("no run in progress"));
        return postJson("/api/workflow/turn", {
            workflow_id: workflowId,
            state: run.state,
            message: text,
            language,
        }).then(turn => {
            saveRun(workflowId, turn, [{ role: "user", content: text }]);
            return turn;
        });
    };

    const requestDocument = (workflowId) => {
        const run = getRun(workflowId);
        if (!run) return Promise.reject(new Error("no run in progress"));
        return doFetch(apiBase + "/api/workflow/document", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ workflow_id: workflowId, state: run.state }),
        })
            .then(r => r.json())
            .then(data => (data && data.document) || null);
    };

    const submit = (workflowId, wantEmail) => {
        const run = getRun(workflowId);
        if (!run) return Promise.reject(new Error("no run in progress"));
        return doFetch(apiBase + "/api/workflow/submit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                workflow_id: workflowId,
                state: run.state,
                email: Boolean(wantEmail),
            }),
        }).then(r => r.json());
    };

    const progressLabel = (progress, template) => {
        const p = progress || {};
        const text = template || "Section {n} of {total} · {percent}%";
        return text
            .replace("{n}", String((p.section_index || 0) + 1))
            .replace("{total}", String(p.section_total || 0))
            .replace("{percent}", String(p.percent || 0))
            .replace("{answered}", String(p.answered || 0))
            .replace("{questions}", String(p.total || 0));
    };

    return {
        configure, list, start, send, requestDocument, submit,
        getRun, saveRun, clearRun, listRuns,
        isActive, activeWorkflowId, setActive,
        normalizeTurn, progressLabel,
        RUN_VERSION,
        // Test seam: drop the cached /api/workflows response.
        __resetCache: () => { cachedList = null; memoryRuns = null; },
    };
})();

window.WorkflowClient = WorkflowClient;

})(typeof window !== "undefined" ? window : globalThis);
