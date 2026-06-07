// Command esp32-ph is a small desktop wizard that flashes a Petri-Heil water
// sensor (LILYGO T-SIM7000G): it writes MicroPython and uploads the firmware
// modules to the board. Device configuration (WiFi/LTE, API key, …) is NOT done
// here — the board hosts a WiFi hotspot + captive portal for that, so it can be
// (re)configured from a phone in the field without a cable.
//
// The UI is a guided wizard. While flashing runs, all controls are locked and an
// indeterminate progress bar plus a status line give feedback.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
	"time"

	"fyne.io/fyne/v2"
	"fyne.io/fyne/v2/app"
	"fyne.io/fyne/v2/canvas"
	"fyne.io/fyne/v2/container"
	"fyne.io/fyne/v2/dialog"
	"fyne.io/fyne/v2/theme"
	"fyne.io/fyne/v2/widget"

	"esp32ph/internal/assets"
	"esp32ph/internal/flash"
	"esp32ph/internal/provision"
	phserial "esp32ph/internal/serial"
)

// version is overridden at build time via -ldflags "-X main.version=...".
var version = "dev"

const (
	stepCount    = 3
	impressumURL = "https://petri-heil.online/clubs/impressum/"
)

var stepTitles = [stepCount]string{
	"Schritt 1 von 3 · Board verbinden",
	"Schritt 2 von 3 · Board einrichten",
	"Schritt 3 von 3 · Fertig",
}

var actionLabels = [stepCount]string{
	"Ports suchen",
	"Flashen + Firmware aufspielen",
	"",
}

func main() {
	a := app.New()
	w := a.NewWindow(fmt.Sprintf("Petri-Heil Sensor-Flasher (%s)", version))
	w.Resize(fyne.NewSize(760, 700))

	ui := newUI(w)
	w.SetContent(ui.root)
	ui.showStep(0)
	ui.refreshPorts()
	w.ShowAndRun()
}

type appUI struct {
	win  fyne.Window
	root fyne.CanvasObject

	step    int
	running bool
	setupOK bool

	titleLbl *widget.Label
	content  *fyne.Container
	steps    [stepCount]fyne.CanvasObject

	portSelect *widget.Select
	ports      []phserial.Port

	backBtn   *widget.Button
	nextBtn   *widget.Button
	actionBtn *widget.Button
	statusLbl *widget.Label
	progress  *widget.ProgressBarInfinite

	logText   string
	logLabel  *widget.Label
	logScroll *container.Scroll
}

func newUI(w fyne.Window) *appUI {
	ui := &appUI{win: w}

	ui.titleLbl = widget.NewLabelWithStyle("", fyne.TextAlignLeading, fyne.TextStyle{Bold: true})

	ui.steps[0] = ui.buildBoardStep()
	ui.steps[1] = ui.buildSetupStep()
	ui.steps[2] = ui.buildDoneStep()
	ui.content = container.NewStack(ui.steps[0])

	ui.logLabel = widget.NewLabel("")
	ui.logLabel.Wrapping = fyne.TextWrapWord
	ui.logScroll = container.NewVScroll(ui.logLabel)
	ui.logScroll.SetMinSize(fyne.NewSize(0, 180))
	logToolbar := container.NewHBox(
		widget.NewButtonWithIcon("Kopieren", theme.ContentCopyIcon(), ui.copyLog),
		widget.NewButtonWithIcon("Speichern…", theme.DocumentSaveIcon(), ui.exportLog),
	)
	logBody := container.NewBorder(logToolbar, nil, nil, nil, ui.logScroll)
	logCard := widget.NewCard("Protokoll", "", logBody)

	ui.progress = widget.NewProgressBarInfinite()
	ui.progress.Stop()
	ui.progress.Hide()
	ui.statusLbl = widget.NewLabel("Bereit.")

	ui.backBtn = widget.NewButton("◀ Zurück", ui.goBack)
	ui.nextBtn = widget.NewButton("Weiter ▶", ui.goNext)
	ui.actionBtn = widget.NewButton("", ui.onAction)
	ui.actionBtn.Importance = widget.HighImportance

	statusBox := container.NewVBox(ui.statusLbl, ui.progress)
	footer := container.NewBorder(
		widget.NewSeparator(), nil,
		ui.backBtn,
		container.NewHBox(ui.actionBtn, ui.nextBtn),
		statusBox,
	)

	logoImg := canvas.NewImageFromResource(fyne.NewStaticResource("petri_heil_logo.png", assets.Logo))
	logoImg.FillMode = canvas.ImageFillContain
	logoImg.SetMinSize(fyne.NewSize(56, 56))
	appName := widget.NewLabelWithStyle("Petri-Heil Sensor-Flasher", fyne.TextAlignLeading, fyne.TextStyle{Bold: true})
	header := container.NewVBox(
		container.NewBorder(nil, nil, container.NewPadded(logoImg), nil,
			container.NewVBox(appName, ui.titleLbl)),
		widget.NewSeparator(),
	)

	impURL, _ := url.Parse(impressumURL)
	creditTop := container.NewBorder(nil, nil,
		widget.NewLabel("Version "+version),
		widget.NewHyperlink("Petri-Heil · Impressum", impURL),
		nil,
	)
	copyright := widget.NewLabelWithStyle(copyrightLine(), fyne.TextAlignCenter, fyne.TextStyle{})
	credit := container.NewVBox(creditTop, copyright)

	body := container.NewBorder(nil, logCard, nil, nil, ui.content)
	bottom := container.NewVBox(footer, widget.NewSeparator(), credit)
	ui.root = container.NewBorder(header, bottom, nil, nil, body)

	ui.logf("Willkommen. Board per USB anstecken, Port wählen, dann »Weiter«.")
	return ui
}

