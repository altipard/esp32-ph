# Captive-Portal zur Konfiguration ueber das Handy — MicroPython.
#
# Faehrt einen WLAN-Access-Point (WPA2) hoch, faengt per DNS-Catch-all alle
# Anfragen ab und zeigt ein Konfigurationsformular. Speichert config.json und
# startet das Board neu.
#
# Aufruf aus main.py:
#   - Erstkonfiguration (noch keine config): bleibt offen bis gespeichert.
#   - Re-Konfiguration nach Kaltstart/Reset: ~60s sichtbar, geht aus wenn
#     niemand verbindet (Strom sparen).
#
# Bewusst NICHT bei jedem Deep-Sleep-Wakeup starten (Akku!). Steuerung in main.
#
# STATUS: UNGETESTET auf Hardware. Standard-AP/DNS/HTTP-Muster fuer ESP32.

import json
import time

import machine
import network
import socket

import board
from logbuf import log

AP_IP = "192.168.4.1"
CONFIG_PATH = "config.json"

# Formular nach Themen gruppiert: (Gruppentitel, [(key, label, typ), ...]).
# typ: "text" | "password" | "number"
GROUPS = (
    ("WLAN", (
        ("wifi_ssid", "WLAN-Name", "text"),
        ("wifi_pass", "WLAN-Passwort", "password"),
    )),
    ("Mobilfunk (LTE)", (
        ("transport", "Transport (lte / wifi / auto)", "text"),
        ("apn", "APN", "text"),
        ("apn_user", "APN-Benutzer", "text"),
        ("apn_pass", "APN-Passwort", "password"),
    )),
    ("Backend", (
        ("tenant", "Verein (Kuerzel, z. B. fvw)", "text"),
        ("device_id", "Device-ID", "text"),
        ("api_key", "API-Key", "password"),
    )),
    ("Messung", (
        ("measure_interval_s", "Messintervall (Sekunden)", "number"),
        ("batch_size", "Senden nach N Messungen", "number"),
    )),
)
INT_FIELDS = ("measure_interval_s", "batch_size")

# Eigene Petri-Heil-Akzente zusaetzlich zu Pure.css.
BRAND_CSS = (
    "body{background:#eef2f0;margin:0}"
    ".wrap{max-width:560px;margin:0 auto;padding:16px}"
    ".card{background:#fff;border-radius:12px;padding:18px;margin-bottom:16px;"
    "box-shadow:0 1px 4px rgba(0,0,0,.08)}"
    "header{text-align:center;padding:18px 0}"
    "header img{width:84px;height:84px}"
    "header h1{color:#15604a;font-size:1.25em;margin:8px 0 0}"
    "fieldset{border:0;padding:0;margin:0 0 8px}"
    "legend{font-weight:700;color:#15604a;font-size:1.05em;margin-bottom:6px}"
    ".pure-button-primary{background:#15604a}"
    ".pure-form input{border-radius:8px}"
    ".pure-form label{font-weight:600;margin-top:6px}"
    ".hint{color:#777;font-size:.8em;margin:-4px 0 8px}"
    ".msg{background:#dff0e8;color:#15604a;border-radius:8px;padding:10px;margin-bottom:12px}"
    "a{color:#15604a}"
    ".full{width:100%}"
)


def _led(on):
    try:
        machine.Pin(board.LED, machine.Pin.OUT).value(1 if on else 0)
    except (ValueError, OSError):
        pass


def _ssid(ap):
    """Geraetespezifische SSID aus der MAC: PH-<letzte 3 MAC-Bytes>, z. B.
    PH-5BFF6C. Eindeutig pro Board, auch ohne Konfiguration."""
    try:
        mac = ap.config("mac")
        return "PH-" + "".join("%02X" % b for b in mac[3:])
    except (OSError, ValueError):
        return "PH-Sensor"


