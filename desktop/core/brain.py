import json
import re
import threading

import requests

from config import OLLAMA_URL, MODEL, SYSTEM_PROMPT
from core.memory import VALID_SOURCES, MemoryStore
from core.tools import PHONE_BLOCKED_BY_DEFAULT, TOOL_NAMES, TOOLS, ToolManager
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


def phone_allowed_tools():
    """v2.6: telefondan (source="phone") kullanılabilecek araçlar (config.PHONE_ALLOWED_TOOLS)."""
    value = _cfg("PHONE_ALLOWED_TOOLS", None)
    if value is None:
        return frozenset(TOOL_NAMES) - set(PHONE_BLOCKED_BY_DEFAULT)
    if isinstance(value, str):
        value = value.split(",")
    return frozenset(str(x).strip() for x in value if str(x).strip())


def filter_tools(allowed):
    """Ollama'ya gönderilecek araç tanımları: allowed None ise hepsi."""
    if allowed is None:
        return TOOLS
    return [t for t in TOOLS if t["function"]["name"] in allowed]


def creator_answer():
    return _cfg("CREATOR_ANSWER",
                "Slov Studio — Made in Türkiye. Süleyman Kenan Yıldırım tarafından yapılmıştır.")


class JarvisBrain:
    def __init__(self):
        # v2.2: config.MEMORY_DB artık gerçekten kullanılıyor (göreli yol main.py'nin klasörüne göre)
        # v2.4: MEMORY_STEMMING=False -> hafıza araması v2.3'teki gibi yalnızca alt-dizi eşlemesi
        self.memory = MemoryStore(_cfg("MEMORY_DB", "data/jarvis_memory.db"),
                                  stemming=_cfg("MEMORY_STEMMING", True))
        self.tools = ToolManager(self.memory)
        self.tasks = BackgroundTaskManager(
            max_workers=_cfg("BACKGROUND_MAX_WORKERS", 3),
            max_retries=_cfg("BACKGROUND_MAX_RETRIES", 2),
        )
        self.updater = SelfUpdater()
        self.council = AICouncil(self._local_chat_text)
        self.planner = TaskPlanner(self._local_chat_text, max_steps=_cfg("PLANNER_MAX_STEPS", 7))
        self._init_ollama_lock()
        self.tools.background_runner = self.run_background
        self.tools.planned_runner = self.start_planned_task
        self.tools.task_lister = self.task_status
        self.tools.task_getter = self.task_status
        self.tools.council_runner = self.council_decide

    # ------------------------------------------------------------ Ollama kilidi (v2.3)
    def _init_ollama_lock(self):
        """Flask (threaded), HUD ve arka plan görevleri Ollama'yı aynı anda çağırmasın."""
        limit = max(1, int(_cfg("OLLAMA_MAX_CONCURRENT", 1) or 1))
        self._ollama_slots = threading.Semaphore(limit)
        self._tool_ctx = threading.local()      # v2.6: o an çalışan isteğin araç kısıtı (iş parçacığına özel)

    def _acquire_slot(self, blocking=True, timeout=None):
        if not blocking:
            return self._ollama_slots.acquire(blocking=False)
        return self._ollama_slots.acquire(timeout=timeout)

    def _release_slot(self):
        self._ollama_slots.release()

    def _wait_slot(self):
        timeout = float(_cfg("OLLAMA_LOCK_TIMEOUT", 300))
        if not self._acquire_slot(timeout=timeout):
            raise RuntimeError(f"Model meşgul: {timeout:g} saniyedir başka bir isteğin bitmesi bekleniyor. "
                               "Biraz sonra tekrar deneyin.")

    # ------------------------------------------------------------ model çağrıları
    @staticmethod
    def _payload(messages, stream, allowed_tools=None):
        return {
            "model": MODEL,
            "messages": messages,
            "tools": filter_tools(allowed_tools),   # v2.6: telefonda yalnızca izinli araçların tanımı
            "stream": stream,
            "think": False,
            "keep_alive": "10m",
        }

    def _chat(self, messages, allowed_tools=None):
        """Tek seferlik model çağrısı. Kilit yalnızca çağrı süresince tutulur."""
        self._wait_slot()
        try:
            r = requests.post(OLLAMA_URL, json=self._payload(messages, False, allowed_tools), timeout=180)
            r.raise_for_status()
            return r.json().get("message", {})
        finally:
            self._release_slot()

    def _chat_stream(self, messages, allowed_tools=None):
        """Akışlı model çağrısı. Olaylar: ("wait", None), ("token", metin), ("message", son mesaj).

        Üreteç yarıda kapatılsa da (telefon bağlantıyı kopardı) bağlantı kapanır ve kilit bırakılır.
        """
        if not self._acquire_slot(blocking=False):
            yield ("wait", None)
            self._wait_slot()
        resp = None
        try:
            resp = requests.post(OLLAMA_URL, json=self._payload(messages, True, allowed_tools),
                                 timeout=180, stream=True)
            resp.raise_for_status()
            content, tool_calls = [], []
            for raw in resp.iter_lines():
                if not raw:
                    continue
                data = json.loads(raw)
                if data.get("error"):
                    raise RuntimeError(f"Ollama hatası: {data['error']}")
                msg = data.get("message") or {}
                piece = msg.get("content") or ""
                if piece:
                    content.append(piece)
                    yield ("token", piece)
                tool_calls.extend(msg.get("tool_calls") or [])
                if data.get("done"):
                    break
            final = {"role": "assistant", "content": "".join(content)}
            if tool_calls:
                final["tool_calls"] = tool_calls
            yield ("message", final)
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            self._release_slot()

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

    # ------------------------------------------------------------ oturumlar (v2.3)
    @staticmethod
    def _session(session_id, source):
        source = source or "hud"
        if source not in VALID_SOURCES:
            raise ValueError(f"Geçersiz kaynak: {source!r} (hud / phone / task)")
        if session_id is None:
            session_id = {"hud": _cfg("HUD_SESSION_ID", "hud"),
                          "phone": _cfg("PHONE_SESSION_ID", "phone")}.get(source, "task")
        return session_id, source

    def _context(self, query, session_id=None):
        limit = int(_cfg("RECENT_CONTEXT_MESSAGES", 8))
        memories, recent = self.memory.build_context(query, session_id=session_id, recent_limit=limit)
        memory_text = "\n".join("- " + x for x in memories) or "- Yok"
        recent_text = "\n".join(f"{role}: {content}" for role, content in recent) or "Yok"
        return "KALICI HAFIZA:\n" + memory_text + "\n\nYAKIN KONUŞMA:\n" + recent_text

    def _prepare(self, text, session_id, source, allow_planning):
        """Ortak başlangıç: mesajı kaydeder; hazır cevap (sabit/planlı görev) ya da model mesajları döner."""
        self.memory.save_message("user", text, session_id, source)
        if is_creator_question(text):
            return creator_answer(), None
        if allow_planning and _cfg("AUTO_PLANNER", True) and self._looks_like_long_task(text):
            return self.start_planned_task("Otomatik görev: " + text[:70], text), None
        return None, [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": self._context(text, session_id)},
            {"role": "user", "content": text},
        ]

    # ------------------------------------------------------------ araç kısıtı (v2.6)
    @staticmethod
    def allowed_tools_for(source, inherited=None):
        """Bu istekte kullanılabilecek araçlar; None = hepsi (HUD, PC'de başlatılan görevler).

        Telefondan başlatılan arka plan görevi kısıtı `inherited` ile devralır; bu yolla aşılamaz.
        """
        if inherited is not None:
            return frozenset(inherited)
        if source == "phone":
            return phone_allowed_tools()
        return None

    def _run_tools(self, calls, messages, allowed=None):
        ctx = getattr(self, "_tool_ctx", None)
        if ctx is None:
            ctx = self._tool_ctx = threading.local()
        previous = getattr(ctx, "allowed", None)
        ctx.allowed = allowed            # run_background_task bu kısıtı arka plan görevine aktarır
        try:
            for name, args in calls:
                if allowed is not None and name not in allowed:
                    print(f"[GÜVENLİK] Telefondan kapalı araç çağrıldı, çalıştırılmadı: {name}")
                    result = (f"ARAÇ_ENGELLİ: '{name}' aracı telefondan kullanılamaz (PHONE_ALLOWED_TOOLS). "
                              "Kullanıcıya bunu PC'den yapması gerektiğini söyle.")
                else:
                    result = self.tools.execute(name, args)
                messages.append({"role": "tool", "tool_name": name, "content": str(result)})
        finally:
            ctx.allowed = previous

    def ask(self, text, session_id=None, source=None, allow_planning=True, allowed_tools=None):
        """Etkileşimli sohbet. Parametresiz çağrı eskisi gibi HUD oturumunu kullanır.

        Uzun görevler arka plana devredilir; konuşma beklemez.
        """
        session_id, source = self._session(session_id, source)
        allowed = self.allowed_tools_for(source, allowed_tools)
        ready, messages = self._prepare(text, session_id, source, allow_planning)
        if ready is not None:
            self.memory.save_message("assistant", ready, session_id, source)
            return ready

        if self.tools.web.should_search(text):
            results, error = self.tools.web.search(text)
            if not error and results:
                messages.append({"role": "system", "content": "İLK WEB ARAŞTIRMASI:\n" + self.tools.web.context(results)})

        answer = ""
        for _ in range(8):
            message = self._chat(messages, allowed)
            messages.append(message)
            calls = self._calls(message)
            if not calls:
                answer = (message.get("content") or "").strip()
                break
            self._run_tools(calls, messages, allowed)

        answer = answer or "İşlem tamamlandı."
        self.memory.save_message("assistant", answer, session_id, source)
        return answer

    def ask_stream(self, text, session_id=None, source=None, allow_planning=True, allowed_tools=None):
        """v2.3: cevabı parça parça üretir (telefonun /api/ask_stream ucu).

        Olaylar: {"type": "wait"} · {"type": "tool", "name", "text"} · {"type": "token", "text"} ·
        {"type": "done", "reply"}. Hata olursa istisna fırlatılır (sunucu "error" olayına çevirir).
        Araç çağrısından önceki ara metin son cevaba dahil edilmez (ask ile aynı davranış).
        """
        session_id, source = self._session(session_id, source)
        allowed = self.allowed_tools_for(source, allowed_tools)
        ready, messages = self._prepare(text, session_id, source, allow_planning)
        if ready is not None:
            self.memory.save_message("assistant", ready, session_id, source)
            yield {"type": "token", "text": ready}
            yield {"type": "done", "reply": ready}
            return

        if self.tools.web.should_search(text):
            yield {"type": "tool", "name": "web_search", "text": "araç çalışıyor: web_search"}
            results, error = self.tools.web.search(text)
            if not error and results:
                messages.append({"role": "system", "content": "İLK WEB ARAŞTIRMASI:\n" + self.tools.web.context(results)})

        answer = ""
        waited = False
        for _ in range(8):
            message = {}
            for kind, value in self._chat_stream(messages, allowed):
                if kind == "wait":
                    if not waited:
                        waited = True
                        yield {"type": "wait", "text": "Model meşgul, sıra bekleniyor."}
                elif kind == "token":
                    yield {"type": "token", "text": value}
                elif kind == "message":
                    message = value
            messages.append(message)
            calls = self._calls(message)
            if not calls:
                answer = (message.get("content") or "").strip()
                break
            for name, _args in calls:
                yield {"type": "tool", "name": name, "text": f"araç çalışıyor: {name}"}
            self._run_tools(calls, messages, allowed)

        answer = answer or "İşlem tamamlandı."
        self.memory.save_message("assistant", answer, session_id, source)
        yield {"type": "done", "reply": answer}

    # ------------------------------------------------------------ arka plan görevleri
    def _background_ask(self, prompt, task_id=None, allowed_tools=None):
        """Arka plan görevi kendi oturumunda (task-<id>) konuşur; sohbet geçmişini görmez/kirletmez."""
        return self.ask(prompt, session_id=f"task-{task_id or 'bg'}", source="task",
                        allow_planning=False, allowed_tools=allowed_tools)

    def run_background(self, title, prompt):
        kwargs = {}
        ctx = getattr(self, "_tool_ctx", None)
        inherited = getattr(ctx, "allowed", None) if ctx is not None else None
        if inherited is not None:              # v2.6: telefondan başlatıldı -> aynı araç kısıtı
            kwargs["allowed_tools"] = sorted(inherited)
        task_id = self.tasks.submit(title, self._background_ask, prompt,
                                    retries=_cfg("BACKGROUND_MAX_RETRIES", 2), **kwargs)
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
        # v2.3: planlı görev kendi oturumuna (task-<id>) yazar; sohbet geçmişini kirletmez.
        session = f"task-{task_id or 'plan'}"
        self.memory.save_message("user", goal, session, "task")
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
        self.memory.save_message("assistant", synthesis or "", session, "task")
        return synthesis

    def start_planned_task(self, title, goal):
        task_id = self.tasks.submit(
            title,
            self._execute_planned,
            title,
            goal,
            retries=_cfg("BACKGROUND_MAX_RETRIES", 2),
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
