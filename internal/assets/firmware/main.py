# Water Monitoring – LILYGO T-SIM7000G (MicroPython)
#
# Misst die in config.json konfigurierten Sensoren, puffert im RTC-Memory ueber
# mehrere Deep-Sleep-Zyklen und sendet gebuendelt an den Ingest-Endpoint —
# wahlweise per LTE (SIM7000) oder WLAN.
#
# Konfiguration kommt aus config.json (vom Captive-Portal ueber das Handy oder
# vom Flash-Tool per USB geschrieben), NICHT hardcoded.
#
# Re-Konfiguration ohne Kabel: Reset-Taste druecken -> Kaltstart -> Board faehrt
# fuer ~60s einen WLAN-Hotspot "PH-<MAC>" (z. B. PH-5BFF6C) hoch (Captive-Portal).
# Beim Deep-Sleep-Wakeup (Messzyklus) bleibt der Hotspot aus -> Strom sparen.
#
# Ingest-Protokoll (siehe water_monitoring/views_ingest.py):
#   POST <ingest_url>  Header X-Device-ID/X-API-Key/X-Tenant-ID
#   Body {"m": [["sensor-id", <unix_ts>, <wert_x10>], ...]}
#
# STATUS: UNGETESTET auf Hardware. Struktur + AT/PPP-Sequenzen folgen der
# SIM7000-/MicroPython-Doku, brauchen aber Bring-up am echten Board.

import json
import time

import machine

import board
import sensors

CONFIG_PATH = "config.json"
EPOCH_OFFSET = 946684800  # RTC zaehlt ab 2000, Unix ab 1970
TIME_VALID = 1_600_000_000  # > 2020 => RTC gesetzt
TIME_RETRY_S = 60  # kurzer Deep-Sleep, wenn keine Zeit ermittelbar war

_DEFAULTS = {
    "ap_password": "petriheil",
    "transport": "auto",          # "lte" | "wifi" | "auto"
    "wifi_ssid": "",
    "wifi_pass": "",
    "apn": "",
    "apn_user": "",
    "apn_pass": "",
    "ingest_url": "",
    "device_id": "",
    "api_key": "",
    "tenant_id": "",
    "network_mode": "auto",
    "measure_interval_s": 15 * 60,
    "batch_size": 4,
    "gps_enabled": False,
    "sensors": [{"sensor_id": "temp-1", "type": "ds18b20", "pin": board.DEFAULT_ONEWIRE_PIN}],
}


def load_config():
    cfg = dict(_DEFAULTS)
    try:
        with open(CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError) as exc:
        print("config.json nicht lesbar:", exc)
    return cfg


CFG = load_config()
_rtc = machine.RTC()


# --- RTC-Puffer (ueberlebt Deep-Sleep) ------------------------------------
def _load_buffer():
    try:
        raw = _rtc.memory()
        return json.loads(raw) if raw else []
    except (ValueError, OSError):
        return []


def _save_buffer(buf):
    try:
        _rtc.memory(json.dumps(buf))
    except OSError:
        pass


def unix_now():
    return time.time() + EPOCH_OFFSET


def is_cold_boot():
    """True bei Power-on / externem Reset, False beim Deep-Sleep-Wakeup."""
    return machine.reset_cause() != machine.DEEPSLEEP_RESET


def is_configured(cfg):
    return bool(cfg.get("device_id") and cfg.get("ingest_url"))


def select_transport(cfg):
    t = cfg.get("transport", "auto")
    if t in ("wifi", "lte"):
        return t
    return "wifi" if cfg.get("wifi_ssid") else "lte"


# --- Senden ----------------------------------------------------------------
def _post(cfg, measurements):
    import urequests

    headers = {
        "Content-Type": "application/json",
        "X-Device-ID": cfg["device_id"],
        "X-API-Key": cfg["api_key"],
        "X-Tenant-ID": cfg["tenant_id"],
    }
    resp = None
    try:
        resp = urequests.post(cfg["ingest_url"], data=json.dumps({"m": measurements}), headers=headers)
        ok = resp.status_code in (200, 201)
        print("Ingest:", resp.status_code, resp.text)
        return ok
    except OSError as exc:
        print("POST-Fehler:", exc)
        return False
    finally:
        if resp is not None:
            resp.close()


def _wifi_up(cfg, timeout_s=20):
    import network

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(cfg["wifi_ssid"], cfg["wifi_pass"])
        deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
        while not wlan.isconnected():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                return None
            time.sleep_ms(200)
    return wlan


def _sync_time():
    """NTP-Zeit (nur WLAN/Bench — am 1NCE-APN ist UDP/123 blockiert)."""
    import ntptime
    ntptime.host = "pool.ntp.org"
    try:
        ntptime.timeout = 5  # nicht in jedem Build vorhanden
    except AttributeError:
        pass
    for _ in range(3):
        try:
            ntptime.settime()
            return True
        except OSError:
            time.sleep(1)
    return False