// --- step 1: board / port -------------------------------------------------

func (ui *appUI) buildBoardStep() fyne.CanvasObject {
	help := widget.NewLabel(
		"1. Sensor-Board per USB anstecken.\n" +
			"2. Unten den Port mit [WCH/CH9102] o.ä. wählen.\n" +
			"3. Leuchtet am Board nichts? USB-C-Stecker umdrehen.\n" +
			"Dann unten auf »Weiter«.")
	ui.portSelect = widget.NewSelect(nil, func(string) { ui.updateControls() })
	updateBtn := widget.NewButtonWithIcon("Firmware aktualisieren", theme.ViewRefreshIcon(), ui.doUpdate)
	diagBtn := widget.NewButtonWithIcon("Diagnose / Vitalwerte", theme.InfoIcon(), ui.doDiagnose)
	return container.NewVBox(
		help,
		widget.NewForm(widget.NewFormItem("Serieller Port", ui.portSelect)),
		container.NewHBox(updateBtn, diagBtn),
	)
}

// doUpdate re-uploads only the firmware modules over the raw REPL, WITHOUT
// erasing flash — so config.json and log.txt survive. It runs only if the board
// already has a working MicroPython (raw REPL reachable); otherwise it points
// the user to the full flash.
func (ui *appUI) doUpdate() {
	port, ok := ui.selectedPort()
	if !ok {
		ui.warn("Bitte zuerst einen Port wählen.")
		return
	}
	firmware, err := assets.FirmwareFiles()
	if err != nil {
		ui.warn("Firmware-Dateien nicht lesbar: " + err.Error())
		return
	}
	ui.setRunning(true, "Firmware-Update …")
	go func() {
		ui.logf("Update: prüfe auf vorhandenes MicroPython …")
		p, err := provision.Open(port)
		if err != nil {
			ui.logf("Kein MicroPython erreichbar: %v", err)
			ui.warn("Kein MicroPython auf dem Board erkannt.\n" +
				"Bitte zuerst »Erst-Flash« nutzen (löscht dabei die Konfiguration).")
			ui.setRunning(false, "Update abgebrochen ✗")
			return
		}
		defer func() { _ = p.SoftReset(); _ = p.Close() }()

		impl, _ := p.Eval("import sys; print(sys.implementation.name)")
		if !strings.Contains(impl, "micropython") {
			ui.logf("Unerwartete Laufzeit: %q", impl)
			ui.warn("Auf dem Board läuft kein MicroPython — bitte »Erst-Flash«.")
			ui.setRunning(false, "Update abgebrochen ✗")
			return
		}
		ui.logf("MicroPython erkannt — Konfiguration bleibt erhalten.")
		for _, file := range firmware {
			ui.logf("Aktualisiere %s (%d Bytes) …", file.Name, len(file.Data))
			if err := p.WriteFile(file.Name, file.Data); err != nil {
				ui.logf("FEHLER: %v", err)
				ui.setRunning(false, "Update fehlgeschlagen ✗")
				return
			}
		}
		ui.logf("Neustart auslösen …")
		ui.logf("FERTIG: Firmware aktualisiert (config.json + log.txt erhalten).")
		ui.setRunning(false, "Update fertig ✓")
	}()
}

