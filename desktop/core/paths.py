"""
Yol çözümü (v2.2)
=================
JARVIS'in tüm kalıcı dosyaları (data/ klasörü) artık çalışma klasörüne (cwd)
göre değil, main.py'nin bulunduğu klasöre göre çözülür. Böylece JARVIS
Windows açılışında, kısayoldan ya da başka bir klasörden başlatılsa bile
hep aynı hafızayı, görev geçmişini ve telefon anahtarını kullanır.
"""

from pathlib import Path

# core/paths.py -> core/ -> main.py'nin klasörü
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"


def resolve(path, base=None):
    """Göreli bir yolu main.py'nin klasörüne göre mutlak yola çevirir.

    Mutlak yollar olduğu gibi döner (testler ve özel ayarlar için).
    """
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    return Path(base or BASE_DIR) / p


def data_path(*parts):
    """data/ klasörü içindeki bir dosyanın mutlak yolu: data_path("x.json")."""
    return DATA_DIR.joinpath(*parts)
