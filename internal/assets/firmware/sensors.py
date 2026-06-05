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
    sensor.convert_temp()
    time.sleep_ms(750)  # 12-bit Wandlungszeit
    celsius = sensor.read_temp(roms[0])
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
            print("Sensor uebersprungen (id/typ):", sensor_id, stype)
            continue
        try:
            value = reader(entry)
        except (OSError, ValueError) as exc:
            print("Sensor-Fehler", sensor_id, ":", exc)
            value = None
        if value is None:
            print("Sensor", sensor_id, "liefert keinen Wert")
            continue
        results.append((sensor_id, value))
    return results
