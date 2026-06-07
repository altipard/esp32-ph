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
#   POST <ingest_url>  Header X-Device-ID/X-API-Key (Tenant via Subdomain)
#   Body {"m": [["sensor-id", <unix_ts>, <wert_x10>], ...]}
#
# STATUS: LTE-Pfad am echten Board verifiziert (Netzzeit -> Datenkontext ->
# HTTPS-POST -> Wert im Backend). Der DS18B20-Lesepfad ist noch ungetestet
# (kein Sensor angeschlossen).

import json
import time

import machine

import board
import sensors
import logbuf
from logbuf import log

CONFIG_PATH = "config.json"
EPOCH_OFFSET = 946684800  # RTC zaehlt ab 2000, Unix ab 1970
TIME_VALID = 1_600_000_000  # > 2020 => RTC gesetzt
TIME_RETRY_S = 60  # kurzer Deep-Sleep, wenn keine Zeit ermittelbar war

# Ingest-URL wird aus dem Vereinskuerzel (tenant) gebaut:
#   https://<tenant>.petri-heil.online/water-monitoring/api/v1/ingest/
# Der Tenant wird serverseitig ueber die Subdomain aufgeloest -> kein Header.
INGEST_HOST_SUFFIX = ".petri-heil.online"
INGEST_PATH = "/water-monitoring/api/v1/ingest/"

# Batteriestand: hoechstens einmal pro Tag mitsenden (haengt am naechsten Batch).
BATTERY_INTERVAL_S = 24 * 60 * 60
# LiPo/18650-Naeherung; Sense-Pin hat einen Spannungsteiler (Faktor 2).
BATTERY_FULL_V = 4.2
BATTERY_EMPTY_V = 3.3
BATTERY_DIVIDER = 2.0

# Firmware-Version (mit Git-Tag/Release synchron halten) — im Status-Portal.
VERSION = "v0.1.0"

