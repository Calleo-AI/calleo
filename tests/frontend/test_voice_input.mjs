// Unit tests for frontend/voice_input.js.
//
// The Web Speech API does not exist in Node, which is exactly the point: the
// module is written so the recognizer is constructed from `window`, letting a
// fake stand in here and letting the unsupported-browser path be tested too.
//
// Run: node --test tests/frontend/test_voice_input.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { installBrowserGlobals } from "./localstorage_shim.mjs";

installBrowserGlobals();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(
    path.join(__dirname, "..", "..", "frontend", "voice_input.js"), "utf8");

/** Reload the module against the current globalThis.window. */
function loadVoiceInput() {
    new Function("window", source)(globalThis.window);
    return globalThis.window.VoiceInput;
}

/** A stand-in for the browser's SpeechRecognition, with hooks to fire events. */
class FakeRecognition {
    constructor() {
        this.continuous = false;
        this.interimResults = false;
        this.lang = "";
        this.started = 0;
        this.stopped = 0;
        this.aborted = 0;
        FakeRecognition.last = this;
    }
    start() { this.started += 1; }
    stop() { this.stopped += 1; }
    abort() { this.aborted += 1; }

    // --- test helpers -------------------------------------------------------
    emitResult(entries, resultIndex = 0) {
        const results = entries.map(([transcript, isFinal]) => {
            const result = [{ transcript }];
            result.isFinal = isFinal;
            return result;
        });
        results.length = entries.length;
        this.onresult({ resultIndex, results });
    }
    emitError(code) { this.onerror({ error: code }); }
    emitEnd() { this.onend(); }
}

function withSpeechSupport(fn, siteConfig) {
    globalThis.window.SpeechRecognition = FakeRecognition;
    globalThis.window.SITE_CONFIG = siteConfig || {
        speechLangs: { English: "en-US", French: "fr-FR", Chinese: "zh-CN" },
    };
    const VoiceInput = loadVoiceInput();
    try {
        return fn(VoiceInput);
    } finally {
        delete globalThis.window.SpeechRecognition;
        delete globalThis.window.webkitSpeechRecognition;
        delete globalThis.window.SITE_CONFIG;
    }
}

// ---------------------------------------------------------------------------
// Support detection
// ---------------------------------------------------------------------------

test("VoiceInput is published on window", () => {
    assert.ok(loadVoiceInput(), "window.VoiceInput should be defined");
});

test("isSupported is false when the browser has no Web Speech API", () => {
    const VoiceInput = loadVoiceInput();
    assert.equal(VoiceInput.isSupported(), false);
});

test("create returns null when unsupported, so the caller can hide the button", () => {
    const VoiceInput = loadVoiceInput();
    assert.equal(VoiceInput.create({}), null);
});

test("isSupported is true with the standard constructor", () => {
    withSpeechSupport(VoiceInput => {
        assert.equal(VoiceInput.isSupported(), true);
    });
});

test("the webkit-prefixed constructor is accepted too (Safari)", () => {
    globalThis.window.webkitSpeechRecognition = FakeRecognition;
    const VoiceInput = loadVoiceInput();
    assert.equal(VoiceInput.isSupported(), true);
    delete globalThis.window.webkitSpeechRecognition;
});

// ---------------------------------------------------------------------------
// Language mapping
// ---------------------------------------------------------------------------

test("speechLangFor maps a language name to its BCP-47 tag", () => {
    withSpeechSupport(VoiceInput => {
        assert.equal(VoiceInput.speechLangFor("French"), "fr-FR");
        assert.equal(VoiceInput.speechLangFor("Chinese"), "zh-CN");
    });
});

test("speechLangFor falls back to en-US for an unmapped language", () => {
    withSpeechSupport(VoiceInput => {
        assert.equal(VoiceInput.speechLangFor("Klingon"), "en-US");
    });
});

test("speechLangFor falls back when SITE_CONFIG has no speechLangs at all", () => {
    withSpeechSupport(VoiceInput => {
        assert.equal(VoiceInput.speechLangFor("French"), "en-US");
    }, {});
});

test("create applies the language, and setLang changes it", () => {
    withSpeechSupport(VoiceInput => {
        const recognizer = VoiceInput.create({ lang: "French" });
        assert.equal(FakeRecognition.last.lang, "fr-FR");
        recognizer.setLang("Chinese");
        assert.equal(FakeRecognition.last.lang, "zh-CN");
    });
});

test("create configures continuous and interim recognition", () => {
    withSpeechSupport(VoiceInput => {
        VoiceInput.create({});
        assert.equal(FakeRecognition.last.continuous, true,
            "continuous keeps a long answer from being cut off at the first pause");
        assert.equal(FakeRecognition.last.interimResults, true);
    });
});

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

test("interim results are reported separately from final ones", () => {
    withSpeechSupport(VoiceInput => {
        const interim = [];
        const final = [];
        VoiceInput.create({ onInterim: t => interim.push(t), onFinal: t => final.push(t) });
        FakeRecognition.last.emitResult([["what are your ", false]]);
        assert.deepEqual(interim, ["what are your"]);
        assert.deepEqual(final, []);
    });
});

test("a final result is dispatched to onFinal", () => {
    withSpeechSupport(VoiceInput => {
        const final = [];
        VoiceInput.create({ onFinal: t => final.push(t) });
        FakeRecognition.last.emitResult([["What are your opening hours?", true]]);
        assert.deepEqual(final, ["What are your opening hours?"]);
    });
});

