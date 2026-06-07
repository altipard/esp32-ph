// Package assets embeds the firmware artifacts shipped with the flasher:
// the MicroPython runtime image and the application .py modules.
package assets

import "embed"

// MicroPython is the ESP32_GENERIC MicroPython image flashed at offset 0x1000.
//
//go:embed micropython.bin
var MicroPython []byte

// Logo is the round Petri-Heil logo shown in the GUI header.
//
//go:embed petri_heil_logo_round.png
var Logo []byte

//go:embed firmware
var firmwareFS embed.FS

// MicroPythonVersion documents which build is embedded (keep in sync with the
// micropython.bin file and the sync script).
const MicroPythonVersion = "ESP32_GENERIC-20260406-v1.28.0"

// FlashOffset is the write-flash offset for the classic ESP32 (Xtensa).
const FlashOffset = "0x1000"

// FirmwareFile is one application module to upload to the board's filesystem.
type FirmwareFile struct {
	Name string
	Data []byte
}

// firmwareOrder lists the files to upload. Static assets (CSS/logo) for the
// captive portal go first; main.py is LAST so it only runs after all its
// dependencies (board/modem/sensors/captive + static files) are present.
var firmwareOrder = []string{
	"pure-min.css",
	"logo.png",
	"logbuf.py",
	"board.py",
	"modem.py",
	"sensors.py",
	"captive.py",
	"main.py",
}

// FirmwareFiles returns the embedded application modules in upload order.
func FirmwareFiles() ([]FirmwareFile, error) {
	out := make([]FirmwareFile, 0, len(firmwareOrder))
	for _, name := range firmwareOrder {
		data, err := firmwareFS.ReadFile("firmware/" + name)
		if err != nil {
			return nil, err
		}
		out = append(out, FirmwareFile{Name: name, Data: data})
	}
	return out, nil
}
