import json
import re
import requests

from config import OLLAMA_URL, MODEL, SYSTEM_PROMPT
from core.memory import MemoryStore
from core.tools import TOOLS, ToolManager
from core.background import BackgroundTaskManager
from core.council import AICouncil
from core.planner import TaskPlanner
from core.updater import SelfUpdater



# "Seni kim üretti?" — model ne derse desin sabit cevap (config.CREATOR_ANSWER).
_CREATOR_RE = re.compile(
    r"\bkim(ler)?\b.*\b(üret|yap|geliştir|yarat|tasarla|oluştur|kodla)"
    r"|\b(üret|yap|geliştir|yarat|tasarla|oluştur|kodla)\w*\s.*\bkim(ler)?\b"
    r"|\bhangi (şirket|firma|ekip)\w*\s.*(üret|yap|geliştir|yarat)"
    r"|(yapımcın|geliştiricin|yaratıcın|üreticin|tasarımcın)\b")
_SELF_RE = re.compile(r"\b(sen|seni|senin)\b|\w+(ıldın|ildin|uldun|üldün)\b"
                      r"|(yapımcın|geliştiricin|yaratıcın|üreticin|tasarımcın)\b")


def is_creator_question(text):
    low = str(text or "").replace("I", "ı").replace("İ", "i").lower()
    return bool(_CREATOR_RE.search(low) and _SELF_RE.search(low))


def _cfg(name, default=None):
    import config
    return getattr(config, name, default)


def creator_answer():
    return _cfg("CREATOR_ANSWER",
                "Slov Studio — Made in Türkiye. Süleyman Kenan Yıldırım tarafından yapılmıştır.")


