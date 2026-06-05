package flash

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"
)

// Flasher runs esptool against a specific serial port.
type Flasher struct {
	EsptoolPath string
	Port        string
}

// New prepares a Flasher, ensuring esptool is available (downloads on first use).
func New(port string, onStatus func(string)) (*Flasher, error) {
	path, err := EnsureEsptool(onStatus)
	if err != nil {
		return nil, err
	}
	return &Flasher{EsptoolPath: path, Port: port}, nil
}

var chipRe = regexp.MustCompile(`(?i)Chip type:\s*([^\r\n]+)`)

// DetectChip runs `esptool chip-id` and returns the detected chip description.
func (f *Flasher) DetectChip(ctx context.Context, onLine func(string)) (string, error) {
	out, err := f.run(ctx, onLine, "chip-id")
	if err != nil {
		return "", err
	}
	if m := chipRe.FindStringSubmatch(out); m != nil {
		return strings.TrimSpace(m[1]), nil
	}
	return "", fmt.Errorf("Chip-Typ nicht aus esptool-Ausgabe lesbar")
}

// EraseFlash wipes the board.
func (f *Flasher) EraseFlash(ctx context.Context, onLine func(string)) error {
	_, err := f.run(ctx, onLine, "erase-flash")
	return err
}

// WriteFirmware writes the MicroPython image (provided as raw bytes) to the
// board at the given flash offset.
func (f *Flasher) WriteFirmware(ctx context.Context, image []byte, offset string, onLine func(string)) error {
	tmp, err := os.CreateTemp("", "micropython-*.bin")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(image); err != nil {
		tmp.Close()
		return err
	}
	tmp.Close()

	_, err = f.run(ctx, onLine,
		"--baud", "460800", "write-flash", "-z", offset, tmp.Name())
	return err
}

// run executes esptool with the shared --port flag, streaming combined output
// line-by-line to onLine, and returns the full output for parsing.
func (f *Flasher) run(ctx context.Context, onLine func(string), args ...string) (string, error) {
	full := append([]string{"--port", f.Port}, args...)
	cmd := exec.CommandContext(ctx, f.EsptoolPath, full...)

	// Single pipe for stdout+stderr so progress and errors stream in order.
	pr, pw, err := os.Pipe()
	if err != nil {
		return "", err
	}
	cmd.Stdout = pw
	cmd.Stderr = pw

	if err := cmd.Start(); err != nil {
		pw.Close()
		pr.Close()
		return "", fmt.Errorf("esptool-Start fehlgeschlagen: %w", err)
	}
	pw.Close() // parent's write end; child holds its own copy

	var sb strings.Builder
	scanner := bufio.NewScanner(pr)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		sb.WriteString(line)
		sb.WriteByte('\n')
		if onLine != nil {
			onLine(line)
		}
	}
	pr.Close()

	if err := cmd.Wait(); err != nil {
		return sb.String(), fmt.Errorf("esptool fehlgeschlagen: %w\n%s", err, sb.String())
	}
	return sb.String(), nil
}
