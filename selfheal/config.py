"""Ayarlar. Varsayılanlar burada; değiştirmek için kök klasördeki
selfheal_ayarlar.json dosyasına aynı isimle yaz (sadece değiştirmek istediklerini)."""
import json
from dataclasses import dataclass, field, fields
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def default_target(root=PACKAGE_ROOT) -> Path:
    """Geliştirilecek JARVIS klasörü.
    - Depo düzeni (GitHub): <kök>/desktop  (selfheal/ ve run.py kökte)
    - Kurulu paket (C:\\JARVIS): main.py, run.py ve selfheal/ aynı klasörde -> kökün kendisi
    """
    root = Path(root)
    if (root / "desktop" / "main.py").is_file():
        return root / "desktop"
    if (root / "main.py").is_file():
        return root
    return root / "desktop"


@dataclass
class Settings:
    # Hangi kodu geliştirecek? Varsayılan: depoda desktop/, kurulu pakette kök klasör.
    target_dir: str = field(default_factory=lambda: str(default_target()))
    # Durum, yedek, rapor ve loglar burada tutulur (hedef klasörün DIŞINDA).
    data_dir: str = str(PACKAGE_ROOT / "selfheal_data")

    # gozlem  : sadece sorunları bulur ve raporlar
    # oneri   : düzeltme üretir, test eder, onaylanırsa öneri klasörüne yazar (koda dokunmaz)
    # otomatik: düzeltme testlerden geçerse yedek alıp uygular, sorun çıkarsa geri alır
    mode: str = "otomatik"

    cycle_seconds: int = 300           # iki tarama arası bekleme
    test_timeout_seconds: int = 180
    max_attempts_per_issue: int = 3    # sonra "insan gerekli" olarak işaretlenir
    retry_backoff_minutes: int = 30    # başarısız denemeden sonra bekleme (her denemede katlanır)
    max_changed_lines: int = 80
    max_files_per_fix: int = 3
    require_repro_test_for_runtime: bool = True  # çalışma hatasında önce hatayı yakalayan test şart
    idle_test_generation: bool = True  # yapacak iş yokken yeni test yazarak kendini büyütür
    max_context_chars: int = 14000

    # Asla otomatik değiştirilmeyecek dosyalar (hedef klasöre göre glob)
    protected_globs: list = field(default_factory=lambda: [
        "tests/*", "tests/**/*", "*.bat", "*.ino", "**/*.ino", "requirements.txt",
        "data/*", "data/**/*", "selfheal/*", "selfheal/**/*", "interface/gpu_hud_v0_6_yedek.py",
        # v2.1: kişisel ayarlar ve güvenlik kodu (telefon erişim anahtarı, güncelleme doğrulaması)
        "config_local.py", "config_local.example.py", "core/phone_server.py", "core/updater.py",
        "run.py", "phone_ui/*", "phone_ui/**/*",
    ])
    # LLM'in yeni dosya oluşturabileceği tek yer
    new_file_globs: list = field(default_factory=lambda: ["tests/auto/test_*.py"])
    ignore_dirs: list = field(default_factory=lambda: [
        "__pycache__", ".git", "data", "logs", ".venv", "venv", "node_modules", "selfheal_data",
        "selfheal", "phone_ui", "build",   # v2.1: kurulu pakette motor ve telefon arayüzü hedefin içinde
    ])

    # LLM sağlayıcıları (sırayla denenir; anahtarı olmayan atlanır)
    llm_order: list = field(default_factory=lambda: ["anthropic", "openai", "gemini", "ollama"])
    ollama_url: str = "http://localhost:11434/api/chat"
    ollama_model: str = "qwen3:8b"
    anthropic_model: str = "claude-opus-5-5"
    openai_model: str = "gpt-5"
    gemini_model: str = "gemini-3.8-flash"
    llm_timeout_seconds: int = 300

    # Uygulama gözetmeni (--uygulama ile)
    app_command: list = field(default_factory=lambda: ["{python}", "main.py"])
    app_restart_delay_seconds: int = 5
    app_max_quick_crashes: int = 5
    probation_seconds: int = 120       # düzeltmeden sonra bu süre içinde çökerse geri al
    restart_app_after_fix: bool = True
    check_ollama_health: bool = True

    @property
    def target(self) -> Path:
        return Path(self.target_dir).resolve()

    @property
    def data(self) -> Path:
        p = Path(self.data_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_settings(path=None, **overrides) -> Settings:
    s = Settings()
    path = Path(path) if path else PACKAGE_ROOT / "selfheal_ayarlar.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Settings)}
        for k, v in data.items():
            if k in known:
                setattr(s, k, v)
    for k, v in overrides.items():
        if v is not None:
            setattr(s, k, v)
    return s