// doDiagnose connects to the selected board, reads its vitals + persisted log
// over the serial raw REPL, and shows them. The board is reset by Open (RTC RAM
// counters reset to 0), but log.txt on flash survives; SoftReset resumes normal
// operation afterwards.
func (ui *appUI) doDiagnose() {
	port, ok := ui.selectedPort()
	if !ok {
		ui.warn("Bitte zuerst einen Port wählen.")
		return
	}
	ui.setRunning(true, "Diagnose läuft …")
	go func() {
		defer ui.setRunning(false, "Bereit.")
		ui.logf("Diagnose: verbinde mit %s …", port)
		p, err := provision.Open(port)
		if err != nil {
			ui.logf("FEHLER: %v", err)
			return
		}
		defer func() { _ = p.SoftReset(); _ = p.Close() }()

		vitals, verr := p.ReadVitals()
		if verr != nil {
			ui.logf("Vitalwerte: %v", verr)
		}
		logTxt, lerr := p.ReadLog()
		if lerr != nil {
			ui.logf("Log: %v", lerr)
		}
		ui.logf("Diagnose fertig.")
		ui.showReport("Gerätediagnose", formatDiagnose(vitals, logTxt))
	}()
}

// formatDiagnose turns the vitals JSON + raw log into a readable report.
func formatDiagnose(vitalsJSON, logTxt string) string {
	var b strings.Builder
	var m map[string]any
	if vitalsJSON != "" && json.Unmarshal([]byte(vitalsJSON), &m) == nil {
		b.WriteString("== Vitalwerte ==\n")
		rows := []struct{ key, label string }{
			{"fw", "Firmware"},
			{"time_utc", "Zeit (UTC)"},
			{"battery", "Batterie %"},
			{"uptime_s", "Uptime (s)"},
			{"ram_free", "RAM frei (B)"},
			{"chip_c", "Chip °C"},
			{"buffered", "Gepuffert"},
			{"send", "Letzter Versand"},
		}
		for _, r := range rows {
			if v, ok := m[r.key]; ok && v != nil {
				b.WriteString(fmt.Sprintf("%-18s %v\n", r.label+":", v))
			}
		}
	} else if vitalsJSON != "" {
		b.WriteString(vitalsJSON + "\n")
	}
	b.WriteString("\n== Log (log.txt) ==\n")
	if strings.TrimSpace(logTxt) == "" {
		b.WriteString("(leer)\n")
	} else {
		b.WriteString(logTxt)
	}
	return b.String()
}

// showReport displays a scrollable, monospace report dialog.
func (ui *appUI) showReport(title, text string) {
	fyne.Do(func() {
		lbl := widget.NewLabel(text)
		lbl.TextStyle = fyne.TextStyle{Monospace: true}
		sc := container.NewVScroll(lbl)
		sc.SetMinSize(fyne.NewSize(460, 360))
		dialog.ShowCustom(title, "Schließen", sc, ui.win)
	})
}

func (ui *appUI) refreshPorts() {
	ports, err := phserial.List()
	if err != nil {
		ui.logf("Ports lesen fehlgeschlagen: %v", err)
		return
	}
	ui.ports = ports
	labels := make([]string, len(ports))
	for i, p := range ports {
		labels[i] = p.Label
	}
	fyne.Do(func() {
		ui.portSelect.Options = labels
		if len(labels) > 0 {
			ui.portSelect.SetSelectedIndex(0)
		}
		ui.portSelect.Refresh()
		ui.updateControls()
	})
	ui.logf("%d Port(s) gefunden.", len(ports))
}

func (ui *appUI) selectedPort() (string, bool) {
	idx := ui.portSelect.SelectedIndex()
	if idx < 0 || idx >= len(ui.ports) {
		return "", false
	}
	return ui.ports[idx].Name, true
}

// --- step 2: flash + upload firmware --------------------------------------