class JarvisBrain:
    def __init__(self):
        # v2.2: config.MEMORY_DB artık gerçekten kullanılıyor (göreli yol main.py'nin klasörüne göre)
        self.memory = MemoryStore(_cfg("MEMORY_DB", "data/jarvis_memory.db"))
        self.tools = ToolManager(self.memory)
        self.tasks = BackgroundTaskManager(
            max_workers=getattr(__import__('config'), 'BACKGROUND_MAX_WORKERS', 3),
            max_retries=getattr(__import__('config'), 'BACKGROUND_MAX_RETRIES', 2),
        )
        self.updater = SelfUpdater()
        self.council = AICouncil(self._local_chat_text)
        self.planner = TaskPlanner(
            self._local_chat_text,
            max_steps=getattr(__import__('config'), 'PLANNER_MAX_STEPS', 7),
        )
        self.tools.background_runner = self.run_background
        self.tools.planned_runner = self.start_planned_task
        self.tools.task_lister = self.task_status
        self.tools.task_getter = self.task_status
        self.tools.council_runner = self.council_decide

    def _context(self, query):
        memories, recent = self.memory.build_context(query)
        memory_text = "\n".join("- " + x for x in memories) or "- Yok"
        recent_text = "\n".join(f"{role}: {content}" for role, content in recent) or "Yok"
        return "KALICI HAFIZA:\n" + memory_text + "\n\nYAKIN KONUŞMA:\n" + recent_text

    def _chat(self, messages):
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
        }
        r = requests.post(OLLAMA_URL, json=payload, timeout=180)
        r.raise_for_status()
        return r.json().get("message", {})

    def _local_chat_text(self, prompt):
        msg = self._chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])
        return (msg.get("content") or "").strip()

    def _calls(self, message):
        out = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            out.append((fn.get("name", ""), args))
        return out

    @staticmethod
    def _looks_like_long_task(text):
        low = (text or "").lower()
        strong = (
            "araştır ve", "bul ve", "karşılaştır ve", "kontrol et ve",
            "incele ve", "analiz et ve", "rapor hazırla", "detaylı araştır",
            "kendi kendine", "adım adım", "en uygununu bul", "listele ve"
        )
        actions = ("araştır", "karşılaştır", "kontrol et", "incele", "analiz", "bul", "rapor", "kaynak")
        return any(x in low for x in strong) or (len(low) > 150 and sum(x in low for x in actions) >= 2)

    def ask(self, text, allow_planning=True):
        """Interactive chat; long tasks can be delegated without blocking the conversation."""
        self.memory.save_message("user", text)
        if is_creator_question(text):
            answer = creator_answer()
            self.memory.save_message("assistant", answer)
            return answer
        if allow_planning and getattr(__import__('config'), 'AUTO_PLANNER', True) and self._looks_like_long_task(text):
            answer = self.start_planned_task("Otomatik görev: " + text[:70], text)
            self.memory.save_message("assistant", answer)
            return answer
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": self._context(text)},
            {"role": "user", "content": text},
        ]

        if self.tools.web.should_search(text):
            results, error = self.tools.web.search(text)
            if not error and results:
                messages.append({"role": "system", "content": "İLK WEB ARAŞTIRMASI:\n" + self.tools.web.context(results)})

        answer = ""
        for _ in range(8):
            message = self._chat(messages)
            messages.append(message)
            calls = self._calls(message)
            if not calls:
                answer = (message.get("content") or "").strip()
                break
            for name, args in calls:
                # Planner tool always gets control back immediately by creating a background task.
                result = self.tools.execute(name, args)
                messages.append({"role": "tool", "tool_name": name, "content": str(result)})

        answer = answer or "İşlem tamamlandı."
        self.memory.save_message("assistant", answer)
        return answer

    def run_background(self, title, prompt):
        task_id = self.tasks.submit(title, self.ask, prompt, allow_planning=False, retries=getattr(__import__('config'), 'BACKGROUND_MAX_RETRIES', 2))
        return f"Arka plan görevi başlatıldı. Görev ID: {task_id}"

    @staticmethod
    def _clean_json(text):
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        return text

    def _recover_plan(self, goal, error, failed_step):
        prompt = f"""
Bir JARVIS görevi başarısız oldu. Yeni bir yaklaşım öner.
Hedef: {goal}
Başarısız adım: {failed_step}
Hata: {error}
Sadece kısa bir düzeltme talimatı ver; kullanıcıya sorma. Geri dönüşü zor işlem önerme.
"""
        return self.council.deliberate(prompt, "analiz")

    @staticmethod
    def _resolve_url(value, previous):
        url = (value or "").strip()
        if url.lower() not in {"first_result_url", "$first_result_url", "last_url"}:
            return url
        try:
            data = json.loads(previous)
            for item in data.get("results", []):
                if item.get("url"):
                    return item["url"]
            if data.get("url"):
                return data["url"]
        except Exception:
            pass
        return ""

    def _execute_step(self, step, previous):
        kind = step.get("kind", "reason")
        if kind == "web_search":
            result, error = self.tools.web.search(step.get("query", ""))
            if error:
                raise RuntimeError(error)
            return self.tools.web.context(result)
        if kind == "browser_search":
            return json.dumps(self.tools.browser.search(step.get("query", "")), ensure_ascii=False)
        if kind == "browser_visit":
            data = self.tools.browser.visit(self._resolve_url(step.get("url", ""), previous))
            return json.dumps(data, ensure_ascii=False)
        if kind == "browser_click":
            data = self.tools.browser.click(self._resolve_url(step.get("url", ""), previous), step.get("text", ""), step.get("selector", ""))
            return json.dumps(data, ensure_ascii=False)
        if kind == "system_info":
            return self.tools.execute("get_system_info", {})
        if kind == "council":
            context = "\n\nÖNCEKİ ADIMLAR:\n" + previous[-5000:]
            return self.council.deliberate(step.get("prompt", "") + context, step.get("task_type", "general"))
        if kind == "reason":
            context = "\n\nÖNCEKİ ADIM SONUÇLARI:\n" + previous[-8000:]
            return self._local_chat_text(step.get("prompt", "") + context)
        raise RuntimeError(f"Desteklenmeyen plan adımı: {kind}")

    def _execute_planned(self, title, goal, task_id=None):
        plan = self.planner.create_plan(goal)
        steps = plan["steps"]
        if task_id:
            self.tasks.set_plan(task_id, steps)

        previous = ""
        completed = []
        for index, step in enumerate(steps, 1):
            if task_id:
                self.tasks.set_progress(task_id, index - 1, len(steps), "running", step.get("title", ""))
            step_error = ""
            result = None
            for attempt in range(0, 3):
                try:
                    result = self._execute_step(step, previous)
                    self.tasks.append_step_result(task_id, index, step, result, True, "", attempt)
                    break
                except Exception as exc:
                    step_error = str(exc)
                    self.tasks.append_step_result(task_id, index, step, "", False, step_error, attempt)
                    if attempt >= 2:
                        recovery = self._recover_plan(goal, step_error, step.get("title", ""))
                        # One final self-correction attempt using the council's recovery instruction.
                        corrected = dict(step)
                        corrected["kind"] = "reason"
                        corrected["prompt"] = recovery + "\n\nAsıl adım hedefi: " + step.get("prompt", step.get("query", ""))
                        result = self._execute_step(corrected, previous)
                        self.tasks.append_step_result(task_id, index, corrected, result, True, "", attempt + 1)
                        break
            if result is None:
                raise RuntimeError(f"Adım tamamlanamadı: {step.get('title')} — {step_error}")
            completed.append(f"ADIM {index}: {step.get('title')}\n{result}")
            previous = "\n\n".join(completed)
            if task_id:
                self.tasks.set_progress(task_id, index, len(steps), "running", step.get("title", ""))

        synthesis = self._local_chat_text(
            "JARVIS görevi için aşağıdaki adım sonuçlarını birleştir. Sonucu Türkçe ve uygulanabilir ver. "
            "Eksik/çelişkili noktaları açıkça belirt.\n\nHEDEF:\n" + goal + "\n\n" + previous
        )
        return synthesis

    def start_planned_task(self, title, goal):
        task_id = self.tasks.submit(
            title,
            self._execute_planned,
            title,
            goal,
            retries=getattr(__import__('config'), 'BACKGROUND_MAX_RETRIES', 2),
            recovery_fn=lambda error, tid: self._recover_plan(goal, error, title),
        )
        return f"Planlı arka plan görevi başlatıldı. Görev ID: {task_id}. JARVIS planı kendi oluşturup yürütecek."

    def task_status(self, task_id=None):
        if task_id:
            return self.tasks.get(task_id)
        return self.tasks.list()

    def council_decide(self, prompt, task_type="general"):
        return self.council.deliberate(prompt, task_type)

    def shutdown(self):
        self.tasks.shutdown()
