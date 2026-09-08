import os

import httpx
from dotenv import load_dotenv


# --------------------------------------------------
# Environment
# --------------------------------------------------

load_dotenv()


# --------------------------------------------------
# Configuration
# --------------------------------------------------

def get_ollama_url() -> str:
    """
    Return the configured Ollama server URL.
    """

    return os.getenv(
        "OLLAMA_URL",
        "http://localhost:11434",
    ).strip().rstrip("/")


def get_ollama_model() -> str:
    """
    Return the configured Ollama model.
    """

    return os.getenv(
        "OLLAMA_MODEL",
        "qwen2.5:7b",
    ).strip()


# --------------------------------------------------
# Generate text
# --------------------------------------------------

async def generate_text(
    prompt: str,
) -> str:
    """
    Generate text using the configured Ollama model.

    This function intentionally exposes the same
    generate_text(prompt) interface as other AI
    providers.
    """

    ollama_url = get_ollama_url()
    ollama_model = get_ollama_model()

    if not ollama_url:
        raise RuntimeError(
            "OLLAMA_URL is not configured."
        )

    if not ollama_model:
        raise RuntimeError(
            "OLLAMA_MODEL is not configured."
        )

    payload = {
        "model": ollama_model,
        "prompt": prompt,
        "stream": False,
    }

    try:

        async with httpx.AsyncClient(
            timeout=300.0
        ) as client:

            response = await client.post(
                f"{ollama_url}/api/generate",
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

    except httpx.TimeoutException as error:

        raise RuntimeError(
            "Ollama request timed out."
        ) from error

    except httpx.HTTPError as error:

        raise RuntimeError(
            f"Could not connect to Ollama: {error}"
        ) from error

    result = data.get(
        "response"
    )

    if not result:
        raise RuntimeError(
            "Ollama returned an empty response."
        )

    return result