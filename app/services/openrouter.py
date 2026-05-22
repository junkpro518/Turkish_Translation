import httpx

from app.config import Settings


class OpenRouterError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
        if not self.settings.openrouter_api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not configured")

        payload = {
            "model": model or self.settings.openrouter_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.settings.app_base_url,
            "X-Title": "Arabic Turkish Translation Bot",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)

        if response.status_code >= 400:
            raise OpenRouterError(f"OpenRouter returned {response.status_code}: {response.text[:500]}")

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError(f"Unexpected OpenRouter response shape: {data}") from exc

        if not content:
            raise OpenRouterError("OpenRouter returned an empty response")
        return str(content).strip()
