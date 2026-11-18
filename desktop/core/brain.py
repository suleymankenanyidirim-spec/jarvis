import json
import re
import threading

import requests

from config import OLLAMA_URL, MODEL, SYSTEM_PROMPT
from core import visuals
from core.memory import VALID_SOURCES, MemoryStore
from core.visuals import extract_location, info_topic, is_info_question, is_weather_question
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


# ---------------------------------------------------------------- v2.8: yerel modeli daha akıllı kullanma
# Küçük model (qwen3:8b) her mesajda 24 araç tanımı görünce şaşırıyor. Mesajda belirli bir konu geçiyorsa
# yalnızca o konunun araçları + temel araçlar gönderilir; hiçbir konu geçmiyorsa hepsi gönderilir.
CORE_TOOLS = ("web_search", "save_memory", "forget_memory", "get_system_info", "get_jarvis_status",
              "knowledge_search", "knowledge_save")
TOOL_GROUPS = (
    (r"\baç|\bkapat|başlat|çalıştır|uygulama|program|chrome|edge|spotify|discord|steam|notepad|"
     r"hesap makinesi|klasör|dosya|link|site|url|youtube|vscode",
     ("open_application", "close_application", "open_folder", "open_file", "open_url")),
    (r"tarayıcı|browser|sayfa|siteye|sitesine|incele|web'?de|internette",
     ("browser_search", "browser_visit", "open_url")),
    (r"kopyala|yapıştır|pano", ("clipboard_copy", "clipboard_read", "copy_selected_text",
                                "paste_clipboard", "paste_text")),
    (r"kask|helmet|maske", ("helmet_control",)),
    (r"görev|arka plan|araştır|rapor|planla|uzun süre",
     ("run_background_task", "list_background_tasks", "get_background_task", "start_planned_task")),
    (r"konsey|uzman|danış", ("council_consult",)),
    (r"güncelle", ("stage_self_update",)),
    (r"hava\s*(durum|nasıl)|kaç\s*derece|yağmur|\bkar\s*yağ|sıcaklık", ("get_weather",)),
)
_THINK_RE = re.compile(r"\bneden\b|\bniçin\b|\bniye\b|\bnasıl\b|açıkla|karşılaştır|hesapla|analiz|\bkod|program|"
                       r"\bhata|\bfark|\bplan|öner|strateji|mantık|ispat|çöz|\d+\s*[-+*/x×]\s*\d+")
_THINK_TAG_RE = re.compile(r"<think>.*?</think>\s*", re.S)


def _fold(text):
    return str(text or "").replace("I", "ı").replace("İ", "i").lower()


def select_tools(text):
    """Mesajın konusuna göre gönderilecek araçlar; belirli bir konu yoksa None (hepsi)."""
    if not _cfg("OLLAMA_TOOL_SELECTION", True):
        return None
    low = _fold(text)
    picked = set()
    for pattern, names in TOOL_GROUPS:
        if re.search(pattern, low):
            picked.update(names)
    if not picked:
        return None
    return frozenset(picked | set(CORE_TOOLS))


def combine_tools(selected, allowed):
    if selected is None:
        return allowed
    if allowed is None:
        return selected
    return frozenset(selected) & frozenset(allowed)


def wants_thinking(text, source="hud"):
    """OLLAMA_THINK: True / False / "auto". auto: uzun ya da akıl yürütme isteyen soru ve arka plan görevleri."""
    mode = _cfg("OLLAMA_THINK", "auto")
    if isinstance(mode, bool):
        return mode
    if str(mode).lower() != "auto":
        return str(mode).lower() in ("1", "true", "evet", "on")
    if source == "task":
        return True
    return len(str(text or "")) > 160 or bool(_THINK_RE.search(_fold(text)))


def strip_think(text):
    """Eski Ollama sürümleri düşünme metnini <think>…</think> olarak cevaba koyar; atılır."""
    text = _THINK_TAG_RE.sub("", str(text or ""))
    if "<think>" in text:              # kapanmamış etiket
        text = text.split("<think>")[0]
    return text.strip()


def model_options():
    opts = {}
    ctx = _cfg("OLLAMA_NUM_CTX", 8192)
    if ctx:
        opts["num_ctx"] = int(ctx)
    temp = _cfg("OLLAMA_TEMPERATURE", 0.4)
    if temp is not None:
        opts["temperature"] = float(temp)
    return opts


