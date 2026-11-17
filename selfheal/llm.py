"""Çok sağlayıcılı LLM katmanı (eski JARVIS AI Konseyi'nden uyarlandı).

Sırayla dener: Anthropic -> OpenAI -> Gemini -> Ollama (yerel). Anahtarı olmayan
sağlayıcı atlanır. Hiçbir ek paket gerektirmez; `anthropic` paketi kuruluysa onu kullanır.
"""
import json
import os
import urllib.request


class LLMError(RuntimeError):
    pass


def _post_json(url, payload, headers, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **headers}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class LLM:
    def __init__(self, settings):
        self.s = settings

    def available(self):
        return {
            "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
            "openai": bool(os.getenv("OPENAI_API_KEY")),
            "gemini": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
            "ollama": True,
        }

    def complete(self, prompt, system=""):
        """(metin, sağlayıcı) döndürür; hepsi başarısızsa LLMError."""
        errors = []
        avail = self.available()
        for name in self.s.llm_order:
            if not avail.get(name):
                continue
            try:
                text = getattr(self, "_" + name)(prompt, system)
                if text and text.strip():
                    return text, name
                errors.append(f"{name}: boş cevap")
            except Exception as exc:  # sağlayıcı hatası -> sıradakine geç
                errors.append(f"{name}: {exc}")
        raise LLMError("Hiçbir LLM cevap vermedi: " + " | ".join(errors))

    # --- sağlayıcılar ---
    def _anthropic(self, prompt, system):
        model = os.getenv("ANTHROPIC_MODEL", self.s.anthropic_model)
        try:
            import anthropic  # type: ignore
        except ImportError:
            anthropic = None
        if anthropic is not None:
            client = anthropic.Anthropic(timeout=self.s.llm_timeout_seconds)
            msg = client.messages.create(
                model=model, max_tokens=16000, system=system or anthropic.NOT_GIVEN,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "high"},
            )
            if msg.stop_reason == "refusal":
                raise LLMError("model isteği reddetti")
            return "".join(b.text for b in msg.content if b.type == "text")
        payload = {
            "model": model, "max_tokens": 16000,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": "high"},
        }
        if system:
            payload["system"] = system
        data = _post_json(
            "https://api.anthropic.com/v1/messages", payload,
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
            self.s.llm_timeout_seconds,
        )
        if data.get("stop_reason") == "refusal":
            raise LLMError("model isteği reddetti")
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def _openai(self, prompt, system):
        payload = {"model": os.getenv("OPENAI_MODEL", self.s.openai_model), "input": prompt}
        if system:
            payload["instructions"] = system
        data = _post_json(
            "https://api.openai.com/v1/responses", payload,
            {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
            self.s.llm_timeout_seconds,
        )
        if data.get("output_text"):
            return data["output_text"]
        parts = []
        for item in data.get("output", []):
            for c in item.get("content", []) or []:
                if c.get("type") in ("output_text", "text"):
                    parts.append(c.get("text", ""))
        return "".join(parts)

    def _gemini(self, prompt, system):
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        model = os.getenv("GEMINI_MODEL", self.s.gemini_model)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        data = _post_json(url, payload, {}, self.s.llm_timeout_seconds)
        return data["candidates"][0]["content"]["parts"][0]["text"]

    def _ollama(self, prompt, system):
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}]
        data = _post_json(
            self.s.ollama_url,
            {"model": self.s.ollama_model, "messages": messages, "stream": False,
             "think": False, "format": "json", "keep_alive": "10m"},
            {}, self.s.llm_timeout_seconds,
        )
        return (data.get("message") or {}).get("content", "")


def extract_json(text):
    """LLM çıktısından ilk JSON nesnesini çıkarır."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Cevapta JSON bulunamadı")
    return json.loads(text[start:end + 1])
