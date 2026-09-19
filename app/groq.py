"""One place that talks to Groq's OpenAI-compatible chat API.

Handles the free tier's per-minute token limit: on 429, wait for retry-after (if it's
short enough for the caller) and try once more.
"""

import time

import httpx

from app import config


def chat(messages: list, *, max_wait_s: float, timeout: float = 60, **params) -> str:
    """Return the assistant message text. Raises httpx.HTTPError on failure."""
    body = {"model": config.GROQ_MODEL, "messages": messages, **params}
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}
    url = f"{config.GROQ_BASE_URL}/chat/completions"
    response = httpx.post(url, headers=headers, json=body, timeout=timeout)
    if response.status_code == 429:
        wait = _retry_after(response)
        if wait is not None and wait <= max_wait_s:
            time.sleep(wait + 0.25)
            response = httpx.post(url, headers=headers, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()["choices"][0]["message"].get("content") or ""


def _retry_after(response: httpx.Response):
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
