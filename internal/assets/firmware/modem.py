# SIM7000G-Mobilfunkmodem (LILYGO T-SIM7000G) — MicroPython.
#
# Sendet per AT-HTTP (SIM7000 SH-Befehle) statt PPP — PPP ist auf SIM7000 +
# MicroPython unzuverlaessig, der SIM7000 hat eine eingebaute TCP/IP+HTTP-Engine.
#
# Aufgaben:
#   - Modem an/aus (PWRKEY-Puls), Netzmodus konfigurieren
#   - Status: SIM (CPIN), Signal (CSQ), Netz/Operator (COPS), Registrierung
#   - GNSS-Position (CGNSPWR/CGNSINF)
#   - Datenkontext aktivieren (CNACT) + HTTP-POST (SHCONF/SHCONN/SHBOD/SHREQ)
#
# STATUS: Status/GPS am echten Board verifiziert. Der HTTP-POST-Pfad
# (data_connect + http_post) ist gegen die SIM7000-SH-Spezifikation
# implementiert, aber am Teststandort NICHT verifizierbar (kein Datenkontext:
# 2G ohne 1NCE-Daten / LTE-M ohne Abdeckung). Im Feld mit Datenempfang testen.

import time

from machine import UART, Pin

import board

CSQ_UNKNOWN = 99


def _csq_to_dbm(csq):
    if csq is None or csq >= CSQ_UNKNOWN:
        return None
    return -113 + 2 * csq


# Netzmodus-Profile: (CNMP, CMNB)
# CNMP 2=auto, 38=LTE only, 13=GSM only ; CMNB 1=CAT-M, 2=NB-IoT, 3=beide
_NET_MODES = {
    "auto": (2, 3),
    "ltem": (38, 1),
    "nbiot": (38, 2),
    "gsm": (13, 3),
}


