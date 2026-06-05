// Package provision writes files (firmware modules, static assets) onto a
// MicroPython board over the serial raw REPL, and triggers a soft reset.
package provision

import (
	"encoding/base64"
	"fmt"
)

// BuildWriteFileCommand returns a single MicroPython script that creates
// remoteName with exactly content. The whole file is base64-encoded into one
// a2b_base64 call; the raw-paste transfer handles arbitrary size with flow
// control, so no manual chunking is needed.
func BuildWriteFileCommand(remoteName string, content []byte) string {
	b64 := base64.StdEncoding.EncodeToString(content)
	return fmt.Sprintf(
		"import ubinascii\n_f=open(%q,'wb')\n_f.write(ubinascii.a2b_base64('%s'))\n_f.close()",
		remoteName, b64,
	)
}