def start_ap(config):
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ssid = _ssid(ap)
    pwd = config.get("ap_password") or "petriheil"
    try:
        ap.config(essid=ssid, password=pwd, authmode=network.AUTH_WPA2_PSK)
    except OSError:
        ap.config(essid=ssid)  # Fallback offen, falls authmode nicht geht
    # IP fest auf AP_IP.
    try:
        ap.ifconfig((AP_IP, "255.255.255.0", AP_IP, AP_IP))
    except OSError:
        pass
    return ap, ssid


# --- DNS-Catch-all ---------------------------------------------------------
def _dns_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    s.bind(("0.0.0.0", 53))
    return s


def _handle_dns(sock):
    """Beantwortet jede DNS-A-Anfrage mit AP_IP (Captive-Portal-Trick)."""
    try:
        data, addr = sock.recvfrom(256)
    except OSError:
        return
    if len(data) < 12:
        return
    # Antwort-Header: ID uebernehmen, Flags=0x8180, 1 Frage, 1 Antwort.
    txid = data[:2]
    resp = txid + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
    resp += data[12:]  # Frage zurueckspiegeln
    # Antwort-Record: Name-Pointer, Typ A, Klasse IN, TTL, Laenge 4, IP.
    resp += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04"
    resp += bytes(int(p) for p in AP_IP.split("."))
    try:
        sock.sendto(resp, addr)
    except OSError:
        pass


# --- HTTP ------------------------------------------------------------------
def _page(inner, refresh_s=None):
    """Rahmt Inhalt in eine mobile Seite (Pure.css + Petri-Heil-Branding).

    refresh_s setzt ein Auto-Refresh — auf der Status-Seite dient das zugleich
    als Herzschlag, der das Portal offen haelt, solange der Tab offen ist."""
    refresh = ("<meta http-equiv='refresh' content='%d'>" % refresh_s) if refresh_s else ""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        + refresh +
        "<title>Petri-Heil Sensor</title>"
        "<link rel='stylesheet' href='/pure.css'>"
        "<style>" + BRAND_CSS + "</style></head><body><div class='wrap'>"
        "<header><img src='/logo.png' alt='Petri-Heil'><h1>Sensor einrichten</h1></header>"
        + inner +
        "</div></body></html>"
    )


def _field(key, label, typ, value):
    itype = "password" if typ == "password" else ("number" if typ == "number" else "text")
    return (
        "<label for='%s'>%s</label>"
        "<input class='full' id='%s' type='%s' name='%s' value='%s'>"
        % (key, label, key, itype, key, value)
    )


def _html(config, msg=""):
    sections = []
    for title, fields in GROUPS:
        rows = [_field(k, lbl, t, config.get(k, "")) for (k, lbl, t) in fields]
        sections.append("<fieldset><legend>%s</legend>%s</fieldset>" % (title, "".join(rows)))

    # Sensor-Gruppe (haeufigster Fall: ein Temperaturfuehler).
    s0 = (config.get("sensors") or [{}])[0]
    sensor = (
        _field("s_id", "Sensor-ID", "text", s0.get("sensor_id", "temp-1"))
        + _field("s_type", "Typ", "text", s0.get("type", "ds18b20"))
        + _field("s_pin", "GPIO (T-SIM7000G: nicht 4!)", "number", s0.get("pin", board.DEFAULT_ONEWIRE_PIN))
    )
    sections.append("<fieldset><legend>Sensor</legend>%s</fieldset>" % sensor)

    banner = ("<div class='msg'>%s</div>" % msg) if msg else ""
    form = (
        "<form class='pure-form pure-form-stacked card' method='POST' action='/save'>"
        + "".join(sections)
        + "<button type='submit' class='pure-button pure-button-primary full'>"
          "Speichern &amp; Neustart</button></form>"
        "<p class='card' style='text-align:center'>"
        "<a href='/status'>&#8505; Ger&auml;tezustand (Vitalwerte / Log)</a></p>"
    )
    return _page(banner + form)


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;")