class Modem:
    def __init__(self):
        self.uart = UART(
            board.MODEM_UART,
            baudrate=board.MODEM_BAUD,
            tx=board.MODEM_TX,
            rx=board.MODEM_RX,
            timeout=1000,
        )
        self._pwrkey = Pin(board.MODEM_PWRKEY, Pin.OUT)

    # --- Stromversorgung ---------------------------------------------------
    def power_on(self):
        self._pwrkey.value(1)
        time.sleep_ms(100)
        self._pwrkey.value(0)
        time.sleep_ms(1200)
        self._pwrkey.value(1)
        return self._wait_at(timeout_s=15)

    def power_off(self):
        try:
            self.at("AT+CPOWD=1", timeout_ms=2000)
        except OSError:
            pass

    def configure_network(self, mode="auto"):
        """Setzt bevorzugten Funk-Modus (auto/ltem/nbiot/gsm)."""
        cnmp, cmnb = _NET_MODES.get(mode, _NET_MODES["auto"])
        for cmd in ("AT+CNMP=%d" % cnmp, "AT+CMNB=%d" % cmnb):
            try:
                self.at(cmd, timeout_ms=3000)
            except OSError:
                pass

    # --- AT-Helfer ---------------------------------------------------------
    def at(self, cmd, timeout_ms=2000, expect="OK"):
        self._drain()
        self.uart.write(cmd + "\r\n")
        return self._read_until(expect, timeout_ms, cmd)

    def _read_until(self, expect, timeout_ms, cmd):
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        buf = b""
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            chunk = self.uart.read()
            if chunk:
                buf += chunk
                if expect and expect.encode() in buf:
                    return buf.decode("utf-8", "ignore")
                if b"ERROR" in buf:
                    raise OSError("AT ERROR: %s -> %s" % (cmd, buf))
            else:
                time.sleep_ms(20)
        raise OSError("AT timeout: %s" % cmd)

    def _wait_at(self, timeout_s=15):
        deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            try:
                self.at("AT", timeout_ms=1000)
                return True
            except OSError:
                time.sleep_ms(500)
        return False

    def _drain(self):
        while self.uart.read():
            pass

    # --- Status ------------------------------------------------------------
    def sim_ready(self):
        try:
            return "READY" in self.at("AT+CPIN?", timeout_ms=3000)
        except OSError:
            return False

    def signal_dbm(self):
        try:
            resp = self.at("AT+CSQ", timeout_ms=2000)
        except OSError:
            return None
        idx = resp.find("+CSQ:")
        if idx < 0:
            return None
        try:
            rssi = int(resp[idx + 5:].split(",")[0])
        except (ValueError, IndexError):
            return None
        return _csq_to_dbm(rssi)

    def operator(self):
        try:
            resp = self.at("AT+COPS?", timeout_ms=5000)
        except OSError:
            return None
        start = resp.find('"')
        end = resp.find('"', start + 1)
        if start >= 0 and end > start:
            return resp[start + 1:end]
        return None

    def registered(self):
        """True wenn (Daten-)registriert. Prueft CEREG (LTE) und CGREG (2G)."""
        for cmd in ("AT+CEREG?", "AT+CGREG?"):
            try:
                resp = self.at(cmd, timeout_ms=3000)
            except OSError:
                continue
            tag = cmd[3:8]  # CEREG / CGREG
            idx = resp.find("+" + tag)
            if idx < 0:
                continue
            parts = resp[idx + 6:].split(",")
            if len(parts) >= 2:
                try:
                    stat = int(parts[1].strip().split()[0])
                except (ValueError, IndexError):
                    continue
                if stat in (1, 5):
                    return True
        return False

    def status(self):
        return {
            "sim": self.sim_ready(),
            "signal_dbm": self.signal_dbm(),
            "operator": self.operator(),
            "registered": self.registered(),
        }

    # --- GNSS / GPS --------------------------------------------------------
    def gps_power(self, on):
        try:
            self.at("AT+CGNSPWR=%d" % (1 if on else 0), timeout_ms=3000)
            return True
        except OSError:
            return False

    def gps_location(self):
        try:
            resp = self.at("AT+CGNSINF", timeout_ms=3000)
        except OSError:
            return {"fix": False}
        idx = resp.find("+CGNSINF:")
        if idx < 0:
            return {"fix": False}
        fields = resp[idx + 9:].split(",")
        if len(fields) < 5 or fields[1].strip() != "1":
            return {"fix": False}
        try:
            lat = float(fields[3])
            lon = float(fields[4])
        except (ValueError, IndexError):
            return {"fix": False}
        sats = None
        if len(fields) > 14:
            try:
                sats = int(fields[14])
            except (ValueError, IndexError):
                sats = None
        return {"fix": True, "lat": lat, "lon": lon, "sats": sats}

    # --- Datenkontext (CNACT) ----------------------------------------------
    def data_connect(self, apn, timeout_s=60):
        """Aktiviert den App-Datenkontext, liefert die IP (oder None).

        Setzt APN per CGDCONT, aktiviert via CNACT und pollt CNACT? auf eine
        IP != 0.0.0.0.
        """
        try:
            self.at('AT+CGDCONT=1,"IP","%s"' % apn, timeout_ms=5000)
        except OSError:
            return None
        try:
            self.at('AT+CNACT=1,"%s"' % apn, timeout_ms=3000)
        except OSError:
            pass

        deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            ip = self._cnact_ip()
            if ip and ip != "0.0.0.0":
                return ip
            time.sleep_ms(1000)
        return None

    def _cnact_ip(self):
        try:
            resp = self.at("AT+CNACT?", timeout_ms=3000)
        except OSError:
            return None
        a = resp.find('"')
        b = resp.find('"', a + 1)
        if a >= 0 and b > a:
            return resp[a + 1:b]
        return None

    def data_disconnect(self):
        try:
            self.at("AT+CNACT=0", timeout_ms=5000)
        except OSError:
            pass

    def sync_rtc(self):
        """Setzt die ESP32-RTC. Reihenfolge: GPS (atomgenau, kein Netz noetig)
        -> Netzzeit (NITZ/CCLK). True bei Erfolg.

        NTP ist BEWUSST nicht dabei: UDP/123 ist ueber den 1NCE-APN blockiert
        (am Board verifiziert: +CNTP: 61 zu 4 versch. Anycast-IPs). GPS ist am
        Einsatzort (Gewaesser, freier Himmel) die zuverlaessige Quelle; ein
        Cold-Fix kann aber Minuten dauern. CCLK haengt von NITZ ab (oft leer).
        """
        import machine

        # 1. GPS-Zeit (UTC aus CGNSINF, sobald Fix vorhanden).
        self.gps_power(True)
        t = self._gps_time()
        if t:
            machine.RTC().datetime((t[0], t[1], t[2], 0, t[3], t[4], t[5], 0))
            return True

        # 2. Netzzeit (NITZ) als Fallback.
        return self._cclk_to_rtc()

    def _gps_time(self):
        """UTC-Tupel (Y,M,D,h,m,s) aus GPS oder None (kein Fix)."""
        try:
            resp = self.at("AT+CGNSINF", timeout_ms=3000)
        except OSError:
            return None
        idx = resp.find("+CGNSINF:")
        if idx < 0:
            return None
        f = resp[idx + 9:].split(",")
        if len(f) < 3 or f[1].strip() != "1":
            return None
        u = f[2].strip()  # yyyyMMddhhmmss.sss
        if len(u) < 14:
            return None
        try:
            return (int(u[0:4]), int(u[4:6]), int(u[6:8]),
                    int(u[8:10]), int(u[10:12]), int(u[12:14]))
        except ValueError:
            return None

    def _cclk_to_rtc(self):
        """Setzt RTC aus AT+CCLK. Verwirft unplausible Jahre (<2020)."""
        try:
            resp = self.at("AT+CCLK?", timeout_ms=3000)
        except OSError:
            return False
        a = resp.find('"')
        b = resp.find('"', a + 1)
        if a < 0 or b <= a:
            return False
        s = resp[a + 1:b]  # "yy/MM/dd,hh:mm:ss+zz"
        try:
            date, rest = s.split(",")
            yy, mo, dd = date.split("/")
            hh, mm, ss = rest[:8].split(":")
            year = 2000 + int(yy)
            if year < 2020:  # NITZ nicht gesetzt -> unbrauchbar
                return False
            import machine
            machine.RTC().datetime((year, int(mo), int(dd), 0, int(hh), int(mm), int(ss), 0))
            return True
        except (ValueError, IndexError):
            return False

    # --- HTTP-POST (SIM7000 SH-Engine) -------------------------------------
    def http_post(self, url, headers, body, timeout_s=30):
        """POSTet body an url mit headers. Liefert HTTP-Statuscode oder None.

        Setzt einen aktiven Datenkontext voraus (data_connect()).
        """
        scheme, _, rest = url.partition("://")
        slash = rest.find("/")
        if slash < 0:
            host, path = rest, "/"
        else:
            host, path = rest[:slash], rest[slash:]
        base = scheme + "://" + host

        try:
            self.at('AT+SHCONF="URL","%s"' % base, timeout_ms=3000)
            self.at('AT+SHCONF="BODYLEN",1024', timeout_ms=3000)
            self.at('AT+SHCONF="HEADERLEN",350', timeout_ms=3000)
            if scheme == "https":
                # TLS 1.2, Cert-Zeitpruefung ignorieren (RTC oft ungesetzt),
                # SNI auf den Host (noetig bei Shared-Hosts/CDNs).
                try:
                    self.at('AT+CSSLCFG="sslversion",1,3', timeout_ms=3000)
                    self.at('AT+CSSLCFG="ignorertctime",1,1', timeout_ms=3000)
                    self.at('AT+CSSLCFG="sni",1,"%s"' % host, timeout_ms=3000)
                    self.at('AT+SHSSL=1,""', timeout_ms=3000)
                except OSError:
                    pass
            self.at("AT+SHCONN", timeout_ms=timeout_s * 1000)
        except OSError as exc:
            print("SHCONN-Fehler:", exc)
            return None

        try:
            self.at("AT+SHCHEAD", timeout_ms=3000)
            for key, val in headers.items():
                self.at('AT+SHAHEAD="%s","%s"' % (key, val), timeout_ms=3000)
            self._sh_body(body)
            resp = self.at('AT+SHREQ="%s",3' % path, timeout_ms=timeout_s * 1000, expect="+SHREQ:")
            return self._parse_shreq(resp)
        except OSError as exc:
            print("SHREQ-Fehler:", exc)
            return None
        finally:
            try:
                self.at("AT+SHDISC", timeout_ms=5000)
            except OSError:
                pass

    def _sh_body(self, body):
        # SIM7000 SHBOD nimmt den Body INLINE: AT+SHBOD="<body>",<len> (max 1024).
        # Innere Anfuehrungszeichen muessen escaped werden (\"); <len> ist die
        # echte (unescapte) Byte-Laenge.
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        length = len(body.encode("utf-8"))
        esc = body.replace('"', '\\"')
        self._drain()
        self.uart.write('AT+SHBOD="%s",%d\r\n' % (esc, length))
        self._read_until("OK", 5000, "SHBOD")

    @staticmethod
    def _parse_shreq(resp):
        # Antwort: +SHREQ: "POST",<status>,<datalen>
        idx = resp.find("+SHREQ:")
        if idx < 0:
            return None
        parts = resp[idx + 7:].split(",")
        if len(parts) >= 2:
            try:
                return int(parts[1].strip())
            except ValueError:
                return None
        return None