_DEFAULTS = {
    "ap_password": "petriheil",
    "transport": "auto",          # "lte" | "wifi" | "auto"
    "wifi_ssid": "",
    "wifi_pass": "",
    "apn": "",
    "apn_user": "",
    "apn_pass": "",
    "tenant": "",                 # Vereinskuerzel (Subdomain), z. B. "fvw"
    "ingest_url": "",             # optionaler Dev-Override; sonst aus tenant gebaut
    "device_id": "",
    "api_key": "",
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
        log("config.json nicht lesbar:", exc)
    return cfg


CFG = load_config()
_rtc = machine.RTC()


# --- RTC-State (ueberlebt Deep-Sleep) -------------------------------------
# Haelt den Messwert-Puffer und den Zeitpunkt des letzten Batterie-Reports.
def _load_state():
    try:
        raw = _rtc.memory()
        if raw:
            data = json.loads(raw)
            if isinstance(data, list):  # alte Form: nur der Puffer
                data = {"buf": data}
        else:
            data = {}
    except (ValueError, OSError):
        data = {}
    data.setdefault("buf", [])
    data.setdefault("bat_ts", 0)
    return data


def _save_state(state):
    try:
        _rtc.memory(json.dumps(state))
    except OSError:
        pass


def read_battery_percent():
    """Liest die Batteriespannung am Sense-Pin und gibt 0-100 % (oder None).

    Lineare Naeherung zwischen BATTERY_EMPTY_V und BATTERY_FULL_V — bewusst grob,
    der ESP32-ADC ist nicht praezise. Ohne angeschlossene Zelle ~0 %."""
    try:
        adc = machine.ADC(machine.Pin(board.BAT_ADC))
        try:
            adc.atten(machine.ADC.ATTN_11DB)  # voller Messbereich
        except AttributeError:
            pass
        total = 0
        n = 5
        for _ in range(n):
            total += adc.read_uv()
            time.sleep_ms(20)
        v_bat = (total / n) / 1_000_000 * BATTERY_DIVIDER
        pct = (v_bat - BATTERY_EMPTY_V) / (BATTERY_FULL_V - BATTERY_EMPTY_V) * 100
        return int(max(0, min(100, round(pct))))
    except (OSError, ValueError, AttributeError) as exc:
        log("Batterie-Lesefehler:", exc)
        return None


def unix_now():
    return time.time() + EPOCH_OFFSET


def is_cold_boot():
    """True bei Power-on / externem Reset, False beim Deep-Sleep-Wakeup."""
    return machine.reset_cause() != machine.DEEPSLEEP_RESET


def resolve_ingest_url(cfg):
    """Liefert die Ingest-URL. Ein explizit gesetztes ingest_url (Dev/Override)
    hat Vorrang, sonst wird sie aus dem Vereinskuerzel (tenant) gebaut."""
    url = cfg.get("ingest_url")
    if url:
        return url
    tenant = cfg.get("tenant", "").strip()
    if not tenant:
        return ""
    return "https://%s%s%s" % (tenant, INGEST_HOST_SUFFIX, INGEST_PATH)


def is_configured(cfg):
    return bool(cfg.get("device_id") and resolve_ingest_url(cfg))


def select_transport(cfg):
    t = cfg.get("transport", "auto")
    if t in ("wifi", "lte"):
        return t
    return "wifi" if cfg.get("wifi_ssid") else "lte"


# --- Senden ----------------------------------------------------------------
def _build_body(measurements, battery):
    body = {"m": measurements}
    if battery is not None:
        body["bat"] = battery
    return json.dumps(body)


def _post(cfg, measurements, battery=None):
    import urequests

    headers = {
        "Content-Type": "application/json",
        "X-Device-ID": cfg["device_id"],
        "X-API-Key": cfg["api_key"],
    }
    resp = None
    try:
        resp = urequests.post(resolve_ingest_url(cfg), data=_build_body(measurements, battery), headers=headers)
        ok = resp.status_code in (200, 201)
        log("Ingest:", resp.status_code, resp.text)
        return ok
    except OSError as exc:
        log("POST-Fehler:", exc)
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


def _send_via_wifi(cfg, measurements, battery=None):
    wlan = _wifi_up(cfg)
    if not wlan:
        log("WLAN nicht verbunden")
        return False
    try:
        return _post(cfg, measurements, battery)
    finally:
        try:
            wlan.disconnect()
            wlan.active(False)
        except OSError:
            pass


def _send_via_lte(cfg, measurements, battery=None):
    """Sendet per Mobilfunk via SIM7000-AT-HTTP (kein PPP)."""
    from modem import Modem

    m = Modem()
    if not m.power_on():
        log("Modem antwortet nicht")
        return False
    try:
        m.configure_network(cfg.get("network_mode", "auto"))
        if not m.sim_ready():
            log("SIM nicht bereit")
            return False
        ip = m.data_connect(cfg.get("apn", ""))
        if not ip:
            log("Kein Datenkontext (Netz/SIM pruefen)")
            return False
        log("LTE-IP:", ip)
        headers = {
            "Content-Type": "application/json",
            "X-Device-ID": cfg["device_id"],
            "X-API-Key": cfg["api_key"],
        }
        payload = _build_body(measurements, battery)
        code = m.http_post(resolve_ingest_url(cfg), headers, payload)
        log("Ingest HTTP:", code)
        return code in (200, 201)
    finally:
        m.data_disconnect()
        m.power_off()


def send_batch(cfg, buf, battery=None):
    measurements = [[sid, ts, val] for (sid, ts, val) in buf][:60]
    transport = select_transport(cfg)
    log("Sende per", transport, "-", len(measurements), "Messwerte", "| bat:", battery)
    if transport == "wifi":
        return _send_via_wifi(cfg, measurements, battery)
    return _send_via_lte(cfg, measurements, battery)


# --- Vitalwerte fuers Status-Portal ---------------------------------------
def vitals():
    """Liefert die Geraete-Vitalwerte fuer die Status-Seite des Portals."""
    import gc

    state = _load_state()
    time_utc = None
    if unix_now() >= TIME_VALID:
        tm = time.localtime()  # RTC steht auf UTC
        time_utc = "%04d-%02d-%02d %02d:%02d:%02d UTC" % (tm[0], tm[1], tm[2], tm[3], tm[4], tm[5])

    chip_c = None
    try:
        import esp32
        chip_c = int((esp32.raw_temperature() - 32) / 1.8)  # raw ist Fahrenheit
    except (ImportError, AttributeError, OSError):
        chip_c = None

    send = "-"
    sts = state.get("send_ts")
    if sts:
        stm = time.localtime(int(sts) - EPOCH_OFFSET)
        send = "%s @ %02d:%02d" % ("OK" if state.get("send_ok") else "Fehler", stm[3], stm[4])

    return {
        "fw": VERSION,
        "uptime_s": time.ticks_ms() // 1000,
        "ram_free": gc.mem_free(),
        "chip_c": chip_c,
        "battery": read_battery_percent(),
        "buffered": len(state.get("buf", [])),
        "time_utc": time_utc,
        "send": send,
        "log": logbuf.lines(15),
    }


# --- Captive-Portal bei Kaltstart -----------------------------------------
def maybe_run_portal(cfg):
    """Faehrt bei Kaltstart das Konfig-/Status-Portal hoch. Liefert True wenn neu
    konfiguriert wurde (Aufrufer startet dann neu)."""
    import captive

    modem = None
    # Modem im Portal anlassen, damit der Status SIM/Signal/Netz zeigen kann.
    if select_transport(cfg) == "lte":
        try:
            from modem import Modem
            modem = Modem()
            if modem.power_on():
                modem.configure_network(cfg.get("network_mode", "auto"))
                if cfg.get("gps_enabled"):
                    modem.gps_power(True)
            else:
                modem = None
        except (OSError, ImportError):
            modem = None

    saved = captive.run(cfg, modem=modem, vitals=vitals, window_s=60)
    if modem is not None:
        modem.power_off()
    return saved


# --- Hauptablauf -----------------------------------------------------------
def _sleep(ms):
    """Log nach Flash sichern, dann Deep-Sleep (danach laeuft kein Code mehr)."""
    logbuf.flush()
    machine.deepsleep(ms)


def run():
    # Persistierte Log-Zeilen in den Ring laden (ueberleben den Deep-Sleep).
    logbuf.load()

    # 1. Kaltstart -> Konfig-Portal (Erstconfig blockierend, sonst 60s-Fenster).
    if is_cold_boot():
        if maybe_run_portal(CFG):
            log("Neu konfiguriert -> Neustart")
            logbuf.flush()
            machine.reset()

    # Ohne gueltige Config nichts senden (z. B. nach Erst-Flash, Portal abgebrochen).
    if not is_configured(CFG):
        log("Nicht konfiguriert -> Deep-Sleep")
        _sleep(CFG["measure_interval_s"] * 1000)
        return

    # 2. Zeit sicherstellen, BEVOR gemessen wird. Ohne gueltige UTC-Zeit keine
    #    Messung — sonst falsche Timestamps und der Ingest verwirft die Werte.
    #    Die RTC ueberlebt den Deep-Sleep, daher nur nach Power-Verlust noetig.
    if unix_now() < TIME_VALID:
        if not ensure_time(CFG):
            log("Keine gueltige Zeit -> nicht messen, kurzer Retry")
            _sleep(TIME_RETRY_S * 1000)
            return

    # 3. Messen (jetzt mit gueltiger UTC-Zeit).
    state = _load_state()
    buf = state["buf"]
    readings = sensors.read_all(CFG)
    if readings:
        ts = int(unix_now())
        for sensor_id, value in readings:
            buf.append([sensor_id, ts, value])
        log("Messung:", readings, "Buffer:", len(buf))

    # 4. Senden wenn Batch voll. Batterie hoechstens einmal pro Tag mitsenden —
    #    sie haengt sich an den naechsten regulaeren Batch (kein Extra-Funkzyklus).
    now_ts = int(unix_now())
    bat = read_battery_percent() if (now_ts - state["bat_ts"]) >= BATTERY_INTERVAL_S else None
    # Senden wenn Batch voll ODER Batterie faellig (dann auch als Heartbeat ohne
    # Messwerte — das Backend akzeptiert leeres "m" mit "bat").
    if len(buf) >= CFG["batch_size"] or bat is not None:
        ok = send_batch(CFG, buf, bat)
        state["send_ok"] = ok
        state["send_ts"] = now_ts
        if ok:
            buf = []
            if bat is not None:
                state["bat_ts"] = now_ts
        else:
            buf = buf[-60:]  # Ueberlauf begrenzen, naechster Zyklus erneut
    state["buf"] = buf
    _save_state(state)

    # 5. Deep-Sleep bis zur naechsten Messung.
    interval = CFG["measure_interval_s"]
    log("Deep-Sleep:", interval, "s")
    _sleep(interval * 1000)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # Absturz noch ins Log retten, dann kurz schlafen
        try:
            log("CRASH:", exc)
            logbuf.flush()
        except Exception:
            pass
        machine.deepsleep(TIME_RETRY_S * 1000)
