"""İnsan okunur Türkçe durum raporu: selfheal_data/RAPOR.md"""
from datetime import datetime

STATUS_TR = {
    "acik": "🔴 açık", "duzeltildi": "✅ düzeltildi", "kendiliginden_gitti": "⚪ gitti",
    "oneri_hazir": "🟡 öneri hazır", "insan_gerekli": "🟠 insan gerekli", "izleniyor": "🔵 izleniyor",
}


def write_report(engine, baseline=None):
    counts, issues, attempts, changes = engine.store.summary()
    s = engine.last_scan
    lines = [
        "# JARVIS Self-Heal Raporu", "",
        f"Son güncelleme: {datetime.now():%Y-%m-%d %H:%M:%S}  ",
        f"Mod: **{engine.s.mode}**  ",
        f"Hedef: `{engine.root}`  ",
        f"Son tarama: {s.get('tests_ran', 0)} test çalıştı, {s.get('tests_failing', 0)} başarısız, "
        f"{s.get('found', 0)} sorun görüldü ({s.get('new', 0)} yeni, {s.get('gone', 0)} kayboldu)", "",
        "## Özet", "",
    ]
    lines += [f"- {STATUS_TR.get(k, k)}: {v}" for k, v in sorted(counts.items())] or ["- Kayıtlı sorun yok"]
    lines += ["", "## Sorunlar", "", "| Durum | Tür | Sorun | Deneme | Görülme |", "|---|---|---|---|---|"]
    for r in issues:
        title = r["title"].replace("|", "/")[:110]
        lines.append(f"| {STATUS_TR.get(r['status'], r['status'])} | {r['kind']} | {title} | {r['attempts']} | {r['seen_count']} |")
    lines += ["", "## Uygulanan değişiklikler", ""]
    for c in changes:
        lines.append(f"- #{c['id']} {c['ts']} — {c['title']} — **{c['status']}** (yedek: `{c['backup_dir']}`)")
    if not changes:
        lines.append("- Henüz yok")
    lines += ["", "## Son denemeler", ""]
    for a in attempts:
        reason = (a["reason"] or "").splitlines()[0][:160] if a["reason"] else ""
        lines.append(f"- {a['ts']} [{a['provider']}] {a['outcome']}: `{a['fingerprint'][:70]}` — {reason}")
    if not attempts:
        lines.append("- Henüz yok")
    lines += ["", "Son değişikliği geri almak için: `python run.py geri-al`", ""]
    (engine.data / "RAPOR.md").write_text("\n".join(lines), encoding="utf-8")
