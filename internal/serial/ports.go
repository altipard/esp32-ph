// Package serial lists candidate USB serial ports for ESP32 boards.
package serial

import (
	"strings"

	gserial "go.bug.st/serial"
	"go.bug.st/serial/enumerator"
)

// Port describes a serial device the user might pick.
type Port struct {
	Name    string // e.g. /dev/cu.usbserial-58CE0167151 or COM5
	Label   string // human-friendly description
	IsLikely bool  // true if it looks like a USB-UART bridge (CH9102/CP210x/...)
}

// likelyVID maps known USB-UART bridge vendor IDs to a label.
var likelyVID = map[string]string{
	"1A86": "WCH (CH340/CH9102)",
	"10C4": "Silicon Labs (CP210x)",
	"0403": "FTDI",
	"303A": "Espressif (USB-nativ)",
}

// List returns available ports, USB-UART bridges first and flagged.
func List() ([]Port, error) {
	details, err := enumerator.GetDetailedPortsList()
	if err != nil {
		return fallbackList()
	}

	var out []Port
	for _, d := range details {
		p := Port{Name: d.Name, Label: d.Name}
		if d.IsUSB {
			vid := strings.ToUpper(d.VID)
			if label, ok := likelyVID[vid]; ok {
				p.IsLikely = true
				p.Label = d.Name + "  [" + label + "]"
			} else if d.Product != "" {
				p.Label = d.Name + "  [" + d.Product + "]"
			}
		}
		out = append(out, p)
	}
	sortLikelyFirst(out)
	return out, nil
}

// fallbackList is used when detailed enumeration is unavailable.
func fallbackList() ([]Port, error) {
	names, err := gserial.GetPortsList()
	if err != nil {
		return nil, err
	}
	out := make([]Port, 0, len(names))
	for _, n := range names {
		out = append(out, Port{Name: n, Label: n, IsLikely: strings.Contains(n, "usbserial") || strings.Contains(n, "usbmodem")})
	}
	sortLikelyFirst(out)
	return out, nil
}

func sortLikelyFirst(ports []Port) {
	// stable partition: likely ports keep order but move to front
	likely := make([]Port, 0, len(ports))
	rest := make([]Port, 0, len(ports))
	for _, p := range ports {
		if p.IsLikely {
			likely = append(likely, p)
		} else {
			rest = append(rest, p)
		}
	}
	copy(ports, append(likely, rest...))
}
