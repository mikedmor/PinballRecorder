# Pinball Screen Recorder

A lightweight, portable screen recorder designed for **virtual pinball cabinets** — record your Playfield, Backglass, and DMD displays simultaneously to separate video files, with optional system audio capture.

Built with Python + tkinter. Distributed as a single `.exe` with no installation required.

---

## Features

- **Multi-screen simultaneous recording** — Playfield, Backglass, and FullDMD each saved to their own MP4
- **Per-screen configuration** — individual FPS, start delay, and duration per screen
- **System audio capture** — records system audio via WASAPI loopback, with per-audio delay, duration, and "match screen" options
- **Visual preview overlays** — drag and resize live overlays to set capture regions precisely
- **Monitor auto-detection** — automatically maps screens to connected monitors on first run
- **Named config profiles** — File → New / Open / Save / Save As to manage multiple config files (e.g. per-table or per-emulator)
- **PinUP Popper Integration** — select a table from PUPDatabase, preview destination paths, and automatically move recordings into the correct POPMedia folder structure after recording
- **CLI / headless mode** — launch with a JSON config and `--autostart` to record and exit automatically
- **Portable** — runs from a USB drive, no install required
- **Config persistence** — session settings auto-saved to `default_config.json` next to the `.exe`; global preferences stored in `global.json`

---

## Quick Start

1. Download `PinballRecorder.exe` from the [Releases](../../releases) page
2. Place it anywhere (including a USB drive)
3. Run it — it will auto-detect FFmpeg if installed, or guide you through setup
4. Configure your screen regions using the **Preview** overlays
5. Click **▶ START RECORDING**

On first run with no saved config, monitors are auto-detected and assigned to screens automatically. The PinUP database is also auto-detected from common install locations.

---

## Requirements

- **Windows 10 / 11**
- **FFmpeg** — auto-detected from PATH or common install locations. If not found, the built-in setup assistant can install or download it for you.
- **Python 3.11+** (only needed if running from source)

---

## Screen Configuration

| Column | Description |
|--------|-------------|
| On | Enable/disable this screen |
| Monitor | Which monitor this screen maps to |
| X / Y | Top-left coordinate of the capture region |
| Width / Height | Size of the capture region |
| FPS | Frame rate (default: 30; set Playfield to 60 for smoother video) |
| Delay(s) | Seconds to wait before starting this stream |
| Duration(s) | Recording length in seconds (0 = record until Stop is clicked) |

Use the **🖥 Preview** button to open a live draggable/resizable overlay to set the region visually.

---

## Audio Capture

Audio is recorded to a separate MP3 file using **WASAPI loopback** — this captures whatever your speakers are playing, from any application.

- **Capture Device** — select a `[Loopback]` device from the dropdown (e.g. `[Loopback] Speakers (USB Device)`)
- **Delay (s)** — seconds to wait before starting audio capture
- **Duration (s)** — how long to record audio (0 = auto-match the longest enabled screen)
- **Match screen** — mirror a specific screen's delay and duration (defaults to **Playfield**); disables the manual delay/duration fields while active

---

## FFmpeg Setup

PinballRecorder uses FFmpeg for video encoding. The built-in **⚙ Setup** button provides:

- **Auto-install via winget** — installs FFmpeg to system PATH
- **Download portable ffmpeg.exe** — saves next to the `.exe`, ideal for USB/portable use
- **Browse** — point to an existing `ffmpeg.exe`
- **Auto-detect** — searches common install paths and PATH

The FFmpeg version and available hardware encoders (NVENC, QSV, AMF) are shown below the FFmpeg path field.

---

## Config File Management

Use the **File** menu to manage named config profiles:

| Command | Shortcut | Description |
|---------|----------|-------------|
| New | Ctrl+N | Reset all settings to defaults |
| Open… | Ctrl+O | Load a saved JSON config file |
| Open Recent | — | Quick-access to the last 8 opened files |
| Save | Ctrl+S | Save to the currently open file |
| Save As… | Ctrl+Shift+S | Save to a new file (suggests ROM name when a PinUP table is selected) |
| Preferences… | — | Edit global preferences (see below) |

The **File menu header** always shows which config file is currently loaded. The title bar also shows the filename when a named (non-default) config is open.

Session settings are auto-saved to `default_config.json` on close, in addition to any named config that is open.

### Global Preferences (`global.json`)

The following settings are **global** — shared across all config profiles and stored in `global.json` next to the exe. Edit them via **File → Preferences…**:

| Setting | Description |
|---------|-------------|
| PinUP Database path | Path to `PUPDatabase.db` (auto-detected on startup) |
| Open output folder after recording | Opens the output folder (or PinUP media dir) in Explorer when recording finishes |
| Save session log | Write a session log to `PinballRecorder.log` (always on in headless mode) |
| Recent files | List of recently opened config files (clearable) |

---

## PinUP Popper Integration

PinballRecorder has a built-in **PinUP Popper Integration** section:

1. **PinUP DB** — auto-detected from common install locations on startup; or browse/click 🔍 to find it
2. **Table / ROM** — select a table from the dropdown (loaded from `PUPDatabase.db`); the ROM name and emulator media path are read automatically from the DB
3. **Destination Preview** — shows the exact output paths for all four streams before you record
4. **After recording** — files are automatically moved into the correct POPMedia folder structure:
   - `{MediaDir}/PlayField/{ROM}.mp4`
   - `{MediaDir}/BackGlass/{ROM}.mp4`
   - `{MediaDir}/DMD/{ROM}.mp4`
   - `{MediaDir}/Audio/{ROM}.mp3`
5. **Conflict handling** — if a capture already exists, choose to Overwrite, Append (numbered suffix), or Skip
6. **Also keep copies** — optionally copy files to POPMedia *and* keep originals in the Output Folder
7. **Open folder after recording** — when a PinUP table is selected, opens the emulator's media directory instead of the Output Folder

See [docs/pinup-popper-integration.md](docs/pinup-popper-integration.md) for full details including CLI automation.

---

## CLI / Headless Mode

```
PinballRecorder.exe [--config PATH] [--autostart]
```

| Argument | Description |
|----------|-------------|
| `--config PATH` | Load a specific JSON config file instead of `default_config.json` |
| `--autostart` | Start recording immediately on launch and exit when done |

> When using `--autostart`, set a non-zero `duration` on each enabled screen. If all durations are 0, headless mode defaults to **20 seconds** per screen as a safety guard.

---

## Output Files

Files are saved to the **Output Folder** with the naming pattern:

```
{prefix}_{Screen}_{YYYYMMDD_HHMMSS}.mp4
{prefix}_Audio_{YYYYMMDD_HHMMSS}.mp3
```

When a PinUP table is selected, files are moved (or copied) into the POPMedia folder structure after recording.

---

## Building from Source

```powershell
git clone https://github.com/yourusername/PinballRecorder.git
cd PinballRecorder
python -m venv .venv
.venv\Scripts\activate
pip install pyaudiowpatch pyinstaller

python PinballRecorder.py       # run from source
pyinstaller PinballRecorder.spec  # build exe → dist\PinballRecorder.exe
```

---

## License

MIT License — see [LICENSE](LICENSE)

---

## Acknowledgements

- [FFmpeg](https://ffmpeg.org/) — video encoding
- [pyaudiowpatch](https://github.com/s0d3s/PyAudioWPatch) — WASAPI loopback audio capture
- [PinUP Popper](https://www.nailbuster.com/wikipinup/doku.php) — the pinball frontend this was built to complement