test("a mixed batch splits final and interim correctly", () => {
    withSpeechSupport(VoiceInput => {
        const interim = [];
        const final = [];
        VoiceInput.create({ onInterim: t => interim.push(t), onFinal: t => final.push(t) });
        FakeRecognition.last.emitResult([["Hello there.", true], ["and then", false]]);
        assert.deepEqual(final, ["Hello there."]);
        assert.deepEqual(interim, ["and then"]);
    });
});

test("a whitespace-only final result is ignored", () => {
    withSpeechSupport(VoiceInput => {
        const final = [];
        VoiceInput.create({ onFinal: t => final.push(t) });
        FakeRecognition.last.emitResult([["   ", true]]);
        assert.deepEqual(final, []);
    });
});

test("results before resultIndex are not re-dispatched", () => {
    withSpeechSupport(VoiceInput => {
        const final = [];
        VoiceInput.create({ onFinal: t => final.push(t) });
        FakeRecognition.last.emitResult([["old", true], ["new", true]], 1);
        assert.deepEqual(final, ["new"], "only results from resultIndex onward count");
    });
});

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

test("error codes map to stable translatable keys", () => {
    withSpeechSupport(VoiceInput => {
        assert.equal(VoiceInput.errorKeyFor("not-allowed"), "denied");
        assert.equal(VoiceInput.errorKeyFor("service-not-allowed"), "denied");
        assert.equal(VoiceInput.errorKeyFor("no-speech"), "noSpeech");
        assert.equal(VoiceInput.errorKeyFor("audio-capture"), "noMic");
        assert.equal(VoiceInput.errorKeyFor("network"), "network");
        assert.equal(VoiceInput.errorKeyFor("something-new"), "other");
    });
});

test("a permission denial surfaces as the denied key", () => {
    withSpeechSupport(VoiceInput => {
        const errors = [];
        VoiceInput.create({ onError: k => errors.push(k) });
        FakeRecognition.last.emitError("not-allowed");
        assert.deepEqual(errors, ["denied"]);
    });
});

test("an abort caused by our own stop() is not reported as an error", () => {
    withSpeechSupport(VoiceInput => {
        const errors = [];
        const recognizer = VoiceInput.create({ onError: k => errors.push(k) });
        recognizer.start();
        recognizer.stop();
        FakeRecognition.last.emitError("aborted");
        assert.deepEqual(errors, [], "a user-initiated stop is not a failure");
    });
});

test("an unsolicited abort IS reported", () => {
    withSpeechSupport(VoiceInput => {
        const errors = [];
        const recognizer = VoiceInput.create({ onError: k => errors.push(k) });
        recognizer.start();
        FakeRecognition.last.emitError("aborted");
        assert.deepEqual(errors, ["aborted"]);
    });
});

test("a throwing start() surfaces an error instead of propagating", () => {
    withSpeechSupport(VoiceInput => {
        const errors = [];
        const recognizer = VoiceInput.create({ onError: k => errors.push(k) });
        FakeRecognition.last.start = () => { throw new Error("InvalidStateError"); };
        assert.doesNotThrow(() => recognizer.start());
        assert.deepEqual(errors, ["other"]);
        assert.equal(recognizer.isActive(), false);
    });
});

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

test("start marks the recognizer active and calls through once", () => {
    withSpeechSupport(VoiceInput => {
        const recognizer = VoiceInput.create({});
        recognizer.start();
        assert.equal(recognizer.isActive(), true);
        assert.equal(FakeRecognition.last.started, 1);
    });
});

test("start is idempotent while already listening", () => {
    withSpeechSupport(VoiceInput => {
        const recognizer = VoiceInput.create({});
        recognizer.start();
        recognizer.start();
        assert.equal(FakeRecognition.last.started, 1);
    });
});

test("stop on an inactive recognizer is a no-op", () => {
    withSpeechSupport(VoiceInput => {
        const recognizer = VoiceInput.create({});
        recognizer.stop();
        assert.equal(FakeRecognition.last.stopped, 0);
    });
});

test("stop twice only stops once", () => {
    withSpeechSupport(VoiceInput => {
        const recognizer = VoiceInput.create({});
        recognizer.start();
        recognizer.stop();
        recognizer.stop();
        assert.equal(FakeRecognition.last.stopped, 1);
    });
});

test("the end event clears active state and fires onEnd", () => {
    withSpeechSupport(VoiceInput => {
        let ended = 0;
        const recognizer = VoiceInput.create({ onEnd: () => { ended += 1; } });
        recognizer.start();
        FakeRecognition.last.emitEnd();
        assert.equal(ended, 1);
        assert.equal(recognizer.isActive(), false);
    });
});

test("a recognizer can be restarted after ending", () => {
    withSpeechSupport(VoiceInput => {
        const recognizer = VoiceInput.create({});
        recognizer.start();
        FakeRecognition.last.emitEnd();
        recognizer.start();
        assert.equal(recognizer.isActive(), true);
        assert.equal(FakeRecognition.last.started, 2);
    });
});

test("missing callbacks do not throw", () => {
    withSpeechSupport(VoiceInput => {
        const recognizer = VoiceInput.create({});
        assert.doesNotThrow(() => {
            FakeRecognition.last.emitResult([["hi", true], ["there", false]]);
            FakeRecognition.last.emitError("network");
            FakeRecognition.last.emitEnd();
        });
        assert.equal(recognizer.isActive(), false);
    });
});
