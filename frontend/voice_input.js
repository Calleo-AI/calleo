// ---------------------------------------------------------------------------
// VoiceInput — browser-native speech-to-text for the chat widget.
//
// Published as `window.VoiceInput`. DOM-free so it can be unit-tested in Node by
// evaluating the file in a scope that provides `window` (the same trick
// chat_history_store.js uses).
//
// Backed by the Web Speech API (`SpeechRecognition` / `webkitSpeechRecognition`),
// which runs in the browser and costs nothing: no server round trip, no second
// provider, no second API key. That matters here — OpenRouter is this project's
// only provider and it exposes no audio-transcription endpoint, so a server-side
// path would mean breaking the single-key invariant.
//
// Support is Chrome (desktop + Android), Edge, and Safari (macOS + iOS 14.5+).
// Firefox ships no implementation, so `isSupported()` returns false there and
// the caller simply never renders a mic button.
//
// Privacy note worth surfacing to users: Chrome streams the audio to Google's
// servers for recognition. Safari recognizes on-device. Neither path involves
// this project's backend.
// ---------------------------------------------------------------------------
(function (window) {
"use strict";

const VoiceInput = (() => {
    // Language-name -> BCP-47 tag. The widget's language selector uses English
    // language NAMES as values (see site_config.js welcomeTranslations), but
    // SpeechRecognition wants a BCP-47 tag, so the two need bridging. The map
    // itself is site config; this is only the fallback for a bare `window`
    // (Node tests) or a language with no entry.
    const DEFAULT_SPEECH_LANG = "en-US";

    // Browser error codes are not stable enough to show a user, and they need
    // translating. Map them to keys that site_config.js has strings for.
    const ERROR_KEYS = {
        "not-allowed": "denied",
        "service-not-allowed": "denied",
        "no-speech": "noSpeech",
        "audio-capture": "noMic",
        "network": "network",
        "aborted": "aborted",
    };

    const getRecognitionCtor = () =>
        window.SpeechRecognition || window.webkitSpeechRecognition || null;

    const isSupported = () => Boolean(getRecognitionCtor());

    const speechLangFor = (languageName) => {
        const map = (window.SITE_CONFIG && window.SITE_CONFIG.speechLangs) || {};
        return map[languageName] || DEFAULT_SPEECH_LANG;
    };

    const errorKeyFor = (code) => ERROR_KEYS[code] || "other";

    /**
     * Build a recognizer, or return null when the browser has no Web Speech API.
     *
     * Callbacks:
     *   onInterim(text)  — the in-progress guess, replaced on every event
     *   onFinal(text)    — a settled phrase, appended by the caller
     *   onError(key)     — one of the ERROR_KEYS values (or "other")
     *   onEnd()          — recognition stopped, for any reason
     */
    const create = (opts) => {
        const Ctor = getRecognitionCtor();
        if (!Ctor) return null;

        const options = opts || {};
        const onInterim = options.onInterim || (() => {});
        const onFinal = options.onFinal || (() => {});
        const onError = options.onError || (() => {});
        const onEnd = options.onEnd || (() => {});

        const recognition = new Ctor();
        // continuous: keep listening across pauses, so a long questionnaire
        // answer isn't cut off at the first breath.
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = speechLangFor(options.lang || "English");

        let active = false;
        // Set when the caller asked to stop, so the `end` event that follows is
        // not reported as an unexpected error.
        let stopping = false;

        recognition.onresult = (event) => {
            let interim = "";
            // `resultIndex` is where the new results begin; everything before it
            // has already been dispatched.
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const result = event.results[i];
                const text = result[0] && result[0].transcript ? result[0].transcript : "";
                if (result.isFinal) {
                    if (text.trim()) onFinal(text.trim());
                } else {
                    interim += text;
                }
            }
            onInterim(interim.trim());
        };

        recognition.onerror = (event) => {
            const key = errorKeyFor(event && event.error);
            // "aborted" is what a caller-initiated stop looks like on some
            // browsers — not something to show the user.
            if (key === "aborted" && stopping) return;
            onError(key);
        };

        recognition.onend = () => {
            active = false;
            stopping = false;
            onEnd();
        };

        return {
            start() {
                if (active) return;
                stopping = false;
                try {
                    recognition.start();
                    active = true;
                } catch (e) {
                    // Chrome throws InvalidStateError if start() races a still
                    // -closing session. Surface it as a normal error, don't throw.
                    active = false;
                    onError("other");
                }
            },
            stop() {
                // `active` stays true until the browser fires `end`, so
                // `stopping` is what makes a second call a no-op. Both the send
                // button and the Enter key stop dictation, and they can fire on
                // the same interaction.
                if (!active || stopping) return;
                stopping = true;
                try {
                    recognition.stop();
                } catch (e) {
                    active = false;
                }
            },
            abort() {
                if (!active || stopping) return;
                stopping = true;
                try {
                    recognition.abort();
                } catch (e) {
                    active = false;
                }
            },
            isActive: () => active,
            setLang(languageName) {
                recognition.lang = speechLangFor(languageName);
            },
        };
    };

    return { isSupported, create, speechLangFor, errorKeyFor, DEFAULT_SPEECH_LANG };
})();

window.VoiceInput = VoiceInput;

})(typeof window !== "undefined" ? window : globalThis);
