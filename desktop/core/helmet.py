"""
Iron Man kaskini WiFi (HTTP, ESP32 WebServer) ve/veya Bluetooth
(klasik SPP, Windows'ta eslesince olusan COM portu) uzerinden kontrol eder.

config.py icindeki HELMET_WIFI_IP ve/veya HELMET_BT_PORT doldurulmadiysa
o baglanti yolu otomatik atlanir - ikisi de doldurulursa ikisine de komut
gonderilir (biri calismazsa digeri yedek olur).
"""

import requests
import serial

from config import HELMET_WIFI_IP, HELMET_BT_PORT, HELMET_BT_BAUD


class HelmetController:
    def __init__(self):
        self.wifi_ip = (HELMET_WIFI_IP or "").strip()
        self.bt_port = (HELMET_BT_PORT or "").strip()
        self.bt_baud = HELMET_BT_BAUD

    def _wifi(self, path):
        if not self.wifi_ip:
            return None
        try:
            r = requests.get(f"http://{self.wifi_ip}/{path}", timeout=2)
            return r.text.strip()
        except requests.RequestException as exc:
            return f"hata ({exc})"

    def _bt(self, command_char):
        if not self.bt_port:
            return None
        try:
            with serial.Serial(self.bt_port, self.bt_baud, timeout=2) as ser:
                ser.write(command_char.encode())
                reply = ser.readline().decode(errors="ignore").strip()
                return reply or "gonderildi"
        except serial.SerialException as exc:
            return f"hata ({exc})"

    def _send(self, wifi_path, bt_char):
        parts = []
        w = self._wifi(wifi_path)
        if w is not None:
            parts.append(f"WiFi: {w}")
        b = self._bt(bt_char)
        if b is not None:
            parts.append(f"BT: {b}")
        if not parts:
            return "Kask baglantisi ayarlanmamis (config.py -> HELMET_WIFI_IP / HELMET_BT_PORT)."
        return " | ".join(parts)

    def ac(self):
        return self._send("ac", "A")

    def kapat(self):
        return self._send("kapat", "K")

    def durum(self):
        # Sadece BT klasik SPP tarafinda anlik durum sorgusu icin ('D')
        # WiFi HTTP tarafinda /durum endpoint'i kullanilir.
        parts = []
        w = self._wifi("durum")
        if w is not None:
            parts.append(f"WiFi: {w}")
        b = self._bt("D")
        if b is not None:
            parts.append(f"BT: {b}")
        return " | ".join(parts) if parts else "Kask baglantisi ayarlanmamis."
