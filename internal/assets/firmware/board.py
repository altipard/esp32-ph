# Board-Pinbelegung — LILYGO T-SIM7000G (ESP32-WROVER-B + SIM7000G + GNSS).
#
# Quelle: LILYGO/TinyGSM T-SIM7000G Referenz. WICHTIG: GPIO4 = Modem-PWRKEY,
# darf NICHT fuer Sensoren benutzt werden (haeufiger Anfaengerfehler).
#
# STATUS: Modem-Pins am echten Board verifiziert. Sensor-Pin (DS18B20) noch
# nicht mit angeschlossenem Sensor geprueft.

# --- Mobilfunk-Modem (SIM7000G) ---
MODEM_UART = 1
MODEM_TX = 27          # ESP32 -> Modem RX
MODEM_RX = 26          # ESP32 <- Modem TX
MODEM_PWRKEY = 4       # LOW-Puls schaltet Modem an/aus
MODEM_DTR = 25
MODEM_BAUD = 115200

# --- Status-LED ---
# ACTIVE-LOW: LED haengt zwischen 3V3 und GPIO12 — Pin LOW = LED AN.
# GPIO12 ist ausserdem Strapping-Pin (MTDI) mit Pulldown: unkonfiguriert
# leuchtet die LED PERMANENT (auch im Deep Sleep, ~1-2 mA = 24-48 mAh/Tag).
# Darum: im Betrieb explizit auf HIGH (aus) treiben, im Deep Sleep halten.
LED = 12
LED_OFF = 1
LED_ON = 0

# --- Batterie-/Solar-Messung ---
BAT_ADC = 35           # Batteriespannung ueber Spannungsteiler (Faktor 2)

# --- BOOT-Taste (nur per USB erreichbar, im Gehaeuse nutzlos) ---
BOOT_BUTTON = 0

# --- Fuer Sensoren freie, unkritische GPIOs (NICHT vom Modem/SD belegt) ---
# Default-OneWire-Pin fuer DS18B20. 4 waere falsch (PWRKEY)!
DEFAULT_ONEWIRE_PIN = 32
