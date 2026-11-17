/*
  Iron Man Kask - WiFi + Bluetooth (Klasik SPP) Servo Kontrol
  ESP32 + 2 Servo

  --- WiFi ---
  Yukledikten sonra Seri Monitor'de yazan IP adresine
  telefon/bilgisayardan tarayiciyla gir (ayni WiFi agi):
    http://<ESP32_IP>/
  Acilan sayfada iki buton var: AC / KAPAT

  --- Bluetooth ---
  Windows'ta Bluetooth ayarlarindan "JARVIS_KASK" cihazini eslestir.
  Eslesince Aygit Yoneticisi > Portlar altinda yeni bir COM portu
  (orn. COM5) olusur. O porta tek karakter gonderince kask calisir:
    'A' -> AC     'K' -> KAPAT     'D' -> DURUM sorgusu
  Bu portu Jarvis tarafinda config.py -> HELMET_BT_PORT alanina yaz.

  WiFi ve Bluetooth ayni anda calisir, istediginle kontrol edebilirsin.
  NOT: ESP32'de klasik Bluetooth ile WiFi ayni radyoyu paylasir; yogun
  WiFi trafiginde BT komutlarinda kucuk gecikme olabilir, bu basit
  ac/kapat komutlari icin sorun yaratmaz.

  Kutuphaneler:
    - ESP32Servo        (Library Manager'dan kur)
    - BluetoothSerial    (ESP32 board paketiyle otomatik gelir, ekstra kurulum gerekmez)

  Tools > Board olarak bir ESP32 karti secili olmali (BluetoothSerial.h
  yalnizca ESP32'de calisir, ESP8266/ESP32-S2/S3'te bulunmaz).
*/

#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>
#include "BluetoothSerial.h"

#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BLUEDROID_ENABLED)
#error Bluetooth etkin degil. Tools > Board olarak klasik BT destekleyen bir ESP32 karti sec (orn. "ESP32 Dev Module").
#endif

// ---- WiFi bilgileri ----
const char* ssid     = "WIFI_ADIN";
const char* password = "WIFI_SIFREN";

// ---- Bluetooth cihaz adi ----
const char* BT_NAME = "JARVIS_KASK";

// ---- PIN AYARLARI ----
#define SERVO1_PIN 13
#define SERVO2_PIN 14

// ---- Aci ayarlari ----
#define ACIK_ACI   150
#define KAPALI_ACI 20

Servo servo1;
Servo servo2;
WebServer server(80);
BluetoothSerial SerialBT;

bool kaskAcik = false;

const char* sayfa = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Iron Man Kask</title>
  <style>
    body { background:#111; color:#fff; font-family:sans-serif; text-align:center; padding-top:60px; }
    button {
      width:200px; height:70px; font-size:22px; margin:15px;
      border:none; border-radius:10px; color:#fff; cursor:pointer;
    }
    .ac { background:#c0392b; }
    .kapat { background:#2980b9; }
  </style>
</head>
<body>
  <h1>Kask Kontrol</h1>
  <button class="ac" onclick="fetch('/ac')">AC</button><br>
  <button class="kapat" onclick="fetch('/kapat')">KAPAT</button>
</body>
</html>
)rawliteral";

// ---- Ortak hareket fonksiyonlari (hem WiFi hem BT bunlari cagirir) ----
void kaskAc() {
  servo1.write(ACIK_ACI);
  servo2.write(ACIK_ACI);
  kaskAcik = true;
  Serial.println("Komut: AC");
}

void kaskKapat() {
  servo1.write(KAPALI_ACI);
  servo2.write(KAPALI_ACI);
  kaskAcik = false;
  Serial.println("Komut: KAPAT");
}

// ---- WiFi HTTP handler'lari ----
void handleRoot()   { server.send(200, "text/html", sayfa); }
void handleAc()     { kaskAc();    server.sendHeader("Access-Control-Allow-Origin", "*"); server.send(200, "text/plain", "ACILDI"); }
void handleKapat()  { kaskKapat(); server.sendHeader("Access-Control-Allow-Origin", "*"); server.send(200, "text/plain", "KAPANDI"); }
void handleDurum()  { server.sendHeader("Access-Control-Allow-Origin", "*"); server.send(200, "text/plain", kaskAcik ? "ACIK" : "KAPALI"); }

// ---- Bluetooth komut dinleyici (loop icinde surekli cagrilir) ----
void handleBluetoothCommands() {
  if (SerialBT.available()) {
    char c = SerialBT.read();
    if (c == 'A' || c == 'a') {
      kaskAc();
      SerialBT.println("ACILDI");
    } else if (c == 'K' || c == 'k') {
      kaskKapat();
      SerialBT.println("KAPANDI");
    } else if (c == 'D' || c == 'd') {
      SerialBT.println(kaskAcik ? "ACIK" : "KAPALI");
    }
  }
}

void setup() {
  Serial.begin(115200);

  servo1.setPeriodHertz(50);
  servo2.setPeriodHertz(50);
  servo1.attach(SERVO1_PIN, 500, 2400);
  servo2.attach(SERVO2_PIN, 500, 2400);
  kaskKapat();

  // ---- WiFi baglantisi (basarisiz olursa 15sn sonra vazgecip
  //      sadece Bluetooth ile devam eder, kod takilip kalmaz) ----
  WiFi.begin(ssid, password);
  Serial.print("WiFi baglaniyor");
  unsigned long baslangic = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - baslangic < 15000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi baglandi! Tarayicidan gir: http://");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi baglanamadi (15sn asildi) - sadece Bluetooth ile devam ediliyor.");
  }

  // ---- Bluetooth baslat ----
  SerialBT.begin(BT_NAME);
  Serial.print("Bluetooth cihaz adi: ");
  Serial.println(BT_NAME);
  Serial.println("Windows'tan eslestir, olusan COM portunu Jarvis config.py'a yaz.");

  // ---- WiFi HTTP rotalari ----
  server.on("/", handleRoot);
  server.on("/ac", handleAc);
  server.on("/kapat", handleKapat);
  server.on("/durum", handleDurum);
  server.begin();
}

void loop() {
  server.handleClient();
  handleBluetoothCommands();
}
