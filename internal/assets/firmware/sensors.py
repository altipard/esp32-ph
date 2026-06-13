# Sensor-Abstraktion — liest die in config["sensors"] konfigurierten Fuehler.
#
# Erweiterbar ueber READERS: pro Sensor-Typ eine Lesefunktion, die den Messwert
# als Ganzzahl ×10 zurueckgibt (z. B. 18.4 °C -> 184) oder None bei Fehler.
#
# Aktuell implementiert: ds18b20 (Wassertemperatur).
# Weitere Typen (ph, oxygen, …) hier ergaenzen, sobald Hardware vorhanden.
#
# STATUS: ds18b20-Pfad folgt dem Standard-MicroPython-Treiber, aber UNGETESTET
# auf diesem Board.

import time

import machine

from logbuf import log


# DS18B20-Konfigregister: TH=0, TL=0, 10-bit-Aufloesung (0.25 C, 188 ms
# Wandlung statt 750 ms bei 12 bit). Fuer Gewaessertemperatur reicht das
# locker und der ESP32 ist pro Messung ~560 ms kuerzer wach.
_DS_SCRATCH_10BIT = b"\x00\x00\x3f"
_DS_CONVERT_MS = 200  # 10-bit braucht max. 187.5 ms, mit Reserve


def read_ds18b20_x10(cfg):
    """Liest einen DS18B20 am konfigurierten Pin. cfg: {"pin": int}."""
    import onewire
    import ds18x20

    pin = cfg.get("pin", 32)
    bus = onewire.OneWire(machine.Pin(pin))
    sensor = ds18x20.DS18X20(bus)
    roms = sensor.scan()
    if not roms:
        return None
    rom = roms[0]
    # Aufloesung bei jeder Messung setzen: das Scratchpad ist fluechtig und
    # der Sensor kann zwischen den Zyklen stromlos gewesen sein.
    convert_ms = _DS_CONVERT_MS
    try:
        sensor.write_scratch(rom, _DS_SCRATCH_10BIT)
    except (OSError, AttributeError):
        # Treiber ohne write_scratch -> Sensor bleibt auf 12-bit-Default.
        # Dann volle Wandlungszeit abwarten, sonst kaeme der 85-C-Power-On-
        # Wert bzw. eine alte Messung zurueck.
        convert_ms = 750
    sensor.convert_temp()
    time.sleep_ms(convert_ms)
    celsius = sensor.read_temp(rom)
    if celsius is None:
        return None
    return int(round(celsius * 10))


# Sensor-Typ -> Lesefunktion. Neue Typen hier registrieren.
READERS = {
    "ds18b20": read_ds18b20_x10,
}


def read_all(config):
    """Liest alle konfigurierten Sensoren.

    Liefert eine Liste [(sensor_id, wert_x10), …] nur fuer erfolgreiche
    Messungen. Unbekannte Typen / Fehler werden uebersprungen.
    """
    results = []
    for entry in config.get("sensors", []):
        sensor_id = entry.get("sensor_id")
        stype = entry.get("type")
        reader = READERS.get(stype)
        if not sensor_id or reader is None:
            log("Sensor uebersprungen (id/typ):", sensor_id, stype)
            continue
        try:
            value = reader(entry)
        except Exception as exc:
            # Isolationsgrenze: EIN flackernder Fuehler darf den Mess-Zyklus
            # nie crashen. onewire.OneWireError (CRC-Fehler) erbt weder von
            # OSError noch ValueError und kommt bei wackelndem DS18B20 oft mit
            # leerer Message — ungefangen landet sie im Top-Handler (main.py),
            # der dann nur "CRASH:" loggt, 60 s schlaeft und nie sendet.
            # repr(exc) statt exc: macht den Typ sichtbar auch ohne Message.
            log("Sensor-Fehler", sensor_id, ":", repr(exc))
            value = None
        if value is None:
            log("Sensor", sensor_id, "liefert keinen Wert")
            continue
        results.append((sensor_id, value))
    return results
