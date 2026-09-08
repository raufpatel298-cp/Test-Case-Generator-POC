import os

from dotenv import load_dotenv
from google import genai


# --------------------------------------------------
# Environment
# --------------------------------------------------

load_dotenv()


# --------------------------------------------------
# Configuration
# --------------------------------------------------

def get_gemini_api_key() -> str:
    """
    Return the configured Gemini API key.
    """

    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    return api_key


def get_gemini_model() -> str:
    """
    Return the configured Gemini model.
    """

    return os.getenv(
        "GEMINI_MODEL",
        "gemini-3.5-flash-lite",
    ).strip()


# --------------------------------------------------
# Gemini Client
# --------------------------------------------------

def get_gemini_client() -> genai.Client:
    """
    Create a Gemini client using the configured API key.
    """

    return genai.Client(
        api_key=get_gemini_api_key()
    )


# --------------------------------------------------
# Generate text
# --------------------------------------------------

async def generate_text(
    prompt: str,
) -> str:
    """
    Generate text using the configured Gemini model.

    This function intentionally exposes the same
    generate_text(prompt) interface as every other
    AI provider.
    """

    client = get_gemini_client()

    model = get_gemini_model()

    response = (
        await client.aio.models.generate_content(
            model=model,
            contents=prompt,
        )
    )

    if not response.text:
        raise RuntimeError(
            "Gemini returned an empty response."
        )

    return response.text