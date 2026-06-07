package provision

import (
	"bytes"
	"fmt"
	"strings"
	"time"

	"go.bug.st/serial"
)

const (
	ctrlA = 0x01 // enter raw REPL
	ctrlB = 0x02 // exit raw REPL
	ctrlC = 0x03 // interrupt running program
	ctrlD = 0x04 // execute submission / EOT marker
)

// Provisioner talks to a MicroPython board over its serial raw REPL.
type Provisioner struct {
	port serial.Port
	name string
}

// Open connects to portName at the MicroPython default 115200 baud and puts the
// board into raw REPL, ready to receive file writes.
func Open(portName string) (*Provisioner, error) {
	port, err := serial.Open(portName, &serial.Mode{BaudRate: 115200})
	if err != nil {
		return nil, fmt.Errorf("serieller Port %s nicht zu oeffnen: %w", portName, err)
	}
	if err := port.SetReadTimeout(2 * time.Second); err != nil {
		_ = port.Close()
		return nil, err
	}
	p := &Provisioner{port: port, name: portName}

	// Each attempt: hard-reset the board into RUN mode, wait for MicroPython to
	// boot, then try the raw REPL handshake. Opening the port can otherwise
	// leave the ESP32 held in reset → it outputs nothing.
	var lastErr error
	for attempt := 0; attempt < 5; attempt++ {
		p.resetToRun()
		time.Sleep(1800 * time.Millisecond) // MicroPython boot
		if err := p.enterRaw(); err != nil {
			lastErr = err
			continue
		}
		return p, nil
	}
	_ = port.Close()
	return nil, lastErr
}

// resetToRun pulses the auto-reset lines to boot the firmware (mirrors esptool's
// hard reset): RTS (wired to EN) asserted→released while DTR stays deasserted,
// so IO0 stays high (run firmware, NOT download mode).
func (p *Provisioner) resetToRun() {
	_ = p.port.SetDTR(false)
	_ = p.port.SetRTS(true) // EN low → reset
	time.Sleep(120 * time.Millisecond)
	_ = p.port.SetRTS(false) // EN high → run
}

// Close leaves raw REPL and closes the serial port.
func (p *Provisioner) Close() error {
	_, _ = p.port.Write([]byte{ctrlB})
	return p.port.Close()
}

// WriteFile creates remoteName on the board with exactly content. It uses
// MicroPython's flow-controlled raw-paste transfer, so files of any size upload
// reliably (no UART buffer overflow).
func (p *Provisioner) WriteFile(remoteName string, content []byte) error {
	if err := p.execPaste(BuildWriteFileCommand(remoteName, content)); err != nil {
		return fmt.Errorf("schreiben von %s: %w", remoteName, err)
	}
	return nil
}

// SoftReset reboots the firmware so a freshly written main.py starts running.
func (p *Provisioner) SoftReset() error {
	// Leave raw REPL, then Ctrl-D in the friendly REPL triggers a soft reset.
	if _, err := p.port.Write([]byte{ctrlB}); err != nil {
		return err
	}
	time.Sleep(100 * time.Millisecond)
	_, err := p.port.Write([]byte{ctrlD})
	return err
}

// enterRaw interrupts any running program and switches to raw REPL.
func (p *Provisioner) enterRaw() error {
	// Wake the REPL and interrupt any running program. A newline nudges the
	// friendly REPL to print a fresh prompt; two Ctrl-C break a running loop.
	if _, err := p.port.Write([]byte("\r\n")); err != nil {
		return err
	}
	time.Sleep(50 * time.Millisecond)
	if _, err := p.port.Write([]byte{ctrlC, ctrlC}); err != nil {
		return err
	}
	time.Sleep(200 * time.Millisecond)
	p.drain() // discard boot banner / prompt
	if _, err := p.port.Write([]byte{ctrlA}); err != nil {
		return err
	}
	// The device answers "raw REPL; CTRL-B to exit\r\n>" in one go. Match the
	// banner, then drain the trailing prompt — do NOT do a second read for ">",
	// it arrives in the same chunk and would otherwise be lost.
	if err := p.readUntil([]byte("raw REPL"), 3*time.Second); err != nil {
		return fmt.Errorf("raw REPL nicht erreichbar (laeuft MicroPython?): %w", err)
	}
	time.Sleep(150 * time.Millisecond)
	p.drain()
	return nil
}

func (p *Provisioner) readUntil(token []byte, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	var buf bytes.Buffer
	tmp := make([]byte, 256)
	for time.Now().Before(deadline) {
		n, err := p.port.Read(tmp)
		if n > 0 {
			buf.Write(tmp[:n])
			if bytes.Contains(buf.Bytes(), token) {
				return nil
			}
		}
		if err != nil {
			return err
		}
	}
	return fmt.Errorf("timeout: %q nicht gesehen (gelesen: %q)", token, buf.String())
}

func (p *Provisioner) drain() {
	tmp := make([]byte, 256)
	_ = p.port.SetReadTimeout(200 * time.Millisecond)
	for {
		n, err := p.port.Read(tmp)
		if n == 0 || err != nil {
			break
		}
	}
	_ = p.port.SetReadTimeout(2 * time.Second)
}

