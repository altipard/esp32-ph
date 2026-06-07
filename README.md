# Petri-Heil Wassersensor — Firmware & Flasher

[![CI](https://github.com/altipard/-esp32-ph/actions/workflows/ci.yml/badge.svg)](https://github.com/altipard/-esp32-ph/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/altipard/-esp32-ph?sort=semver)](https://github.com/altipard/-esp32-ph/releases)
[![License: MIT](https://img.shields.io/github/license/altipard/-esp32-ph)](LICENSE)

Ein autonomer **Wassersensor für Vereinsgewässer**. Das Gerät misst
Wasserwerte (aktuell die Temperatur, erweiterbar um pH, Sauerstoff …), schickt
sie per **Mobilfunk (LTE)** an das Petri-Heil-Backend und schläft dazwischen
stromsparend. Es braucht **kein WLAN** und **keinen festen Standort** — es läuft
auch am abgelegenen Weiher per Solar/Akku.

Dazu gehören zwei Teile:

- **Flasher** (Desktop-Tool, Go + Fyne) — spielt Firmware aufs Board, **ohne
  Terminal**, für Vereinsmitglieder bedienbar.
- **Firmware** (MicroPython, auf dem Board) — misst, sendet, schläft. Wird über
  ein **Handy-Portal** konfiguriert, nicht über ein Kabel.

> **Status:** Der komplette Pfad ist am echten Board verifiziert — Netzzeit →
> LTE-Datenverbindung → HTTPS-POST → Wert im Backend gespeichert. Getestet mit
> LILYGO T-SIM7000G + 1NCE-SIM gegen die Produktion.

## Geräte-Unterstützung

Die Firmware ist zunächst nur für **dieses eine Board** (LILYGO T-SIM7000G)
gebaut. Weitere Geräte (andere ESP32-Varianten, andere Modems/Sensoren) lassen
sich grundsätzlich ergänzen.

Bedarf an Unterstützung für ein bestimmtes Gerät? → **info@petri-heil.online**

---

## So funktioniert es

```
 Sensor (DS18B20)
      │  misst Temperatur
      ▼
 ESP32  ── puffert Messwerte im RTC-Speicher (überlebt Deep-Sleep)
      │   und schläft zwischen den Messungen (Strom sparen)
      ▼
 SIM7000G ── Mobilfunk (LTE-M), holt Uhrzeit aus dem Netz (NITZ)
      │   und sendet gebündelt per HTTPS
      ▼
 Backend (water_monitoring) ── speichert Werte je Gewässer
      ▼
 Web / App  ── Anzeige, Verlauf, Grenzwert-Alarme
```

Ablauf auf dem Gerät: **Aufwachen → (einmalig) Uhrzeit holen → messen →
puffern → wenn genug zusammen: senden → wieder schlafen.** Die Uhr (RTC)
überlebt den Schlaf, daher wird die Zeit nur nach einem Stromausfall neu geholt.

---

## Bauteile

Was für ein Gerät gebraucht wird (und im Test verwendet wurde):

| Bauteil | Konkret | Hinweis |
|--|--|--|
| **Board** | LILYGO **[T-SIM7000G](https://lilygo.cc/products/t-sim7000g)** (Solar-Variante) | ESP32 + LTE-Modem + GNSS auf einer Platine ([Doku](https://github.com/Xinyuan-LilyGO/LilyGo-Modem-Series/blob/main/docs/en/esp32/sim7000-esp32/README.MD) · [Schaltplan](https://github.com/Xinyuan-LilyGO/LilyGo-Modem-Series/blob/main/schematic/esp32/T-SIM7000G-200415.pdf)) |
| **MCU** | ESP32-WROVER-B (8 MB PSRAM, 16 MB Flash) | auf dem Board |
| **Modem** | SIM7000G — LTE-M / NB-IoT / 2G + GNSS | auf dem Board |
| **Temperatur-Sensor** | DS18B20 (wasserdichte Sonde) | OneWire, an **GPIO 32** |
| **Pull-up** | 4,7 kΩ Widerstand | DS18B20-Datenleitung → 3V3 |
| **SIM** | 1NCE IoT-SIM (Nano) | APN **`sensor.net`** |
| **LTE-Antenne** | Antenne am **`CEL`/`LTE`**-Anschluss | **nötig** — sonst keine Verbindung |
| **GPS-Antenne** | am `GPS`-Anschluss | **optional** — wird **nicht** gebraucht (Zeit kommt aus dem Netz) |
| **Stromversorgung** | LiPo-Akku 18650 + Solarpanel **oder** USB-C | Board hat Laderegler + Akku-Messung (GPIO 35) |
| **USB-UART** | CH9102F (VID `0x1A86` / PID `0x55D4`) | zum Flashen/Konfigurieren am PC |

> Mehrere Sensoren sind möglich (Registry in `sensors.py`). Mehrere DS18B20
> dürfen sich sogar **einen** OneWire-Pin teilen.

### Verkabelung DS18B20

| DS18B20 | Board |
|--|--|
| GND (schwarz) | GND |
| VDD (rot) | 3V3 |
| DATA (gelb) | GPIO **32** + 4,7 kΩ nach 3V3 |

> ⚠️ **GPIO 4 nicht für Sensoren benutzen** — das ist der Modem-PWRKEY
> (häufiger Fehler).

---

## Erstinbetriebnahme

1. **Flashen (PC, einmalig):** Board per USB-C anschließen, Flasher starten,
   Port wählen, *Flashen + Firmware aufspielen* klicken. Schreibt MicroPython +
   alle Firmware-Module.
2. **Konfigurieren (Handy):** Nach dem Flashen — und später nach jedem
   **Reset-Tastendruck** — fährt das Board ~60 s einen WLAN-Hotspot
   `PH-<MAC>` hoch (z. B. `PH-5BFF6C`). Handy verbinden (Passwort `petriheil`),
   die Konfig-Seite öffnet sich von selbst. Werte eintragen, speichern → Board
   startet neu.
3. **Fertig:** Im Messbetrieb bleibt der Hotspot aus (Strom sparen). Neu
   einrichten geht jederzeit per Reset-Taste — **kein Kabel nötig**.

Flashen (per USB) und Konfigurieren (per Handy) sind bewusst getrennt: so lässt
sich der Sensor im Feld ohne PC umstellen.

---

## Konfiguration (Captive-Portal → `config.json`)

| Feld | Beispiel | Bedeutung |
|--|--|--|
| `transport` | `lte` | `lte` \| `wifi` \| `auto` |
| `apn` | `sensor.net` | Mobilfunk-APN (1NCE = `sensor.net`) |
| `network_mode` | `auto` | `auto` \| `ltem` \| `nbiot` \| `gsm` |
| `tenant` | `fvw` | Vereinskürzel — die Ingest-URL wird daraus gebaut |
| `device_id` | `fvw-s001` | Geräte-ID (im Backend angelegt) |
| `api_key` | *(aus dem Backend)* | wird beim Anlegen **einmalig** angezeigt |
| `ingest_url` | *(leer lassen)* | optionaler Dev-Override; sonst aus `tenant` gebaut |
| `measure_interval_s` | `900` | Messabstand (Sekunden), 900 = 15 min |
| `batch_size` | `4` | so viele Messungen sammeln, dann senden |
| `sensor_id` / `type` / `pin` | `temp-1` / `ds18b20` / `32` | muss zum Backend-Sensor passen |

> Die WLAN-Felder (`wifi_ssid`/`wifi_pass`) sind nur für `transport=wifi`
> (Werkbank-Test). Im Feld läuft alles über LTE.

### Uhrzeit / Zeitstempel

Korrekte Zeitstempel sind Pflicht (das Backend verwirft Werte mit mehr als
±24 h Abweichung). Quelle ist die **Netzzeit (NITZ)** des Mobilfunknetzes:

- Die Firmware aktiviert sie per `AT+CTZU=1` (sonst meldet das Modem Jahr 1980).
- Der Zeitzonen-Offset wird abgezogen → die Uhr läuft in **UTC**.
- **Kein GPS und kein NTP nötig** (NTP ist über die 1NCE-APN ohnehin gesperrt).

### Backend anlegen (pro Gerät)

Im Admin der jeweiligen Vereins-Subdomain (`https://<tenant>.petri-heil.online/admin/`):

1. **IoT-Gerät** anlegen → `device_id` vergeben, **API-Key kopieren** (nur
   einmal sichtbar).
2. **Sensor** anlegen → `sensor_id` (identisch zur Geräte-Config) →
   Mess-Typ (`temperature`) → Gewässer wählen, aktiv setzen.
3. Mess-Typen sind in der Regel vorhanden; sonst:
   `python manage.py tenant_command create_measurement_types --schema=<tenant>`.

In der Geräte-Config genügt das **Vereinskürzel** (`tenant`, z. B. `fvw`); die
Firmware baut daraus die URL `https://<tenant>.petri-heil.online/water-monitoring/api/v1/ingest/`.
Der Tenant wird serverseitig über die **Subdomain** aufgelöst.

---

## Technik

**Transport:** LTE läuft **nicht** über PPP, sondern über die im SIM7000
eingebaute TCP/IP+HTTP-Engine (AT `SHCONF/SHCONN/SHBOD/SHREQ`) — auf SIM7000 +
MicroPython zuverlässiger als PPP.

**Ingest-Protokoll** (muss zum Backend `water-monitoring/api/v1/ingest/` passen):

```
POST <ingest_url>
Header: X-Device-ID, X-API-Key
Body:   {"m": [["temp-1", <unix_ts_utc>, <wert_x10>], ...]}   # 18,4 °C -> 184
```

### Designentscheidungen

- **esptool** wird **nicht** committet → beim ersten Flashen `v5.3.0` von GitHub
  geladen und in `~/Library/Caches/esp32-ph/` gecached.
- **MicroPython** (`ESP32_GENERIC-…-v1.28.0`) ist eingebettet → offline flashbar.
- **Konfiguration = `config.json` auf dem Board**, geschrieben vom Captive-Portal
  → Einstellungen ändern ohne Re-Flash.

---

## Bauen & Testen

Voraussetzungen: Go ≥ 1.25, C-Compiler (Fyne braucht CGo).

```bash
task check          # vet + test
task run            # GUI starten
task build          # Binary -> dist/
```

Firmware ändern? Die Module unter `internal/assets/firmware/*.py` bearbeiten —
sie werden beim Build automatisch eingebettet (`go:embed`).

Release (Cross-Platform): Tag `vX.Y.Z` pushen → GitHub-Actions baut macOS/Linux/
Windows und hängt die Binaries ans Release. Lokal: `task package:darwin` / `release`.

---

## Hardware-Referenz (Pins)

| Funktion | Pin |
|--|--|
| Modem UART TX → Modem RX | GPIO 27 |
| Modem UART RX ← Modem TX | GPIO 26 |
| Modem PWRKEY (⚠️ nicht für Sensoren) | GPIO 4 |
| Modem DTR | GPIO 25 |
| Status-LED | GPIO 12 |
| Batterie-/Solar-ADC (Teiler ×2) | GPIO 35 |
| DS18B20 (OneWire, Default) | GPIO 32 |

| | |
|--|--|
| Board | LILYGO T-SIM7000G (Solar) |
| MCU | ESP32-WROVER-B (ESP32-D0WD-V3, 8 MB PSRAM), 16 MB Flash |
| Modem | SIM7000G — 2G/GSM, NB-IoT/LTE-M, GNSS |
| USB-UART | CH9102F (VID `0x1A86` / PID `0x55D4`) |
| Sensor | DS18B20 an **GPIO 32** (nicht 4!), 4,7 kΩ Pull-up nach 3V3 |
