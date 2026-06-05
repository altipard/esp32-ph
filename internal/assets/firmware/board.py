# Board-Pinbelegung — LILYGO T-SIM7000G (ESP32-WROVER-B + SIM7000G + GNSS).
#
# Quelle: LILYGO/TinyGSM T-SIM7000G Referenz. WICHTIG: GPIO4 = Modem-PWRKEY,
# darf NICHT fuer Sensoren benutzt werden (haeufiger Anfaengerfehler).
#
# STATUS: UNGETESTET auf Hardware. Pins gegen das eigene Board verifizieren.

# --- Mobilfunk-Modem (SIM7000G) ---
MODEM_UART = 1
MODEM_TX = 27          # ESP32 -> Modem RX
MODEM_RX = 26          # ESP32 <- Modem TX
MODEM_PWRKEY = 4       # LOW-Puls schaltet Modem an/aus
MODEM_DTR = 25
MODEM_BAUD = 115200

# --- Status-LED ---
LED = 12

# --- Batterie-/Solar-Messung ---
BAT_ADC = 35           # Batteriespannung ueber Spannungsteiler (Faktor 2)

# --- BOOT-Taste (nur per USB erreichbar, im Gehaeuse nutzlos) ---
BOOT_BUTTON = 0

# --- Fuer Sensoren freie, unkritische GPIOs (NICHT vom Modem/SD belegt) ---
# Default-OneWire-Pin fuer DS18B20. 4 waere falsch (PWRKEY)!
DEFAULT_ONEWIRE_PIN = 32
