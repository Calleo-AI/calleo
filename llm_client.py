"""
llm_client.py — the single provider seam for all LLM and embedding calls.

Every model call in this project — chat completions AND embeddings — goes
through OpenRouter, using the OpenAI SDK pointed at OpenRouter's base URL.
This is the ONLY module that imports that SDK, and the ONLY place model IDs and
provider-specific request syntax (e.g. OpenRouter's ``extra_body`` reasoning
fields) appear. Every other module goes through ``chat()`` and
``get_embedding_function()``.

Why it exists: a future provider migration (e.g. to Vertex AI) should be a
localized rewrite of THIS file only. Call sites build their own prompts, parse
their own output, and keep their own fallbacks — they never see the provider.

Configuration (single source of truth, each env-overridable):

    role        default model                  env override
    ----------  -----------------------------  --------------
    chat        qwen/qwen3.5-397b-a17b          CHAT_MODEL
    analysis    deepseek/deepseek-v4-flash      ANALYSIS_MODEL
    judge       deepseek/deepseek-v4-flash      JUDGE_MODEL
    embedding   google/gemini-embedding-001     EMBED_MODEL

All of them are OpenRouter model ids and need only OPENROUTER_API_KEY.
"""
import os

from chromadb.api.types import EmbeddingFunction
from chromadb.utils.embedding_functions import register_embedding_function
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_RETRIES = 3  # provider-side transient failures, retried by the OpenAI SDK

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
    return os.environ.get("EMBED_MODEL", "google/gemini-embedding-001")


# --- Lazy provider singletons -------------------------------------------------
# Both are lazy so nothing constructs a provider client (or reads the API key)
# at import time — a test or a one-off script can import this module without
# credentials as long as it never actually calls out.

_client = None
_embedding_function = None


def _get_client():
    """Return the shared OpenAI (OpenRouter) client, constructing it on first use.

    The ``openai`` import is deferred to here so importing this module stays
    cheap for consumers that only need model-id lookups (e.g. dashboard_data).
    """
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=os.environ.get("OPENROUTER_API_KEY"),
            # Explicit: the SDK retries connection errors, 429s and 5xx with
            # backoff. Embedding a full rebuild is thousands of requests, so
            # the transient-failure handling lives here rather than per caller.
            max_retries=MAX_RETRIES,
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


# --- Embeddings ---------------------------------------------------------------
# Sent to OpenRouter's OpenAI-compatible /embeddings endpoint through the same
# client as chat, so there is exactly one provider and one API key.

EMBED_BATCH_SIZE = 20   # texts per request; ChromaDB can hand us far more


@register_embedding_function
class OpenRouterEmbeddingFunction(EmbeddingFunction):
    """ChromaDB-compatible embedding function backed by OpenRouter.

    ChromaDB calls this object with a list of texts (documents on write, the
    query on read) and expects one vector per text, in order.
    """

    def __init__(self, model_name=None):
        self._model_name = model_name or embed_model()

    @staticmethod
    def name():
        """Identifier ChromaDB persists in the collection configuration."""
        return "openrouter"

    @property
    def model_name(self):
        return self._model_name

    def default_space(self):
        """Distance metric for new collections.

        Cosine, matching what the collections were originally built with — a
        different default here would silently change retrieval ranking.
        """
        return "cosine"

    def __call__(self, input):
        texts = [input] if isinstance(input, str) else list(input)
        embeddings = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            embeddings.extend(self._embed(texts[start:start + EMBED_BATCH_SIZE]))
        return embeddings

    def _embed(self, texts):
        """Embed one batch of texts."""
        response = _get_client().embeddings.create(
            model=self._model_name, input=texts
        )
        # The endpoint may return items out of order; ``index`` is authoritative.
        items = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in items]

    def get_config(self):
        """Serializable config ChromaDB stores alongside the collection."""
        return {"model_name": self._model_name}

    @staticmethod
    def build_from_config(config):
        return OpenRouterEmbeddingFunction(model_name=config.get("model_name"))


def get_embedding_function():
    """Return the shared Chroma-compatible embedding function (singleton).

    ChromaDB invokes this object to embed documents and queries; centralizing
    its construction here means a provider migration swaps it in one place.
    """
    global _embedding_function
    if _embedding_function is None:
        _embedding_function = OpenRouterEmbeddingFunction()
    return _embedding_function
