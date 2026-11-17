import json
import re


ALLOWED_KINDS = {"web_search", "browser_search", "browser_visit", "browser_click", "council", "reason", "system_info"}


class TaskPlanner:
    """Turns a natural-language goal into an executable, bounded plan."""
    def __init__(self, llm, max_steps=7):
        self.llm = llm
        self.max_steps = int(max_steps)

    @staticmethod
    def _extract_json(text):
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Plan JSON bulunamadı.")
        return json.loads(text[start:end + 1])

    def create_plan(self, goal):
        prompt = f"""
Sen JARVIS görev planlayıcısısın. Kullanıcının hedefini küçük, uygulanabilir adımlara ayır.
En fazla {self.max_steps} adım üret.
Sadece şu türleri kullan: web_search, browser_search, browser_visit, browser_click, council, reason, system_info.
Her adım bağımsız ve açık olmalı. URL bilmiyorsan browser_search veya web_search kullan; sonraki browser_visit adımında url olarak FIRST_RESULT_URL kullanabilirsin.
Güvenlik: şifre, ödeme, mesaj gönderme, dosya silme veya geri dönüşü zor sistem değişikliği planlama.
Çıktı SADECE JSON olsun:
{{"title":"...","steps":[{{"title":"...","kind":"reason|web_search|browser_search|browser_visit|browser_click|council|system_info","query":"...","url":"...","text":"...","selector":"...","prompt":"...","task_type":"general|kod|araştır|web|analiz"}}]}}

KULLANICI HEDEFİ:
{goal}
"""
        raw = self.llm(prompt)
        data = self._extract_json(raw)
        steps = data.get("steps") or []
        cleaned = []
        for step in steps[: self.max_steps]:
            if not isinstance(step, dict):
                continue
            kind = str(step.get("kind", "reason")).strip().lower()
            if kind not in ALLOWED_KINDS:
                kind = "reason"
            cleaned.append({
                "title": str(step.get("title") or f"Adım {len(cleaned)+1}"),
                "kind": kind,
                "query": str(step.get("query") or ""),
                "url": str(step.get("url") or ""),
                "text": str(step.get("text") or ""),
                "selector": str(step.get("selector") or ""),
                "prompt": str(step.get("prompt") or ""),
                "task_type": str(step.get("task_type") or "general"),
            })
        if not cleaned:
            cleaned = [{"title": "Doğrudan analiz", "kind": "reason", "query": "", "url": "", "text": "", "selector": "", "prompt": goal, "task_type": "general"}]
        return {"title": str(data.get("title") or "JARVIS Görevi"), "steps": cleaned}