def _status_html(config, modem, vitals=None):
    """Rendert den Gerätezustand: Geräte-Vitals, Energie, Mobilfunk, Daten, Log.
    vitals ist eine Funktion (oder dict) vom Aufrufer; modem optional."""
    try:
        v = vitals() if callable(vitals) else (vitals or {})
    except Exception:
        v = {}
    rows = ["<h3>Ger&auml;t</h3>"]
    rows.append("<p><b>Firmware:</b> %s</p>" % _esc(v.get("fw", "?")))
    up = v.get("uptime_s")
    if up is not None:
        rows.append("<p><b>Uptime:</b> %dh %02dm</p>" % (up // 3600, (up % 3600) // 60))
    if v.get("ram_free") is not None:
        rows.append("<p><b>Speicher frei:</b> %d KB</p>" % (v["ram_free"] // 1024))
    if v.get("chip_c") is not None:
        rows.append("<p><b>Chip-Temp:</b> %d &deg;C</p>" % v["chip_c"])
    if v.get("time_utc"):
        rows.append("<p><b>Zeit:</b> %s</p>" % _esc(v["time_utc"]))

    rows.append("<h3>Energie</h3>")
    bat = v.get("battery")
    rows.append("<p><b>Batterie:</b> %s</p>" % (("%d %%" % bat) if bat is not None else "kein Akku / unbekannt"))

    rows.append("<h3>Mobilfunk</h3>")
    if modem is None:
        rows.append("<p>kein Modem (WLAN-Modus / Modem aus)</p>")
    else:
        st = modem.status()
        rows.append("<p><b>SIM:</b> %s</p>" % ("OK" if st["sim"] else "kein/Fehler"))
        sig = st["signal_dbm"]
        rows.append("<p><b>Signal:</b> %s</p>" % (("%d dBm" % sig) if sig is not None else "unbekannt"))
        rows.append("<p><b>Netz:</b> %s</p>" % _esc(st["operator"] or "n/a"))
        rows.append("<p><b>Registriert:</b> %s</p>" % ("ja" if st["registered"] else "nein"))
        if config.get("gps_enabled"):
            gps = modem.gps_location()
            if gps.get("fix"):
                rows.append("<p><b>GPS:</b> %.5f, %.5f (Sats %s)</p>" % (gps["lat"], gps["lon"], gps.get("sats")))
            else:
                rows.append("<p><b>GPS:</b> kein Fix</p>")

    rows.append("<h3>Daten</h3>")
    rows.append("<p><b>Gepuffert:</b> %s Messwerte</p>" % _esc(v.get("buffered", "?")))
    rows.append("<p><b>Letzter Versand:</b> %s</p>" % _esc(v.get("send", "-")))

    body = "<div class='card'>" + "".join(rows) + "</div>"
    loglines = v.get("log") or []
    if loglines:
        body += (
            "<div class='card'><h3>Letzte Meldungen</h3>"
            "<pre style='white-space:pre-wrap;font-size:.8em;margin:0'>%s</pre></div>"
            % _esc("\n".join(loglines))
        )
    body += "<p style='text-align:center'><a href='/'>&#8592; zur&uuml;ck</a></p>"
    return _page(body, refresh_s=5)


def _urldecode(s):
    s = s.replace("+", " ")
    out = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                out += chr(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out += s[i]
        i += 1
    return out


def _parse_post(body):
    data = {}
    for pair in body.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            data[_urldecode(k)] = _urldecode(v)
    return data


def _apply(config, form):
    for _title, fields in GROUPS:
        for key, _label, _typ in fields:
            if key in form:
                val = form[key]
                if key in INT_FIELDS:
                    try:
                        val = int(val)
                    except ValueError:
                        continue
                config[key] = val
    # Sensor 1.
    try:
        pin = int(form.get("s_pin", board.DEFAULT_ONEWIRE_PIN))
    except ValueError:
        pin = board.DEFAULT_ONEWIRE_PIN
    config["sensors"] = [{
        "sensor_id": form.get("s_id", "temp-1"),
        "type": form.get("s_type", "ds18b20"),
        "pin": pin,
    }]
    return config


def _save(config):
    with open(CONFIG_PATH, "w") as fh:
        json.dump(config, fh)


def _http_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 80))
    s.listen(2)
    s.settimeout(0.5)
    return s


def _send_all(cl, data):
    """Sendet data vollstaendig (socket.send kann partiell sein)."""
    mv = memoryview(data)
    while mv:
        try:
            n = cl.send(mv)
        except OSError:
            return
        if not n:
            return
        mv = mv[n:]


def _send_file(cl, name, ctype):
    try:
        with open(name, "rb") as fh:
            data = fh.read()
    except OSError:
        _send_all(cl, b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
        return
    hdr = ("HTTP/1.1 200 OK\r\nContent-Type: %s\r\n"
           "Cache-Control: max-age=86400\r\nConnection: close\r\n\r\n" % ctype)
    _send_all(cl, hdr.encode())
    _send_all(cl, data)


def _send_page(cl, page):
    _send_all(cl, b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
    _send_all(cl, page.encode("utf-8"))


def _serve_once(http, config, modem, vitals=None):
    """Bedient eine HTTP-Verbindung. Liefert (saved, had_client)."""
    try:
        cl, _ = http.accept()
    except OSError:
        return False, False
    saved = False
    try:
        cl.settimeout(3)
        req = cl.recv(4096)
        if not req:
            return False, True
        text = req.decode("utf-8", "ignore")
        line = text.split("\r\n", 1)[0]
        parts = line.split(" ")
        method = parts[0] if parts else "GET"
        path = parts[1] if len(parts) > 1 else "/"

        if path.startswith("/pure.css"):
            _send_file(cl, "pure-min.css", "text/css")
        elif path.startswith("/logo.png"):
            _send_file(cl, "logo.png", "image/png")
        elif method == "POST" and path.startswith("/save"):
            body = text.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in text else ""
            _apply(config, _parse_post(body))
            _save(config)
            _send_page(cl, _ok_page())
            saved = True
        elif path.startswith("/status"):
            _send_page(cl, _status_html(config, modem, vitals))
        else:
            _send_page(cl, _html(config))
    except OSError:
        pass
    finally:
        cl.close()
    return saved, True


def _ok_page():
    return _page(
        "<div class='card' style='text-align:center'>"
        "<h2 style='color:#15604a'>Gespeichert &#10003;</h2>"
        "<p>Board startet neu und beginnt zu messen.</p></div>"
    )


def run(config, modem=None, vitals=None, window_s=60):
    """Startet das Portal.

    Hat das Board noch keine gueltige Konfiguration (kein device_id/tenant),
    bleibt das Portal offen bis gespeichert wird.

    Sonst gilt ein Idle-Timeout: jeder Client-Zugriff verlaengert das Fenster um
    window_s. Niemand verbindet -> nach window_s aus. Solange jemand die
    Status-Seite offen hat (Auto-Refresh alle 5s = Herzschlag), bleibt es offen;
    Tab zu -> nach window_s in Deep-Sleep (Akku schonen).

    Liefert True, wenn eine neue Konfiguration gespeichert wurde (Aufrufer
    sollte dann neu starten).
    """
    unconfigured = not (config.get("device_id") and (config.get("tenant") or config.get("ingest_url")))
    ap, ssid = start_ap(config)
    log("Captive-Portal aktiv:", ssid, "->", AP_IP)
    _led(True)

    dns = _dns_socket()
    http = _http_socket()
    deadline = time.ticks_add(time.ticks_ms(), window_s * 1000)
    saved = False
    try:
        while True:
            _handle_dns(dns)
            s, client = _serve_once(http, config, modem, vitals)
            if client:
                # Aktivitaet -> Fenster verlaengern (Idle-Timeout).
                deadline = time.ticks_add(time.ticks_ms(), window_s * 1000)
            if s:
                saved = True
                break
            if not unconfigured and time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                log("Captive-Portal: Leerlauf -> aus")
                break
    finally:
        try:
            dns.close()
            http.close()
            ap.active(False)
        except OSError:
            pass
        _led(False)
    return saved