_KB_SAVE_RE = re.compile(r"^(?:bunu\s+)?(?:bilgi\s+)?depo(?:ya|ma)\s+(?:kaydet|ekle|yaz)\s*[:,-]?\s*(.+)$", re.S | re.I)
_QUESTION_RE = re.compile(r"\?\s*$|\b(nedir|kimdir|ne zaman|nerede|nereden|kaç|hangi|ne kadar|nasıl|neden|niçin|"
                          r"kim|ne demek|anlamı|tarihi|fiyatı|hakkında|bilgi ver|anlat)\b")


_HELMET_RE = re.compile(r"\bkask\w*\s+(?:\w+\s+){0,2}?(aç|kapat|durum|açık\s*mı|kapalı\s*mı)"
                        r"|\b(aç|kapat)\w*\s+kask")


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
        # v2.8: bilgi deposu (D:/JARVIS_Bilgi). Soru önce burada aranır, yoksa internete bakılır.
        from core.knowledge import from_config
        self.knowledge = from_config(_cfg)
        self.tools.knowledge = self.knowledge
        # v3.0: görsel kartlar — akıllı ev (data/ev.json), hava durumu (Open-Meteo), bilgi penceresi (Vikipedi)
        from core.home import Home
        self.home = Home() if _cfg("HOME_ENABLED", True) else None
        self.weather_enabled = bool(_cfg("VISUAL_WEATHER", True))
        self.wiki_enabled = bool(_cfg("VISUAL_INFO", True))
        self.direct_helmet = True          # "kaskı aç/kapat" modele gitmeden helmet_control'e
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
    def _payload(messages, stream, allowed_tools=None, think=False):
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": filter_tools(allowed_tools),   # v2.6: telefonda yalnızca izinli araçların tanımı
            "stream": stream,
            "think": bool(think),                   # v2.8: OLLAMA_THINK (auto: zor sorularda düşünür)
            "keep_alive": "10m",
        }
        opts = model_options()                      # v2.8: bağlam penceresi + sıcaklık
        if opts:
            payload["options"] = opts
        return payload

    def _chat(self, messages, allowed_tools=None, think=False):
        """Tek seferlik model çağrısı. Kilit yalnızca çağrı süresince tutulur."""
        self._wait_slot()
        try:
            r = requests.post(OLLAMA_URL, json=self._payload(messages, False, allowed_tools, think), timeout=300)
            r.raise_for_status()
            message = r.json().get("message", {})
            if message.get("content"):
                message["content"] = strip_think(message["content"])
            return message
        finally:
            self._release_slot()

    def _chat_stream(self, messages, allowed_tools=None, think=False):
        """Akışlı model çağrısı. Olaylar: ("wait", None), ("token", metin), ("message", son mesaj).

        Üreteç yarıda kapatılsa da (telefon bağlantıyı kopardı) bağlantı kapanır ve kilit bırakılır.
        """
        if not self._acquire_slot(blocking=False):
            yield ("wait", None)
            self._wait_slot()
        resp = None
        try:
            resp = requests.post(OLLAMA_URL, json=self._payload(messages, True, allowed_tools, think),
                                 timeout=300, stream=True)
            resp.raise_for_status()
            content, tool_calls = [], []
            in_think = False
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
                    # Eski Ollama: düşünme metni <think>…</think> içinde gelir; telefona gönderilmez.
                    if "<think>" in piece:
                        in_think = True
                    visible = piece
                    if in_think:
                        visible = piece.split("</think>", 1)[1] if "</think>" in piece else ""
                        if "</think>" in piece:
                            in_think = False
                    if visible:
                        yield ("token", visible)
                tool_calls.extend(msg.get("tool_calls") or [])
                if data.get("done"):
                    break
            final = {"role": "assistant", "content": strip_think("".join(content))}
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
        saved = self._knowledge_command(text, source)
        if saved:
            return saved, None
        helmet = self._helmet_command(text, source)   # v3.0: kask komutu da doğrudan (küçük model aracı kaçırıyordu)
        if helmet:
            return helmet, None
        home = getattr(self, "home", None)            # v3.0: ev komutu modele gitmeden işlenir, ev kartı açılır
        if home is not None:
            try:
                handled = home.handle(text)
            except Exception as exc:
                handled = (f"Ev sistemine ulaşamadım: {exc}", None)
            if handled:
                reply, card = handled
                if card:
                    self._add_visual(card)
                return reply, None
        if allow_planning and _cfg("AUTO_PLANNER", True) and self._looks_like_long_task(text):
            return self.start_planned_task("Otomatik görev: " + text[:70], text), None
        return None, [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": self._context(text, session_id)},
            {"role": "user", "content": text},
        ]

    def _helmet_command(self, text, source):
        """"kaskı aç / kapat / kask açık mı" -> helmet_control doğrudan çalışır, kask kartı açılır."""
        low = _fold(text)
        if not getattr(self, "direct_helmet", False) or not _HELMET_RE.search(low):
            return None
        allowed = self.allowed_tools_for(source)
        if allowed is not None and "helmet_control" not in allowed:
            return None
        if re.search(r"açık\s*mı|kapalı\s*mı|durum", low):
            action, verb = "durum", "Kask durumu"
        elif re.search(r"kapat", low):
            action, verb = "kapat", "Kaskı kapatıyorum"
        else:
            action, verb = "ac", "Kaskı açıyorum"
        result = str(self.tools.execute("helmet_control", {"action": action}))
        card = visuals.helmet_visual(result if action == "durum" else ("açık" if action == "ac" else "kapalı"))
        if re.search(r"yapılandırılmamış|hata|ulaşılamadı", _fold(result)):
            card["data"]["state"] = "unknown"
            self._add_visual(card)
            return f"{verb} dedim ama kaska ulaşamadım: {result}"
        self._add_visual(card)
        return f"{verb}." if action != "durum" else f"{verb}: {result}"

    # ------------------------------------------------------------ bilgi deposu (v2.8)
    def _knowledge_command(self, text, source):
        """"depoya kaydet: …" modele gitmeden depoya not olarak yazılır (küçük model bunu kaçırmasın)."""
        kb = getattr(self, "knowledge", None)
        m = _KB_SAVE_RE.match(str(text or "").strip())
        if kb is None or not m:
            return None
        try:
            rel = kb.add_note(m.group(1).strip(), source="telefon" if source == "phone" else "sohbet")
            return f"Bilgi deposuna kaydettim ({rel})."
        except Exception as exc:
            return f"Bilgi deposuna kaydedemedim: {exc}"

    def _research(self, text, messages):
        """Önce bilgi deposu; orada yoksa (ya da güncel bilgi isteniyorsa) internet.

        Bulunanları modele sistem mesajı olarak ekler. Kullanılan aracın adlarını üretir (akış için).
        """
        # v3.0: hava durumu sorusu -> gerçek veri (Open-Meteo) modele verilir, hava kartı açılır
        if getattr(self, "weather_enabled", False) and is_weather_question(text):
            try:
                from core import weather
                w = weather.get_weather(extract_location(text), tomorrow="yarın" in _fold(text))
                yield "get_weather"
                messages.insert(len(messages) - 1, {"role": "system", "content":
                                "GÜNCEL HAVA DURUMU (Open-Meteo): " + weather.summary(w) +
                                " Kısa ve doğal cevap ver; sayıları bu veriden al, uydurma."})
                self._add_visual(weather.visual(w))
                return
            except Exception as exc:
                print("[HAVA] Alınamadı:", exc)
        kb = getattr(self, "knowledge", None)
        hits = []
        if kb is not None:
            try:
                hits = kb.search(text, limit=int(_cfg("KNOWLEDGE_MAX_HITS", 4)))
            except Exception as exc:
                print("[BİLGİ] Depo aranamadı:", exc)
            if hits:
                yield "knowledge_search"
                messages.insert(len(messages) - 1, {"role": "system", "content":
                                "BİLGİ DEPOSU (kullanıcının kendi dosyaları) — soruyu önce buna göre cevapla, "
                                "cevapta 'bilgi deposuna göre' de; depoda yoksa uydurma:\n" + kb.context(hits)})
        # v3.0: kişi / yer / tarih sorusu ve depoda yoksa -> Vikipedi özeti + çizimli bilgi penceresi (solda)
        if not hits and getattr(self, "wiki_enabled", False) and is_info_question(text):
            try:
                from core import wiki
                info = wiki.lookup(info_topic(text))
                if info:
                    yield "wikipedia"
                    messages.insert(len(messages) - 1, {"role": "system", "content":
                                    f"VİKİPEDİ ÖZETİ ({info['title']}): {info['extract'][:1800]}\n"
                                    "Bu bilgiye dayanarak kısa, doğru ve anlaşılır anlat; emin olmadığını uydurma."})
                    self._add_visual(wiki.visual(info, wiki.drawing(info.get("image"))))
                    return
            except Exception as exc:
                print("[BİLGİ] Vikipedi'ye ulaşılamadı:", exc)
        web = self.tools.web.should_search(text)
        if (not web and kb is not None and not hits and _cfg("KNOWLEDGE_WEB_FALLBACK", True)
                and _QUESTION_RE.search(_fold(text))):
            web = True                     # depoda yok: internete bak
        if web:
            yield "web_search"
            results, error = self.tools.web.search(text)
            if not error and results:
                messages.append({"role": "system", "content": "İLK WEB ARAŞTIRMASI:\n" + self.tools.web.context(results)})

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
                    if name == "helmet_control":          # v3.0: kask kartı (açık / kapalı)
                        self._add_visual(visuals.helmet_visual(result))
                    elif name == "get_weather" and isinstance(result, str) and "°C" in result:
                        card = getattr(self.tools, "last_visual", None)
                        if isinstance(card, dict):
                            self._add_visual(card)
                messages.append({"role": "tool", "tool_name": name, "content": str(result)})
        finally:
            ctx.allowed = previous

    # ------------------------------------------------------------ görsel kartlar (v3.0)
    def _add_visual(self, card):
        ctx = getattr(self, "_tool_ctx", None)
        sink = getattr(ctx, "visuals", None) if ctx is not None else None
        if sink is not None and isinstance(card, dict):
            # aynı türden yeni kart eskisinin yerine geçer (ör. model başka bir şehrin havasını sorduysa en son veri)
            sink[:] = [c for c in sink if c.get("type") != card.get("type")]
            sink.append(card)

    def _finish_visuals(self, answer):
        """Başka kart yoksa ve cevapta kısayol geçiyorsa klavye kartı."""
        ctx = getattr(self, "_tool_ctx", None)
        sink = getattr(ctx, "visuals", None) if ctx is not None else None
        if sink is not None and not sink:
            card = visuals.keyboard_visual(answer)
            if card:
                sink.append(card)

    def ask_with_visuals(self, text, **kwargs):
        """HUD için: (cevap, [kart, ...]). Kartlar hava, ev, kask, bilgi ve klavye pencereleridir."""
        ctx = getattr(self, "_tool_ctx", None)
        if ctx is None:
            ctx = self._tool_ctx = threading.local()
        previous = getattr(ctx, "visuals", None)
        ctx.visuals = []
        try:
            answer = self.ask(text, **kwargs)
            self._finish_visuals(answer)
            return answer, list(ctx.visuals)
        finally:
            ctx.visuals = previous

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

        for _tool in self._research(text, messages):
            pass
        send = combine_tools(select_tools(text), allowed)   # v2.8: konuya göre araçlar (izin kısıtıyla)
        think = wants_thinking(text, source)

        answer = ""
        for _ in range(8):
            message = self._chat(messages, send, think)
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

        for tool in self._research(text, messages):
            yield {"type": "tool", "name": tool, "text": f"araç çalışıyor: {tool}"}
        send = combine_tools(select_tools(text), allowed)
        think = wants_thinking(text, source)

        answer = ""
        waited = False
        for _ in range(8):
            message = {}
            for kind, value in self._chat_stream(messages, send, think):
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

    def ask_stream_with_visuals(self, text, **kwargs):
        """ask_stream + sonda {"type": "visual", "visual": kart} olayları (telefon ileride kullanabilir)."""
        ctx = getattr(self, "_tool_ctx", None)
        if ctx is None:
            ctx = self._tool_ctx = threading.local()
        ctx.visuals = []
        reply = ""
        for ev in self.ask_stream(text, **kwargs):
            if ev.get("type") == "done":
                reply = ev.get("reply", "")
                self._finish_visuals(reply)
                for card in list(ctx.visuals):
                    yield {"type": "visual", "visual": card}
            yield ev
        ctx.visuals = None

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
