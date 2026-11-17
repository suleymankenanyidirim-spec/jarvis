import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

from core.web import WebResearcher
from core.browser import BrowserAgent
from core.helmet import HelmetController
from core.updater import SelfUpdater

APP_ALIASES = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "discord": "Discord.exe",
    "steam": "steam.exe",
    "spotify": "Spotify.exe",
    "notepad": "notepad.exe",
    "notepad++": "notepad++.exe",
    "calculator": "calc.exe",
    "hesap makinesi": "calc.exe",
    "explorer": "explorer.exe",
    "dosya gezgini": "explorer.exe",
    "vscode": "code.exe",
    "visual studio code": "code.exe",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Windows'ta izin verilen bir uygulamayı aç. Yalnızca kullanıcı açmasını istediğinde kullan.",
            "parameters": {
                "type": "object",
                "required": ["app"],
                "properties": {"app": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_application",
            "description": "İzin verilen Windows uygulamasını kapat. Yalnızca kullanıcı açıkça kapatmasını istediğinde kullan.",
            "parameters": {
                "type": "object",
                "required": ["app"],
                "properties": {"app": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_folder",
            "description": "Yalnızca Masaüstü, Belgeler veya İndirilenler klasörünü aç.",
            "parameters": {
                "type": "object",
                "required": ["folder"],
                "properties": {"folder": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": "Masaüstü, Belgeler veya İndirilenler altındaki belirli bir dosyayı aç.",
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Kullanıcının açıkça istediği http/https adresini tarayıcıda aç.",
            "parameters": {
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "CPU, RAM, disk ve Windows bilgisi getir.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "İnternette araştırma yapıp başlık, özet ve URL içeren sonuçlar döndür.",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Kullanıcı açıkça bir bilgiyi hatırlamamı istediğinde kalıcı hafızaya kaydet.",
            "parameters": {
                "type": "object",
                "required": ["memory"],
                "properties": {"memory": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_memory",
            "description": "Kullanıcı açıkça bir bilgiyi unutmamı istediğinde ilgili hafızayı pasifleştir.",
            "parameters": {
                "type": "object",
                "required": ["phrase"],
                "properties": {"phrase": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "helmet_control",
            "description": "Iron Man kaskinin vizorunu WiFi ve/veya Bluetooth uzerinden acar ya da kapatir. Yalnizca kullanici acikca kaski acmasini/kapatmasini istediginde kullan.",
            "parameters": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["ac", "kapat"]}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_background_task",
            "description": "Uzun sürecek araştırma, analiz veya çözüm işini arka planda başlat; kullanıcı konuşmaya devam edebilir.",
            "parameters": {
                "type": "object",
                "required": ["title", "prompt"],
                "properties": {"title":{"type":"string"}, "prompt":{"type":"string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_background_tasks",
            "description": "Arka plandaki ve tamamlanmış JARVIS görevlerini listele.",
            "parameters": {"type":"object","properties":{}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_background_task",
            "description": "Bir arka plan görevinin durumunu ve sonucunu getir.",
            "parameters": {
                "type":"object","required":["task_id"],
                "properties":{"task_id":{"type":"string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "council_consult",
            "description": "Zor veya uzmanlık gerektiren bir konuda AI Konseyinden görüş al ve yerel JARVIS ile sonucu sentezle.",
            "parameters": {
                "type":"object","required":["prompt","task_type"],
                "properties":{
                    "prompt":{"type":"string"},
                    "task_type":{"type":"string","enum":["general","kod","araştır","web","analiz"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stage_self_update",
            "description": "Güvenilir bir manifest URL'sindeki JARVIS güncellemesini indirir, SHA-256 doğrular ve bir sonraki açılışa hazırlar. Çalışan dosyaların üstüne yazmaz.",
            "parameters": {
                "type":"object","required":["manifest_url"],
                "properties":{"manifest_url":{"type":"string"}},
            },
        },
    },


    {"type":"function","function":{"name":"get_jarvis_status","description":"JARVIS özelliklerinin gerçekten kurulu ve erişilebilir olup olmadığını kontrol et.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"start_planned_task","description":"Çok adımlı veya uzun sürecek hedefi JARVIS planlayıcısına ver. Planı kendi oluşturur, arka planda yürütür ve başarısız adımları düzeltip tekrar dener.","parameters":{"type":"object","required":["title","goal"],"properties":{"title":{"type":"string"},"goal":{"type":"string"}}}}},
    {"type":"function","function":{"name":"browser_search","description":"Gerçek Chrome/Chromium tarayıcı motoruyla web araması yap ve sayfadaki sonuçları oku.","parameters":{"type":"object","required":["query"],"properties":{"query":{"type":"string"}}}}},
    {"type":"function","function":{"name":"browser_visit","description":"Gerçek tarayıcıyla bir URL'yi aç ve sayfanın görünen metnini oku.","parameters":{"type":"object","required":["url"],"properties":{"url":{"type":"string"}}}}},
    {"type":"function","function":{"name":"clipboard_copy","description":"Verilen metni Windows panosuna kopyala. Kullanıcı bir metni kopyalamasını açıkça istediğinde kullan.","parameters":{"type":"object","required":["text"],"properties":{"text":{"type":"string"}}}}},
    {"type":"function","function":{"name":"clipboard_read","description":"Windows panosundaki mevcut metni oku.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"copy_selected_text","description":"Aktif pencerede seçili metni Ctrl+C ile panoya kopyala ve metni geri döndür. Kullanıcı açıkça seçili metni kopyalamanı istediğinde kullan.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"paste_clipboard","description":"Windows panosundaki içeriği aktif pencereye Ctrl+V ile yapıştır. Kullanıcı açıkça yapıştırmanı istediğinde kullan.","parameters":{"type":"object","properties":{"press_enter":{"type":"boolean","description":"Yapıştırmadan sonra Enter'a basılsın mı?"}}}}},
    {"type":"function","function":{"name":"paste_text","description":"Verilen metni panoya kopyala ve aktif pencereye Ctrl+V ile yapıştır. Kullanıcı açıkça bir metni bir uygulamaya yapıştırmanı istediğinde kullan.","parameters":{"type":"object","required":["text"],"properties":{"text":{"type":"string"},"press_enter":{"type":"boolean","description":"Yapıştırmadan sonra Enter'a basılsın mı?"}}}}},

]

class ToolManager:
    def __init__(self, memory):
        self.memory = memory
        self.web = WebResearcher()
        self.browser = BrowserAgent()
        self.helmet = HelmetController()
        self.updater = SelfUpdater()

    def execute(self, name, args):
        try:
            if name == "open_application":
                return self._open_application(args.get("app", ""))
            if name == "close_application":
                return self._close_application(args.get("app", ""))
            if name == "open_folder":
                return self._open_folder(args.get("folder", ""))
            if name == "open_file":
                return self._open_file(args.get("path", ""))
            if name == "open_url":
                return self._open_url(args.get("url", ""))
            if name == "get_system_info":
                return self._system_info()
            if name == "get_jarvis_status":
                return self._jarvis_status()
            if name == "web_search":
                results, error = self.web.search(args.get("query", ""))
                if error:
                    return "WEB_ERROR: " + error
                return self.web.context(results)
            if name == "save_memory":
                return "Hafıza kaydedildi." if self.memory.save_memory(args.get("memory", "")) else "Bu bilgi zaten hafızada."
            if name == "forget_memory":
                return f"{self.memory.forget_memory(args.get('phrase', ''))} hafıza kaydı unutuldu."
            if name == "helmet_control":
                action = args.get("action", "")
                if action == "ac":
                    return self.helmet.ac()
                if action == "kapat":
                    return self.helmet.kapat()
                return "Geçersiz kask komutu."
            if name == "run_background_task":
                # ToolManager receives a callback from JarvisBrain after initialization.
                runner = getattr(self, "background_runner", None)
                if not runner:
                    return "Arka plan yöneticisi hazır değil."
                return runner(args.get("title","Görev"), args.get("prompt",""))
            if name == "list_background_tasks":
                getter = getattr(self, "task_lister", None)
                return getter() if getter else "Görev yöneticisi hazır değil."
            if name == "get_background_task":
                getter = getattr(self, "task_getter", None)
                return getter(args.get("task_id","")) if getter else "Görev yöneticisi hazır değil."
            if name == "council_consult":
                council = getattr(self, "council_runner", None)
                return council(args.get("prompt",""), args.get("task_type","general")) if council else "AI Konseyi hazır değil."
            if name == "stage_self_update":
                return self.updater.stage(args.get("manifest_url",""))
            if name == "start_planned_task":
                runner = getattr(self, "planned_runner", None)
                return runner(args.get("title", "JARVIS Görevi"), args.get("goal", "")) if runner else "Görev planlayıcı hazır değil."
            if name == "browser_search":
                return self.browser.search(args.get("query", ""))
            if name == "browser_visit":
                return self.browser.visit(args.get("url", ""))
            if name == "browser_click":
                return self.browser.click(args.get("url", ""), args.get("text", ""), args.get("selector", ""))
            if name == "clipboard_copy":
                return self._clipboard_copy(args.get("text", ""))
            if name == "clipboard_read":
                return self._clipboard_read()
            if name == "copy_selected_text":
                return self._copy_selected_text()
            if name == "paste_clipboard":
                return self._paste_clipboard(bool(args.get("press_enter", False)))
            if name == "paste_text":
                return self._paste_text(args.get("text", ""), bool(args.get("press_enter", False)))
            return "Bilinmeyen araç."
        except Exception as exc:
            return f"TOOL_ERROR: {exc}"

    @staticmethod
    def _clipboard_copy(text):
        text = str(text or "")
        if not text:
            return "Kopyalanacak metin boş."
        try:
            import pyperclip
            pyperclip.copy(text)
            return f"Pano güncellendi: {len(text)} karakter kopyalandı."
        except Exception as exc:
            return f"TOOL_ERROR: Pano yazılamadı: {exc}"

    @staticmethod
    def _clipboard_read():
        try:
            import pyperclip
            value = pyperclip.paste() or ""
            return value if value else "Pano boş."
        except Exception as exc:
            return f"TOOL_ERROR: Pano okunamadı: {exc}"

    @staticmethod
    def _keyboard():
        try:
            import pyautogui
            pyautogui.PAUSE = 0.04
            return pyautogui
        except Exception as exc:
            raise RuntimeError("Klavyeyi kontrol etmek için pyautogui gerekli. INSTALL.bat'ı çalıştır.") from exc

    def _copy_selected_text(self):
        # Kullanıcının mevcut panosunu kaybetmemek için önce sakla, seçimi kopyala,
        # metni oku; ardından eski panoyu geri yükle.
        try:
            import pyperclip
            old_clipboard = pyperclip.paste() or ""
            pyautogui = self._keyboard()
            pyautogui.hotkey("ctrl", "c")
            import time
            time.sleep(0.12)
            copied = pyperclip.paste() or ""
            try:
                pyperclip.copy(old_clipboard)
            except Exception:
                pass
            return copied if copied else "Seçili metin bulunamadı veya kopyalanamadı."
        except Exception as exc:
            return f"TOOL_ERROR: Seçili metin kopyalanamadı: {exc}"

    def _paste_clipboard(self, press_enter=False):
        try:
            pyautogui = self._keyboard()
            pyautogui.hotkey("ctrl", "v")
            if press_enter:
                pyautogui.press("enter")
            return "Pano içeriği yapıştırıldı." + (" Enter'a basıldı." if press_enter else "")
        except Exception as exc:
            return f"TOOL_ERROR: Yapıştırılamadı: {exc}"

    def _paste_text(self, text, press_enter=False):
        result = self._clipboard_copy(text)
        if result.startswith("TOOL_ERROR"):
            return result
        return self._paste_clipboard(press_enter)

    @staticmethod
    def _clipboard_available():
        try:
            import pyperclip  # noqa: F401
            import pyautogui  # noqa: F401
            return True
        except Exception:
            return False

    def _find_executable(self, exe):
        found = shutil.which(exe)
        if found:
            return found
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            try:
                hits = list(Path(base).rglob(exe))
                if hits:
                    return str(hits[0])
            except Exception:
                pass
        return None

    def _open_application(self, app):
        exe = APP_ALIASES.get(app.strip().lower())
        if not exe:
            return "Bu uygulamaya izin verilmiyor."
        path = self._find_executable(exe)
        if path:
            subprocess.Popen([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"{app} açıldı."
        if exe == "explorer.exe":
            subprocess.Popen(["explorer.exe"])
            return "Dosya Gezgini açıldı."
        return f"{app} bilgisayarda bulunamadı."

    def _close_application(self, app):
        exe = APP_ALIASES.get(app.strip().lower())
        if not exe:
            return "Bu uygulama izin verilen listede değil."
        result = subprocess.run(
            ["taskkill", "/IM", exe, "/T", "/F"],
            capture_output=True, text=True, shell=False
        )
        return f"{app} kapatıldı." if result.returncode == 0 else f"{app} çalışmıyor veya kapatılamadı."

    def _standard_folder(self, name):
        home = Path.home()
        mapping = {
            "masaüstü": home / "Desktop", "desktop": home / "Desktop",
            "belgeler": home / "Documents", "documents": home / "Documents",
            "indirilenler": home / "Downloads", "downloads": home / "Downloads",
        }
        return mapping.get(name.strip().lower())

    def _open_folder(self, folder):
        path = self._standard_folder(folder)
        if not path:
            return "Yalnızca Masaüstü, Belgeler ve İndirilenler açılabilir."
        if not path.exists():
            return f"Klasör bulunamadı: {path}"
        os.startfile(str(path))
        return f"{folder} klasörü açıldı."

    def _safe_path(self, text):
        raw = Path(os.path.expandvars(os.path.expanduser(text.strip())))
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        raw = raw.resolve()
        home = Path.home().resolve()
        roots = [(home / x).resolve() for x in ("Desktop", "Documents", "Downloads")]
        for base in roots:
            try:
                raw.relative_to(base)
                return raw
            except ValueError:
                pass
        return None

    def _open_file(self, path_text):
        path = self._safe_path(path_text)
        if not path:
            return "Güvenlik nedeniyle dosya yalnızca Masaüstü/Belgeler/İndirilenler altından açılabilir."
        if not path.exists() or not path.is_file():
            return "Dosya bulunamadı."
        os.startfile(str(path))
        return f"{path.name} açıldı."

    def _open_url(self, url):
        url = url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return "Yalnızca http/https adresleri açılabilir."
        webbrowser.open(url)
        return "Adres tarayıcıda açıldı."

    def _system_info(self):
        import platform, psutil
        disk = psutil.disk_usage(Path.home().anchor)
        temp = self._cpu_temperature()
        return (f"Windows: {platform.platform()}\n"
                f"CPU kullanımı: {psutil.cpu_percent(interval=0.15):.0f}%\n"
                f"RAM kullanımı: {psutil.virtual_memory().percent:.0f}%\n"
                f"CPU sıcaklığı: {temp if temp is not None else '--'}°C\n"
                f"Disk kullanımı: {disk.percent:.0f}%")

    def _cpu_temperature(self):
        try:
            import psutil
            temps = psutil.sensors_temperatures(fahrenheit=False)
            vals = [float(e.current) for entries in temps.values() for e in entries
                    if e.current is not None and 0 < float(e.current) < 110]
            if vals: return max(vals)
        except Exception: pass
        try:
            import wmi
            c = wmi.WMI(namespace=r"root\OpenHardwareMonitor")
            vals = [float(x.Value) for x in c.Sensor()
                    if getattr(x, "SensorType", "") == "Temperature" and 0 < float(x.Value) < 110]
            if vals: return max(vals)
        except Exception: pass
        return None

    def _jarvis_status(self):
        import os, config, requests
        try:
            base = str(getattr(config, "OLLAMA_URL", "")).split("/api")[0]
            requests.get(base, timeout=1.5).raise_for_status()
            ollama = True
        except Exception:
            ollama = False
        manifest = str(getattr(config, "UPDATE_MANIFEST_URL", "") or "").strip() or \
            self.updater.github_manifest_url(getattr(config, "GITHUB_REPO", ""))
        council = bool(getattr(self, "council_runner", None))
        providers = {
            "ollama": ollama,
            "openai": bool(os.getenv("OPENAI_API_KEY")),
            "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
            "gemini": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        }
        return {
            "background_tasks": bool(getattr(self, "background_runner", None)),
            "persistent_task_history": Path("data/background_tasks.json").exists(),
            "ai_council": council,
            "council_providers": providers,
            "ollama": ollama,
            "openai_key": providers["openai"],
            "anthropic_key": providers["anthropic"],
            "gemini_key": providers["gemini"],
            "safe_staged_updater": hasattr(self, "updater"),
            "auto_update_configured": bool(getattr(config, "AUTO_UPDATE_CHECK", False) and manifest),
            "update_manifest_configured": bool(manifest),
            "auto_update_on_start": bool(getattr(config, "AUTO_APPLY_UPDATES_ON_START", False)),
            "voice_profiles": ["V1", "V2", "V3"],
            "web_search": bool(getattr(self.web, "available", False)),
            "browser_automation": bool(getattr(self.browser, "available", False)),
            "clipboard_tools": bool(getattr(__import__('config'), "CLIPBOARD_ENABLED", True) and self._clipboard_available()),
            "memory": self.memory is not None,
            "planner": bool(getattr(self, "planned_runner", None)),
            "suno": False,
            "phone_server": bool(getattr(config, "PHONE_SERVER_ENABLED", True)),
            "github_repo": str(getattr(config, "GITHUB_REPO", "") or ""),
            "version": self.updater.current_version(),
        }