// pasteAndCapture runs cmd via MicroPython's raw-paste mode: a flow-controlled
// stdin transfer (the same mechanism mpremote uses). The device grants a send
// window and acks with 0x01 as it consumes data, so large payloads cannot
// overflow the UART buffer. Returns the raw board response (stdout, 0x04,
// stderr, 0x04, ">"). Must be called while already in raw REPL.
func (p *Provisioner) pasteAndCapture(cmd string) ([]byte, error) {
	// Initiate raw-paste: Ctrl-E 'A' Ctrl-A.
	if _, err := p.port.Write([]byte{0x05, 'A', ctrlA}); err != nil {
		return nil, err
	}
	hdr, err := p.readN(2, 2*time.Second)
	if err != nil {
		return nil, fmt.Errorf("raw-paste Init: %w", err)
	}
	if !(hdr[0] == 'R' && hdr[1] == 0x01) {
		return nil, fmt.Errorf("raw-paste nicht unterstuetzt (Antwort %q)", hdr)
	}
	win, err := p.readN(2, 2*time.Second)
	if err != nil {
		return nil, fmt.Errorf("raw-paste Fenstergroesse: %w", err)
	}
	window := int(win[0]) | int(win[1])<<8
	if window <= 0 {
		window = 128
	}

	data := []byte(cmd)
	remain := window
	for i := 0; i < len(data); {
		for remain == 0 {
			b, err := p.readByte(8 * time.Second)
			if err != nil {
				return nil, fmt.Errorf("raw-paste Flusskontrolle: %w", err)
			}
			if b == 0x01 {
				remain += window
			} else if b == ctrlD {
				_, _ = p.port.Write([]byte{ctrlD})
				return nil, fmt.Errorf("raw-paste vom Board abgebrochen")
			}
		}
		n := remain
		if rem := len(data) - i; rem < n {
			n = rem
		}
		if _, err := p.port.Write(data[i : i+n]); err != nil {
			return nil, err
		}
		i += n
		remain -= n
	}

	// End of data. The device acks, executes, and returns to the raw-REPL ">"
	// prompt, with stdout/stderr (separated by 0x04 markers) in between.
	if _, err := p.port.Write([]byte{ctrlD}); err != nil {
		return nil, err
	}
	resp, err := p.readUntilCapture([]byte(">"), 15*time.Second)
	if err != nil {
		return resp, fmt.Errorf("keine Board-Antwort: %w", err)
	}
	return resp, nil
}

// execPaste runs cmd and only checks for a Python traceback (no output needed).
func (p *Provisioner) execPaste(cmd string) error {
	resp, err := p.pasteAndCapture(cmd)
	if err != nil {
		return err
	}
	if idx := bytes.Index(resp, []byte("Traceback")); idx >= 0 {
		msg := strings.TrimRight(strings.TrimSpace(string(resp[idx:])), ">")
		return fmt.Errorf("board-fehler: %s", strings.TrimSpace(msg))
	}
	return nil
}

// Eval runs cmd and returns its stdout. A Python exception becomes an error.
// Must be called while in raw REPL.
func (p *Provisioner) Eval(cmd string) (string, error) {
	resp, err := p.pasteAndCapture(cmd)
	if err != nil {
		return "", err
	}
	// Raw-paste ends with: [0x04 end-ack] <stdout> 0x04 <stderr> ">".
	// Drop the trailing prompt, split on 0x04, and skip an empty leading
	// segment (the end-ack) so stdout/stderr land correctly.
	resp = bytes.TrimRight(resp, "\r\n >")
	segs := bytes.Split(resp, []byte{ctrlD})
	if len(segs) > 1 && strings.Trim(string(segs[0]), "\x00\x01\r\n ") == "" {
		segs = segs[1:]
	}
	stdout := strings.Trim(string(segs[0]), "\x00\x01\r\n")
	if len(segs) > 1 {
		if stderr := strings.TrimSpace(string(segs[1])); stderr != "" {
			return stdout, fmt.Errorf("board-fehler: %s", stderr)
		}
	}
	return stdout, nil
}

// ReadVitals returns the device vitals as a JSON string (from main.vitals()).
func (p *Provisioner) ReadVitals() (string, error) {
	return p.Eval("import json, main; print(json.dumps(main.vitals()))")
}

// ReadLog returns the persisted log buffer (log.txt), or "" if none exists.
func (p *Provisioner) ReadLog() (string, error) {
	return p.Eval("try:\n  _d = open('log.txt').read()\nexcept OSError:\n  _d = ''\nprint(_d)")
}

// readN reads exactly n bytes (or errors on timeout).
func (p *Provisioner) readN(n int, timeout time.Duration) ([]byte, error) {
	deadline := time.Now().Add(timeout)
	out := make([]byte, 0, n)
	tmp := make([]byte, 1)
	for len(out) < n && time.Now().Before(deadline) {
		m, err := p.port.Read(tmp)
		if m > 0 {
			out = append(out, tmp[0])
		}
		if err != nil {
			break
		}
	}
	if len(out) < n {
		return out, fmt.Errorf("nur %d/%d bytes gelesen", len(out), n)
	}
	return out, nil
}

// readByte reads a single byte.
func (p *Provisioner) readByte(timeout time.Duration) (byte, error) {
	deadline := time.Now().Add(timeout)
	tmp := make([]byte, 1)
	for time.Now().Before(deadline) {
		n, err := p.port.Read(tmp)
		if n > 0 {
			return tmp[0], nil
		}
		if err != nil {
			return 0, err
		}
	}
	return 0, fmt.Errorf("timeout beim Byte-Lesen")
}

// readUntilCapture reads until token appears, returning everything read.
func (p *Provisioner) readUntilCapture(token []byte, timeout time.Duration) ([]byte, error) {
	deadline := time.Now().Add(timeout)
	var buf bytes.Buffer
	tmp := make([]byte, 256)
	for time.Now().Before(deadline) {
		n, err := p.port.Read(tmp)
		if n > 0 {
			buf.Write(tmp[:n])
			if bytes.Contains(buf.Bytes(), token) {
				return buf.Bytes(), nil
			}
		}
		if err != nil {
			break
		}
	}
	return buf.Bytes(), fmt.Errorf("timeout: %q nicht gesehen", token)
}