func (ui *appUI) buildSetupStep() fyne.CanvasObject {
	return container.NewVBox(widget.NewLabel(
		"Schreibt MicroPython " + assets.MicroPythonVersion + " auf das Board und\n" +
			"spielt die Sensor-Firmware auf. Löscht den Board-Speicher komplett.\n" +
			"Dauert ~1-2 Minuten. Während des Vorgangs nicht abstecken.\n\n" +
			"Klick »" + actionLabels[1] + "«. Danach wird »Weiter« freigeschaltet.\n\n" +
			"Die Einstellungen (WLAN/LTE, API-Key …) macht man NICHT hier, sondern\n" +
			"danach bequem per Handy über den Board-Hotspot — siehe Schritt 3."))
}

func (ui *appUI) doSetup() {
	port, ok := ui.selectedPort()
	if !ok {
		ui.warn("Bitte zuerst in Schritt 1 einen Port wählen.")
		return
	}
	firmware, err := assets.FirmwareFiles()
	if err != nil {
		ui.warn("Firmware-Dateien nicht lesbar: " + err.Error())
		return
	}

	ui.setRunning(true, "Flashe… (nicht abstecken)")
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
		defer cancel()

		ui.logLine(copyrightLine())
		ui.logf("Petri-Heil Sensor-Flasher %s", version)

		// 1. MicroPython flashen.
		ui.logf("esptool vorbereiten …")
		f, err := flash.New(port, ui.logLine)
		if err == nil {
			ui.logf("Chip erkennen …")
			var chip string
			if chip, err = f.DetectChip(ctx, ui.logLine); err == nil {
				ui.logf("Chip: %s", chip)
				ui.logf("Flash löschen …")
				err = f.EraseFlash(ctx, ui.logLine)
			}
			if err == nil {
				ui.logf("MicroPython schreiben …")
				err = f.WriteFirmware(ctx, assets.MicroPython, assets.FlashOffset, ui.logLine)
			}
		}
		if err != nil {
			ui.logf("FEHLER: %v", err)
			ui.setRunning(false, "Flashen fehlgeschlagen ✗")
			return
		}

		// 2. Firmware-Module aufs Board-Dateisystem laden (Raw-REPL).
		ui.setRunning(true, "Lade Firmware aufs Board…")
		ui.logf("Warte auf MicroPython-REPL …")
		time.Sleep(2 * time.Second) // Board nach Flash-Reset hochfahren lassen

		p, err := provision.Open(port)
		if err != nil {
			ui.logf("FEHLER: %v", err)
			ui.setRunning(false, "Firmware-Upload fehlgeschlagen ✗")
			return
		}
		defer p.Close()
		for _, file := range firmware {
			ui.logf("Schreibe %s (%d Bytes) …", file.Name, len(file.Data))
			if err := p.WriteFile(file.Name, file.Data); err != nil {
				ui.logf("FEHLER: %v", err)
				ui.setRunning(false, "Firmware-Upload fehlgeschlagen ✗")
				return
			}
		}
		ui.logf("Neustart auslösen …")
		if err := p.SoftReset(); err != nil {
			ui.logf("Warnung: Soft-Reset: %v", err)
		}

		ui.logf("FERTIG: Board geflasht + Firmware aufgespielt.")
		fyne.Do(func() { ui.setupOK = true })
		ui.setRunning(false, "Fertig ✓ — »Weiter«")
	}()
}

// --- step 3: done / hotspot hint ------------------------------------------

func (ui *appUI) buildDoneStep() fyne.CanvasObject {
	return container.NewVBox(
		widget.NewLabelWithStyle("Board ist startklar.", fyne.TextAlignLeading, fyne.TextStyle{Bold: true}),
		widget.NewLabel(
			"Konfiguriert wird jetzt bequem per Handy — ganz ohne Kabel:\n\n"+
				"1. Das Board fährt einen WLAN-Hotspot »PH-XXXXXX« hoch\n"+
				"   (gerätespezifisch aus der MAC, z. B. PH-5BFF6C),\n"+
				"   (direkt nach dem Einrichten, ~60 Sekunden lang).\n"+
				"2. Mit dem Handy verbinden (Passwort: petriheil).\n"+
				"3. Die Konfig-Seite öffnet sich automatisch (Captive-Portal).\n"+
				"4. WLAN/LTE, Ingest-URL, Device-ID, API-Key usw. eintragen → Speichern.\n\n"+
				"Später ändern? Reset-Taste am Gehäuse drücken → der Hotspot kommt für\n"+
				"~60 Sekunden wieder. Beim normalen Messbetrieb bleibt er aus (Strom)."),
		widget.NewLabel("Du kannst das Board jetzt abstecken."),
	)
}

