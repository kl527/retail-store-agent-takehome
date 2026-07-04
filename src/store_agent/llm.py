"""Chat-completions client for Cloudflare Workers AI.

Talks to the OpenAI-compatible endpoint
(https://api.cloudflare.com/client/v4/accounts/<id>/ai/v1), so any
OpenAI-compatible base URL works as a drop-in via LLM_BASE_URL.
"""

import time

import httpx


class LLMError(RuntimeError):
    pass


class ChatClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0):
        self.model = model
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        """One chat-completions call; returns the assistant message dict."""
        payload = {"model": self.model, "messages": messages}
        if tools:  # some providers reject an empty tools array
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        last_error = None
        for attempt in range(3):
            if attempt:
                time.sleep(2 ** attempt)
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.HTTPError as e:
                last_error = LLMError(f"Request failed: {e}")
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
                continue
            if response.status_code != 200:
                raise LLMError(f"HTTP {response.status_code}: {response.text[:500]}")
            body = response.json()
            try:
                return body["choices"][0]["message"]
            except (KeyError, IndexError):
                raise LLMError(f"Unexpected response shape: {str(body)[:500]}")
        raise last_error
