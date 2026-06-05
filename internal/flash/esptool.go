// Package flash wraps the official esptool standalone binary to detect the chip
// and write the MicroPython image. esptool is downloaded once and cached, so the
// repository stays free of large per-OS binaries.
package flash

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// EsptoolVersion is the esptool release the flasher pins to.
const EsptoolVersion = "v5.3.0"

// assetName maps the current GOOS/GOARCH to the esptool release asset.
func assetName() (string, error) {
	switch runtime.GOOS {
	case "darwin":
		switch runtime.GOARCH {
		case "arm64":
			return "esptool-" + EsptoolVersion + "-macos-arm64.tar.gz", nil
		case "amd64":
			return "esptool-" + EsptoolVersion + "-macos-amd64.tar.gz", nil
		}
	case "linux":
		if runtime.GOARCH == "amd64" {
			return "esptool-" + EsptoolVersion + "-linux-amd64.tar.gz", nil
		}
	case "windows":
		if runtime.GOARCH == "amd64" {
			return "esptool-" + EsptoolVersion + "-windows-amd64.zip", nil
		}
	}
	return "", fmt.Errorf("kein esptool-Build fuer %s/%s", runtime.GOOS, runtime.GOARCH)
}

func cacheDir() (string, error) {
	base, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "esp32-ph", "esptool-"+EsptoolVersion), nil
}

// EnsureEsptool returns the path to a ready-to-run esptool binary, downloading
// and extracting the cached release on first use. onStatus receives progress
// messages for the GUI.
func EnsureEsptool(onStatus func(string)) (string, error) {
	dir, err := cacheDir()
	if err != nil {
		return "", err
	}
	binName := "esptool"
	if runtime.GOOS == "windows" {
		binName = "esptool.exe"
	}
	if path, ok := findBinary(dir, binName); ok {
		return path, nil
	}

	asset, err := assetName()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	url := "https://github.com/espressif/esptool/releases/download/" + EsptoolVersion + "/" + asset
	status(onStatus, "Lade esptool "+EsptoolVersion+" ...")
	archivePath := filepath.Join(dir, asset)
	if err := download(url, archivePath); err != nil {
		return "", fmt.Errorf("esptool-Download fehlgeschlagen: %w", err)
	}
	status(onStatus, "Entpacke esptool ...")
	if strings.HasSuffix(asset, ".zip") {
		err = extractZip(archivePath, dir)
	} else {
		err = extractTarGz(archivePath, dir)
	}
	if err != nil {
		return "", fmt.Errorf("esptool-Entpacken fehlgeschlagen: %w", err)
	}
	_ = os.Remove(archivePath)

	if path, ok := findBinary(dir, binName); ok {
		return path, nil
	}
	return "", fmt.Errorf("esptool-Binary nach Entpacken nicht gefunden in %s", dir)
}

// findBinary walks dir for the esptool executable (PyInstaller onedir layout).
func findBinary(dir, binName string) (string, bool) {
	var found string
	_ = filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}
		if info.Name() == binName {
			found = path
			return io.EOF // stop early
		}
		return nil
	})
	if found != "" {
		if runtime.GOOS != "windows" {
			_ = os.Chmod(found, 0o755)
		}
		return found, true
	}
	return "", false
}

func download(url, dest string) error {
	client := &http.Client{Timeout: 5 * time.Minute}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d von %s", resp.StatusCode, url)
	}
	f, err := os.Create(dest)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	return err
}

func extractTarGz(archivePath, dest string) error {
	f, err := os.Open(archivePath)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		target := filepath.Join(dest, filepath.Clean(hdr.Name))
		if !strings.HasPrefix(target, filepath.Clean(dest)+string(os.PathSeparator)) {
			return fmt.Errorf("unsicherer Pfad im Archiv: %s", hdr.Name)
		}
		switch hdr.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, os.FileMode(hdr.Mode))
			if err != nil {
				return err
			}
			if _, err := io.Copy(out, tr); err != nil {
				out.Close()
				return err
			}
			out.Close()
		}
	}
}

func extractZip(archivePath, dest string) error {
	r, err := zip.OpenReader(archivePath)
	if err != nil {
		return err
	}
	defer r.Close()
	for _, zf := range r.File {
		target := filepath.Join(dest, filepath.Clean(zf.Name))
		if !strings.HasPrefix(target, filepath.Clean(dest)+string(os.PathSeparator)) {
			return fmt.Errorf("unsicherer Pfad im Archiv: %s", zf.Name)
		}
		if zf.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		rc, err := zf.Open()
		if err != nil {
			return err
		}
		out, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, zf.Mode())
		if err != nil {
			rc.Close()
			return err
		}
		_, err = io.Copy(out, rc)
		out.Close()
		rc.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func status(onStatus func(string), msg string) {
	if onStatus != nil {
		onStatus(msg)
	}
}
