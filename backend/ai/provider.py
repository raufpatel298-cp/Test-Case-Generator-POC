import os
from collections.abc import Awaitable, Callable

from dotenv import load_dotenv

from ai.gemini import generate_text as generate_gemini_text
from ai.ollama import generate_text as generate_ollama_text


# --------------------------------------------------
# Environment
# --------------------------------------------------

load_dotenv()


# --------------------------------------------------
# Provider type
# --------------------------------------------------

ProviderFunction = Callable[
    [str],
    Awaitable[str],
]


# --------------------------------------------------
# Provider registry
# --------------------------------------------------

PROVIDERS: dict[
    str,
    ProviderFunction,
] = {
    "gemini": generate_gemini_text,
    "ollama": generate_ollama_text,
}


# --------------------------------------------------
# Provider configuration
# --------------------------------------------------

def get_ai_provider() -> str:
    """
    Return the currently configured AI provider.

    The provider is selected using:

        AI_PROVIDER=gemini

    or:

        AI_PROVIDER=ollama
    """

    provider = os.getenv(
        "AI_PROVIDER",
        "gemini",
    ).strip().lower()

    if provider not in PROVIDERS:
        supported = ", ".join(
            sorted(PROVIDERS.keys())
        )

        raise RuntimeError(
            f"Unsupported AI_PROVIDER: {provider}. "
            f"Supported providers: {supported}"
        )

    return provider


def get_ai_model() -> str:
    """
    Return the model configured for the
    currently selected provider.
    """

    provider = get_ai_provider()

    if provider == "gemini":
        return os.getenv(
            "GEMINI_MODEL",
            "gemini-3.5-flash-lite",
        ).strip()

    if provider == "ollama":
        return os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5:7b",
        ).strip()

    # Defensive fallback.
    raise RuntimeError(
        f"No model configuration exists for provider: {provider}"
    )


# --------------------------------------------------
# Supported providers
# --------------------------------------------------

def get_supported_providers() -> list[str]:
    """
    Return the list of registered AI providers.
    """

    return sorted(
        PROVIDERS.keys()
    )


# --------------------------------------------------
# Dynamic text generation
# --------------------------------------------------

async def generate_text(
    prompt: str,
) -> str:
    """
    Generate text using the configured AI provider.

    The rest of the application should call only
    this function.

    Provider-specific implementations remain inside
    their own modules.
    """

    provider = get_ai_provider()

    generator = PROVIDERS.get(
        provider
    )

    if generator is None:
        raise RuntimeError(
            f"AI provider '{provider}' is not registered."
        )

    return await generator(
        prompt
    )