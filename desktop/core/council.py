import os
import requests

class AICouncil:
    """Provider router. Providers without API keys are simply unavailable."""
    def __init__(self, local_chat):
        self.local_chat = local_chat
        self.providers = {
            "ollama": self._ollama,
            "openai": self._openai,
            "anthropic": self._anthropic,
            "gemini": self._gemini,
        }

    def available(self):
        return {
            "ollama": True,
            "openai": bool(os.getenv("OPENAI_API_KEY")),
            "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
            "gemini": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        }

    def route(self, task_type):
        t = (task_type or "").lower()
        if any(x in t for x in ("kod", "python", "program", "debug")):
            return ["anthropic", "openai", "ollama"]
        if any(x in t for x in ("araştır", "web", "güncel", "kaynak")):
            return ["gemini", "openai", "ollama"]
        return ["openai", "anthropic", "gemini", "ollama"]

    def deliberate(self, prompt, task_type="general"):
        results = []
        for name in self.route(task_type)[:3]:
            if not self.available().get(name):
                continue
            try:
                results.append((name, self.providers[name](prompt)))
            except Exception as exc:
                results.append((name, f"PROVIDER_ERROR: {exc}"))
        if not results:
            return self.local_chat(prompt)
        # The local JARVIS brain synthesizes the council output.
        joined = "\n\n".join(f"[{n.upper()}]\n{r}" for n, r in results)
        return self.local_chat(
            "Aşağıdaki uzman görüşlerini karşılaştır. Çelişkileri belirt, "
            "gerekirse düzelt ve tek uygulanabilir sonuç çıkar.\n\n" + joined
        )

    def _ollama(self, prompt):
        return self.local_chat(prompt)

    def _openai(self, prompt):
        key = os.getenv("OPENAI_API_KEY")
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": os.getenv("OPENAI_MODEL", "gpt-5"), "input": prompt},
            timeout=120
        )
        r.raise_for_status()
        data = r.json()
        return data.get("output_text", "")

    def _anthropic(self, prompt):
        key = os.getenv("ANTHROPIC_API_KEY")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
                  "max_tokens": 3000, "messages":[{"role":"user","content":prompt}]},
            timeout=120
        )
        r.raise_for_status()
        return "".join(x.get("text","") for x in r.json().get("content", []))

    def _gemini(self, prompt):
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = requests.post(url, params={"key": key},
                          json={"contents":[{"parts":[{"text":prompt}]}]}, timeout=120)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