// --- navigation & control state -------------------------------------------

func (ui *appUI) showStep(i int) {
	if i < 0 || i >= stepCount {
		return
	}
	ui.step = i
	ui.titleLbl.SetText(stepTitles[i])
	ui.actionBtn.SetText(actionLabels[i])
	if actionLabels[i] == "" {
		ui.actionBtn.Hide()
	} else {
		ui.actionBtn.Show()
	}
	ui.content.Objects = []fyne.CanvasObject{ui.steps[i]}
	ui.content.Refresh()
	ui.updateControls()
}

func (ui *appUI) goBack() {
	if !ui.running && ui.step > 0 {
		ui.showStep(ui.step - 1)
	}
}

func (ui *appUI) goNext() {
	if !ui.running && ui.canAdvance() && ui.step < stepCount-1 {
		ui.showStep(ui.step + 1)
	}
}

func (ui *appUI) onAction() {
	switch ui.step {
	case 0:
		go ui.refreshPorts()
	case 1:
		ui.doSetup()
	}
}

// canAdvance reports whether the current step's prerequisite is met.
func (ui *appUI) canAdvance() bool {
	switch ui.step {
	case 0:
		_, ok := ui.selectedPort()
		return ok
	case 1:
		return ui.setupOK
	}
	return false
}

func (ui *appUI) updateControls() {
	if ui.running || ui.step == 0 {
		ui.backBtn.Disable()
	} else {
		ui.backBtn.Enable()
	}

	if ui.step >= stepCount-1 {
		ui.nextBtn.Hide()
	} else {
		ui.nextBtn.Show()
		if ui.running || !ui.canAdvance() {
			ui.nextBtn.Disable()
		} else {
			ui.nextBtn.Enable()
		}
	}

	if ui.running {
		ui.actionBtn.Disable()
	} else {
		ui.actionBtn.Enable()
	}
}

// setRunning locks/unlocks the UI and toggles the progress feedback. Safe from
// any goroutine.
func (ui *appUI) setRunning(on bool, status string) {
	fyne.Do(func() {
		ui.running = on
		ui.statusLbl.SetText(status)
		if on {
			ui.progress.Show()
			ui.progress.Start()
		} else {
			ui.progress.Stop()
			ui.progress.Hide()
		}
		ui.updateControls()
	})
}

// --- helpers --------------------------------------------------------------

func (ui *appUI) logf(format string, args ...any) {
	ui.logLine(fmt.Sprintf(format, args...))
}

func (ui *appUI) logLine(line string) {
	fyne.Do(func() {
		ui.logText += line + "\n"
		ui.logLabel.SetText(ui.logText)
		ui.logScroll.ScrollToBottom()
	})
}

func (ui *appUI) warn(msg string) {
	fyne.Do(func() { dialog.ShowInformation("Hinweis", msg, ui.win) })
}

// copyrightLine builds the credit shown in the footer and logged on flashing.
func copyrightLine() string {
	return fmt.Sprintf("© %d Daniel Altiparmak · Zilicon IT Services · Petri-Heil", time.Now().Year())
}

// copyLog puts the whole protocol into the clipboard.
func (ui *appUI) copyLog() {
	ui.win.Clipboard().SetContent(ui.logText)
	ui.statusLbl.SetText("Protokoll in Zwischenablage kopiert")
}

// exportLog saves the protocol to a text file the user picks.
func (ui *appUI) exportLog() {
	d := dialog.NewFileSave(func(w fyne.URIWriteCloser, err error) {
		if err != nil || w == nil {
			return
		}
		defer w.Close()
		if _, werr := w.Write([]byte(ui.logText)); werr != nil {
			ui.warn("Export fehlgeschlagen: " + werr.Error())
			return
		}
		ui.statusLbl.SetText("Protokoll gespeichert")
	}, ui.win)
	d.SetFileName("flash-protokoll.txt")
	d.Show()
}
