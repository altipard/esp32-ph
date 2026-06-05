# esp32-ph — Petri-Heil Sensor-Flasher

Kleines Desktop-Tool (Go + Fyne), mit dem ein Vereinsmitglied **ohne Terminal**
ein Petri-Heil-Wassersensor-Board (**LILYGO T-SIM7000G**) startklar macht:

1. **Board** per USB verbinden, Port wählen
2. **Flashen + Firmware aufspielen** (ein Klick): MicroPython + Sensor-Module
3. **Fertig** — konfiguriert wird danach **per Handy** über den Board-Hotspot

> Das Tool **konfiguriert nicht** (kein WLAN/API-Key-Eintragen). Das macht das
> Board selbst über ein **Captive-Portal** — siehe unten. So lässt sich der
> Sensor im Feld ohne Kabel (neu) einrichten.

## Zwei getrennte Aufgaben

| Aufgabe | Wie | Wann |
|--|--|--|
| MicroPython + Firmware aufs Board | **dieses Go-Tool, per USB** | einmalig / bei Firmware-Update |
| Einstellungen (WLAN/LTE, API-Key, …) | **Captive-Portal am Board, per Handy** | Erst-Konfig + jede Änderung |

### Captive-Portal (Konfiguration übers Handy)

Nach dem Einrichten — und später nach jedem **Reset-Tastendruck** — fährt das
Board ~60 s einen WPA2-Hotspot `PH-<MAC>` hoch (gerätespezifisch, z. B.
`PH-5BFF6C`). Handy verbinden
(Passwort `petriheil`), die Konfig-Seite öffnet sich automatisch. Eintragen,
speichern, Board startet neu. Beim normalen Messbetrieb (Deep-Sleep-Wakeup)
bleibt der Hotspot **aus** → Solar/Akku wird geschont. Funktioniert in **jedem**
Handy-Browser (kein WebSerial nötig).

## Architektur

```
main.go                     Fyne-Wizard (3 Schritte + Protokoll), nur Flashen+Upload
internal/
  serial/                   USB-Serial-Ports auflisten (CH9102/CP210x/... zuerst)
  flash/                    esptool-Standalone laden/cachen + Chip/Erase/Write
  provision/                Raw-REPL: Firmware-Module aufs Board (getestet: Builder)
  assets/
    firmware/               Quelle der Wahrheit: die MicroPython-Module (.py)
    micropython.bin         eingebettetes MicroPython-Image
    petri_heil_logo_round.png
Taskfile.yml                build / run / test / package / release
.github/workflows/release.yml  Cross-Build (macOS/Linux/Windows) bei Tag-Push
```

Das Repo ist **eigenständig** — keine Abhängigkeit zu anderen Repos oder lokalen
Pfaden. Firmware, MicroPython-Image und Logo sind eingebettet.

### Firmware-Module (auf dem Board)

Liegen in `internal/assets/firmware/` und werden ins Tool eingebettet. Das
Ingest-Protokoll (HTTP-Header + Body) muss zum Backend-Endpoint
`water_monitoring/api/v1/ingest/` passen — siehe Modul-Kopf in `main.py`.

| Datei | Aufgabe |
|--|--|
| `board.py` | Pinbelegung T-SIM7000G (⚠️ GPIO4 = Modem-PWRKEY, nicht für Sensoren!) |
| `modem.py` | SIM7000G: Power, AT, SIM/Signal/Netz, GPS, **LTE via PPP** |
| `sensors.py` | Sensor-Registry (mehrere Sensoren, aktuell DS18B20) |
| `captive.py` | WLAN-AP + DNS-Catch-all + Konfig-/Status-Portal |
| `main.py` | Orchestrierung: Cold-Boot→Portal, Messen, Transport, Deep-Sleep |

**Transport:** `config.transport` = `lte` \| `wifi` \| `auto`. LTE läuft über
`network.PPP` auf dem SIM7000 → `urequests` sendet transparent über Mobilfunk.

> ⚠️ **Firmware-Status: UNGETESTET auf Hardware.** Struktur, AT- und PPP-Sequenzen
> folgen der SIM7000-/MicroPython-Doku, brauchen aber Bring-up am echten Board
> (APN, Timing, PPP-Übergabe, Pins verifizieren).

### Designentscheidungen

- **esptool** wird **nicht** committet → beim ersten Flashen `v5.3.0` von GitHub
  geladen und in `~/Library/Caches/esp32-ph/` gecached.
- **MicroPython** (`ESP32_GENERIC-…-v1.28.0`) ist eingebettet → offline flashbar.
- **Konfiguration = config.json auf dem Board**, geschrieben vom Captive-Portal.
  Settings ändern = kein Re-Flash.

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

## Hardware-Referenz

| | |
|--|--|
| Board | LILYGO T-SIM7000G (Solar) |
| MCU | ESP32-WROVER-B (ESP32-D0WD-V3, 8 MB PSRAM), 16 MB Flash |
| Modem | SIM7000G — 2G/GSM, NB-IoT/LTE-M, GNSS |
| USB-UART | CH9102F (VID 0x1A86 / PID 0x55D4) |
| Sensor | DS18B20 an **GPIO 32** (NICHT 4!), 4.7 kΩ Pull-up nach 3V3 |
