package provision

import (
	"encoding/base64"
	"regexp"
	"strings"
	"testing"
)

var writeArg = regexp.MustCompile(`a2b_base64\('([^']*)'\)`)

// reconstruct rebuilds the file content from the generated command, proving the
// base64 round-trips losslessly.
func reconstruct(t *testing.T, cmd string) []byte {
	t.Helper()
	m := writeArg.FindStringSubmatch(cmd)
	if m == nil {
		t.Fatalf("kein a2b_base64-Aufruf in: %q", cmd)
	}
	dec, err := base64.StdEncoding.DecodeString(m[1])
	if err != nil {
		t.Fatalf("base64 decode failed: %v", err)
	}
	return dec
}

func TestBuildWriteFileCommandRoundTrip(t *testing.T) {
	content := []byte("Zeile 1\nZeile 2 mit Umlaut ä\n{\"k\": 1}\n")
	cmd := BuildWriteFileCommand("config.json", content)
	if got := reconstruct(t, cmd); string(got) != string(content) {
		t.Fatalf("round-trip mismatch:\n got: %q\nwant: %q", got, content)
	}
}

func TestBuildWriteFileCommandHasOpenAndClose(t *testing.T) {
	cmd := BuildWriteFileCommand("main.py", []byte("x"))
	if !strings.Contains(cmd, "open(\"main.py\",'wb')") {
		t.Fatalf("Kommando muss Datei oeffnen, war: %q", cmd)
	}
	if !strings.Contains(cmd, "_f.close()") {
		t.Fatalf("Kommando muss Datei schliessen, war: %q", cmd)
	}
}

func TestBuildWriteFileCommandBinary(t *testing.T) {
	content := []byte{0x00, 0x89, 0x50, 0x4e, 0x47, 0xff, 0xfe, 0x0d, 0x0a}
	cmd := BuildWriteFileCommand("logo.png", content)
	got := reconstruct(t, cmd)
	if len(got) != len(content) {
		t.Fatalf("Binaer-Laenge: got %d want %d", len(got), len(content))
	}
	for i := range content {
		if got[i] != content[i] {
			t.Fatalf("Binaer-Byte %d: got %x want %x", i, got[i], content[i])
		}
	}
}

func TestBuildWriteFileCommandEmpty(t *testing.T) {
	cmd := BuildWriteFileCommand("empty", nil)
	if got := reconstruct(t, cmd); len(got) != 0 {
		t.Fatalf("leerer Inhalt sollte 0 bytes rekonstruieren, bekam %d", len(got))
	}
}
