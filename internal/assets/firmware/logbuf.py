# Kleiner Log-Ringpuffer — haelt die letzten Meldungen und schreibt sie 1x pro
# Wachzyklus nach Flash (log.txt), damit sie den Deep-Sleep ueberleben und im
# Go-Tool bzw. auf der Status-Seite sichtbar sind.
#
# log() druckt zusaetzlich auf die serielle Konsole (wie das alte print), damit
# Live-Mitlesen am USB weiter funktioniert.

import time

LOG_PATH = "log.txt"
MAX_LINES = 100

_ring = []


def _stamp():
    """HH:MM:SS wenn die RTC gesetzt ist, sonst relative Sekunden seit Boot."""
    t = time.time()
    if t > 650_000_000:  # > 2020 (RTC-Epoche ab 2000) => Zeit gueltig
        tm = time.localtime(t)
        return "%02d:%02d:%02d" % (tm[3], tm[4], tm[5])
    return "+%ds" % (time.ticks_ms() // 1000)


def _trim():
    if len(_ring) > MAX_LINES:
        del _ring[0:len(_ring) - MAX_LINES]


def log(*args):
    """Loggt eine Meldung: serielle Ausgabe + Eintrag in den Ringpuffer."""
    msg = " ".join(str(a) for a in args)
    line = "%s %s" % (_stamp(), msg)
    print(line)
    _ring.append(line)
    _trim()


def lines(n=None):
    """Liefert die letzten n Zeilen (oder alle)."""
    if n is None or n >= len(_ring):
        return list(_ring)
    return _ring[-n:]


def load(path=LOG_PATH):
    """Liest die persistierten Zeilen beim Boot in den RAM-Ring."""
    try:
        with open(path) as fh:
            for ln in fh:
                ln = ln.rstrip("\n")
                if ln:
                    _ring.append(ln)
    except OSError:
        pass
    _trim()


def flush(path=LOG_PATH):
    """Schreibt den (getrimmten) Ring nach Flash. 1x pro Wachzyklus aufrufen."""
    try:
        with open(path, "w") as fh:
            fh.write("\n".join(_ring[-MAX_LINES:]))
            if _ring:
                fh.write("\n")
    except OSError:
        pass