def ensure_time(cfg):
    """Stellt eine gueltige RTC (UTC) sicher, BEVOR gemessen und gestempelt
    wird — sonst tragen Messwerte falsche Timestamps und der Ingest verwirft
    sie (Backend toleriert nur +/-24h Drift).

    Die RTC ueberlebt den Deep-Sleep, daher ist das nur nach Power-Verlust
    noetig. Quelle: Netzzeit (LTE/NITZ), bzw. NTP nur im WLAN-Bench-Betrieb."""
    if unix_now() >= TIME_VALID:
        return True
    transport = select_transport(cfg)
    if transport == "wifi":
        wlan = _wifi_up(cfg)
        if wlan:
            try:
                _sync_time()
            finally:
                try:
                    wlan.disconnect()
                    wlan.active(False)
                except OSError:
                    pass
    else:
        from modem import Modem
        m = Modem()
        if m.power_on():
            try:
                m.configure_network(cfg.get("network_mode", "auto"))
                if m.sim_ready():
                    m.sync_time()
            finally:
                m.power_off()
    return unix_now() >= TIME_VALID


def _send_via_wifi(cfg, measurements):
    wlan = _wifi_up(cfg)
    if not wlan:
        print("WLAN nicht verbunden")
        return False
    try:
        return _post(cfg, measurements)
    finally:
        try:
            wlan.disconnect()
            wlan.active(False)
        except OSError:
            pass


def _send_via_lte(cfg, measurements):
    """Sendet per Mobilfunk via SIM7000-AT-HTTP (kein PPP)."""
    from modem import Modem

    m = Modem()
    if not m.power_on():
        print("Modem antwortet nicht")
        return False
    try:
        m.configure_network(cfg.get("network_mode", "auto"))
        if not m.sim_ready():
            print("SIM nicht bereit")
            return False
        ip = m.data_connect(cfg.get("apn", ""))
        if not ip:
            print("Kein Datenkontext (Netz/SIM pruefen)")
            return False
        print("LTE-IP:", ip)
        headers = {
            "Content-Type": "application/json",
            "X-Device-ID": cfg["device_id"],
            "X-API-Key": cfg["api_key"],
            "X-Tenant-ID": cfg["tenant_id"],
        }
        payload = json.dumps({"m": measurements})
        code = m.http_post(cfg["ingest_url"], headers, payload)
        print("Ingest HTTP:", code)
        return code in (200, 201)
    finally:
        m.data_disconnect()
        m.power_off()


def send_batch(cfg, buf):
    measurements = [[sid, ts, val] for (sid, ts, val) in buf][:60]
    transport = select_transport(cfg)
    print("Sende per", transport, "-", len(measurements), "Messwerte")
    if transport == "wifi":
        return _send_via_wifi(cfg, measurements)
    return _send_via_lte(cfg, measurements)


# --- Captive-Portal bei Kaltstart -----------------------------------------
def maybe_run_portal(cfg):
    """Faehrt bei Kaltstart das Konfig-Portal hoch. Liefert True wenn neu
    konfiguriert wurde (Aufrufer startet dann neu)."""
    import captive

    modem = None
    if cfg.get("gps_enabled") and select_transport(cfg) == "lte":
        # Modem fuer Status-/GPS-Anzeige im Portal einschalten (nur Kaltstart).
        try:
            from modem import Modem
            modem = Modem()
            if modem.power_on():
                modem.configure_network(cfg.get("network_mode", "auto"))
                modem.gps_power(True)
            else:
                modem = None
        except (OSError, ImportError):
            modem = None

    saved = captive.run(cfg, modem=modem, window_s=60)
    if modem is not None:
        modem.power_off()
    return saved


# --- Hauptablauf -----------------------------------------------------------
def run():
    # 1. Kaltstart -> Konfig-Portal (Erstconfig blockierend, sonst 60s-Fenster).
    if is_cold_boot():
        if maybe_run_portal(CFG):
            print("Neu konfiguriert -> Neustart")
            machine.reset()

    # Ohne gueltige Config nichts senden (z. B. nach Erst-Flash, Portal abgebrochen).
    if not is_configured(CFG):
        print("Nicht konfiguriert -> Deep-Sleep")
        machine.deepsleep(CFG["measure_interval_s"] * 1000)
        return

    # 2. Zeit sicherstellen, BEVOR gemessen wird. Ohne gueltige UTC-Zeit keine
    #    Messung — sonst falsche Timestamps und der Ingest verwirft die Werte.
    #    Die RTC ueberlebt den Deep-Sleep, daher nur nach Power-Verlust noetig.
    if unix_now() < TIME_VALID:
        if not ensure_time(CFG):
            print("Keine gueltige Zeit -> nicht messen, kurzer Retry")
            machine.deepsleep(TIME_RETRY_S * 1000)
            return

    # 3. Messen (jetzt mit gueltiger UTC-Zeit).
    buf = _load_buffer()
    readings = sensors.read_all(CFG)
    if readings:
        ts = int(unix_now())
        for sensor_id, value in readings:
            buf.append([sensor_id, ts, value])
        print("Messung:", readings, "Buffer:", len(buf))

    # 4. Senden wenn Batch voll.
    if len(buf) >= CFG["batch_size"]:
        if send_batch(CFG, buf):
            buf = []
        else:
            buf = buf[-60:]  # Ueberlauf begrenzen, naechster Zyklus erneut
    _save_buffer(buf)

    # 5. Deep-Sleep bis zur naechsten Messung.
    interval = CFG["measure_interval_s"]
    print("Deep-Sleep:", interval, "s")
    machine.deepsleep(interval * 1000)


if __name__ == "__main__":
    run()
