"""
llm_client.py — the single provider seam for all LLM and embedding calls.

This is the ONLY module that imports the OpenAI SDK or the Gemini embedding
provider, and the ONLY place model IDs and provider-specific request syntax
(e.g. OpenRouter's ``extra_body`` reasoning fields) appear. Every other module
goes through ``chat()`` and ``get_embedding_function()``.

Why it exists: a future migration to Vertex AI (the whole project moving under
one Google Cloud project) should be a localized rewrite of THIS file only. Call
sites build their own prompts, parse their own output, and keep their own
fallbacks — they never see the provider.

Configuration (single source of truth, each env-overridable):

    role        default model                  env override
    ----------  -----------------------------  --------------
    chat        qwen/qwen3.5-397b-a17b          CHAT_MODEL
    analysis    deepseek/deepseek-v4-flash      ANALYSIS_MODEL
    judge       deepseek/deepseek-v4-flash      JUDGE_MODEL
    embedding   gemini-embedding-001            EMBED_MODEL
"""
import os

import chromadb.utils.embedding_functions as embedding_functions
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# role -> (env var, default model). Resolved at call time so an env override
# (or a test monkeypatching the environment) takes effect without reimport.
_MODEL_ENV = {
    "chat": "CHAT_MODEL",
    "analysis": "ANALYSIS_MODEL",
    "judge": "JUDGE_MODEL",
}
_MODEL_DEFAULTS = {
    "chat": "qwen/qwen3.5-397b-a17b",
    "analysis": "deepseek/deepseek-v4-flash",
    "judge": "deepseek/deepseek-v4-flash",
}


def model_for(role):
    """Resolve a role to a concrete model id (env override wins over default)."""
    try:
        env_var = _MODEL_ENV[role]
    except KeyError:
        raise ValueError(
            f"Unknown role: {role!r} (expected one of {sorted(_MODEL_ENV)})"
        )
    return os.environ.get(env_var, _MODEL_DEFAULTS[role])


def embed_model():
    """Resolve the embedding model id (env override wins over default)."""
    return os.environ.get("EMBED_MODEL", "gemini-embedding-001")


# --- Lazy provider singletons -------------------------------------------------
# Both are lazy so embedding-only consumers (the DB pipeline, one-off scripts)
# never construct the chat client and never need OPENROUTER_API_KEY, and the
# chat path never constructs an embedding function it won't use.

_client = None
_embedding_function = None


def _get_client():
    """Return the shared OpenAI (OpenRouter) client, constructing it on first use.

    The ``openai`` import is deferred to here so embedding-only consumers (the DB
    crawl/rebuild pipeline and one-off scripts) never import openai or need
    OPENROUTER_API_KEY just to build an embedding function.
    """
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=os.environ.get("OPENROUTER_API_KEY"),
        )
    return _client


def _extra_body(reasoning):
    """Translate the semantic ``reasoning`` argument into a provider request body.

    None  -> no extra_body at all (provider default reasoning)
    "off" / False -> disable model reasoning tokens
    "high"        -> high reasoning effort
    """
    if reasoning is None:
        return None
    if reasoning is False or reasoning == "off":
        return {"reasoning": {"enabled": False}}
    if reasoning == "high":
        return {"reasoning": {"effort": "high"}}
    raise ValueError(
        f"Unknown reasoning: {reasoning!r} (expected None, 'off', False, or 'high')"
    )


def chat(messages, *, role="chat", temperature=None, reasoning=None):
    """Send a chat completion and return the message text ("" if none).

    Provider-agnostic: callers never touch the OpenAI SDK shape. Exceptions are
    propagated so each caller keeps its own tailored fallback behavior.

    role        selects the model (see module docstring table)
    temperature omitted from the request when None; sent verbatim otherwise
                (0 is a real value and is sent)
    reasoning   None | "off"/False | "high" (see _extra_body)
    """
    kwargs = {"model": model_for(role), "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    extra = _extra_body(reasoning)
    if extra is not None:
        kwargs["extra_body"] = extra

    completion = _get_client().chat.completions.create(**kwargs)
    return completion.choices[0].message.content or ""


def get_embedding_function():
    """Return the shared Chroma-compatible Gemini embedding function (singleton).

    ChromaDB invokes this object to embed documents and queries; centralizing its
    construction here means the Vertex migration swaps the provider in one place.
    """
    global _embedding_function
    if _embedding_function is None:
        _embedding_function = embedding_functions.GoogleGenerativeAiEmbeddingFunction(
            api_key=os.environ.get("GEMINI_API_KEY"),
            model_name=embed_model(),
        )
    return _embedding_function
