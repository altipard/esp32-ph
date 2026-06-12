# Batterie-Mathematik (pure Python, kein machine-Import -> host-testbar).
#
# Wandelt die gemessene Zellspannung in einen ehrlichen Prozentwert um.
# Eine lineare Naeherung (4.2..3.3 V) ueberschaetzt den Verbrauch im oberen
# Bereich massiv und zeigt im Li-Ion-Plateau (3.6..3.7 V) noch 30..45 %,
# obwohl die Zelle dort fast leer ist. Darum eine OCV-Kurve (Ruhespannung
# einer generischen Li-Ion/18650-Zelle) mit linearer Interpolation zwischen
# den Stuetzpunkten.

# (Spannung, Prozent) — aufsteigend sortiert.
_OCV_TABLE = (
    (3.30, 0),
    (3.40, 2),
    (3.50, 5),
    (3.60, 15),
    (3.65, 22),
    (3.70, 30),
    (3.75, 39),
    (3.80, 47),
    (3.85, 55),
    (3.90, 62),
    (3.95, 70),
    (4.00, 77),
    (4.05, 83),
    (4.10, 90),
    (4.15, 95),
    (4.20, 100),
)

# Plausibilitaetsfenster fuer eine echte Zelle. BAT_ADC (GPIO35) ist input-only
# und hat keinen internen Pulldown — ohne angeschlossenen Akku (reiner
# USB-Betrieb) floatet der Pin und liefert Zufallswerte.
PLAUSIBLE_MIN_V = 3.0
PLAUSIBLE_MAX_V = 4.35


def is_plausible(v_bat):
    """True, wenn die Spannung zu einer echten Zelle passen kann."""
    return PLAUSIBLE_MIN_V <= v_bat <= PLAUSIBLE_MAX_V


def voltage_to_percent(v_bat):
    """Zellspannung -> Ladestand 0..100 % entlang der OCV-Kurve."""
    if v_bat <= _OCV_TABLE[0][0]:
        return 0
    if v_bat >= _OCV_TABLE[-1][0]:
        return 100
    for i in range(1, len(_OCV_TABLE)):
        v_hi, p_hi = _OCV_TABLE[i]
        if v_bat <= v_hi:
            v_lo, p_lo = _OCV_TABLE[i - 1]
            frac = (v_bat - v_lo) / (v_hi - v_lo)
            return int(round(p_lo + frac * (p_hi - p_lo)))
    return 100
