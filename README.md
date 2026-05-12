# Pinball Screen Recorder

A lightweight, portable screen recorder designed for **virtual pinball cabinets** — record your Playfield, Backglass, and DMD displays simultaneously to separate video files, with optional system audio capture.

Built with Python + tkinter. Distributed as a single `.exe` with no installation required.

---

## Features

- **Multi-screen simultaneous recording** — Playfield, Backglass, and FullDMD each saved to their own MP4
- **Per-screen configuration** — individual FPS, start delay, and duration per screen
- **System audio capture** — WASAPI loopback (captures all system audio) or Application mode (captures a specific app directly, bypassing system volume); with per-audio delay, duration, and "match screen" options
- **Visual preview overlays** — drag and resize live overlays to set capture regions precisely
- **Monitor auto-detection** — automatically maps screens to connected monitors on first run
- **Named config profiles** — File → New / Open / Save / Save As to manage multiple config files (e.g. per-table or per-emulator)
- **PinUP Popper Integration** — select a table from PUPDatabase, preview destination paths, and automatically move recordings into the correct POPMedia folder structure after recording
- **CLI / headless mode** — launch with `--autostart` and optional JSON config; all config values can be supplied as command-line arguments so no config file is required for automation
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
| X / Y | Top-left offset of the capture region, relative to the selected monitor's top-left corner |
| Width / Height | Size of the capture region |
| FPS | Frame rate (default: 30; set Playfield to 60 for smoother video) |
| Delay(s) | Seconds to wait before starting this stream |
| Duration(s) | Recording length in seconds (0 = record until Stop is clicked) |

Use the **🖥 Preview** button to open a live draggable/resizable overlay to set the region visually.

---

## Audio Capture

Two capture modes are available under **Capture Mode** in the Audio section:

### WASAPI Loopback (default)

Captures whatever your speakers are playing system-wide. Select a `[Loopback]` device from the **Capture Device** dropdown (e.g. `[Loopback] Speakers (USB Device)`).

### Application Audio

Captures audio directly from one or more specific application windows using the Windows Application Loopback API (Windows 10 build 19041+). This bypasses the system master volume entirely, so recordings are always at full amplitude regardless of speaker volume. Select "Application" under Capture Mode, then pick one or more windows from the list while those apps are playing audio.

- Apps that are incompatible with direct capture (Chromium-based browsers, Discord, Windows Media Player, Steam, etc.) are hidden from the list by default. **Right-click** any entry to ignore it; manage the ignore list in **File → Preferences…** under IGNORED AUDIO APPS.

### Common audio options

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
| FFmpeg path | Path to `ffmpeg.exe` (auto-detected on startup) |
| PinUP Database path | Path to `PUPDatabase.db` (auto-detected on startup) |
| Open output folder after recording | Opens the output folder (or PinUP media dir) in Explorer when recording finishes |
| Save session log | Write a session log to `PinballRecorder.log` (always on in headless mode) |
| Recent files | List of recently opened config files (clearable) |
| Ignored Audio Apps | Exe names hidden from the Application audio capture list; add via right-click, remove here |

---

## PinUP Popper Integration

PinballRecorder has a built-in **PinUP Popper Integration** section:

1. **PinUP DB** — auto-detected from common install locations on startup; or browse/click 🔍 to find it
2. **Table / ROM** — select a table from the dropdown (loaded from `PUPDatabase.db`); the ROM name and emulator media path are read automatically from the DB
3. **Destination Preview** — shows the exact output paths for all four streams before you record
4. **After recording** — files are automatically moved into the correct POPMedia folder structure:
   - `{MediaDir}/PlayField/{ROM}.mp4`
   - `{MediaDir}/BackGlass/{ROM}.mp4`
   - `{MediaDir}/Menu/{ROM}.mp4`
   - `{MediaDir}/Audio/{ROM}.mp3`
5. **Conflict handling** — if a capture already exists, choose to Overwrite, Append (numbered suffix), or Skip
6. **Also keep copies** — optionally copy files to POPMedia *and* keep originals in the Output Folder
7. **Open folder after recording** — when a PinUP table is selected, opens the emulator's media directory instead of the Output Folder

See [docs/pinup-popper-integration.md](docs/pinup-popper-integration.md) for full details including CLI automation.

---

## CLI / Headless Mode

```
PinballRecorder.exe [--config PATH] [--autostart] [options…]
```

CLI arguments override individual config values, so a JSON file is optional — you can drive everything from the command line alone.

### Core arguments

| Argument | Description |
|----------|-------------|
| `--config PATH` | Base JSON config file to load (defaults to saved config when `--autostart` is used without `--config`) |
| `--autostart` | Start recording immediately on launch and exit when done |

### Config overrides

| Argument | Description |
|----------|-------------|
| `--output-folder PATH` | Output folder for recordings |
| `--file-prefix STR` | Filename prefix |
| `--rom NAME` | PinUP ROM/game name — selects the table and auto-routes output to the correct POPMedia folder |
| `--window-title STR` | Window title to focus before recording starts |
| `--delay SECS` | Start delay applied to **all** enabled screens |
| `--duration SECS` | Duration applied to **all** enabled screens |

### Audio overrides

| Argument | Description |
|----------|-------------|
| `--audio-enabled 0\|1` | Enable or disable audio recording |
| `--audio-device NAME` | Audio capture device name |
| `--audio-delay SECS` | Audio start delay in seconds |
| `--audio-duration SECS` | Audio duration in seconds |

### Per-screen overrides

Replace `<screen>` with `playfield`, `backglass`, or `fulldmd`:

| Argument | Description |
|----------|-------------|
| `--screen-<screen>-enabled 0\|1` | Enable or disable this screen |
| `--screen-<screen>-x PX` | Left edge of capture region |
| `--screen-<screen>-y PX` | Top edge of capture region |
| `--screen-<screen>-width PX` | Capture region width |
| `--screen-<screen>-height PX` | Capture region height |
| `--screen-<screen>-fps FPS` | Frame rate |
| `--screen-<screen>-delay SECS` | Start delay for this screen only |
| `--screen-<screen>-duration SECS` | Duration for this screen only |

### Examples

```bat
:: Record all screens for 30s using the saved default config
PinballRecorder.exe --autostart --duration 30

:: Record a specific PinUP table for 30s, no config file needed
PinballRecorder.exe --autostart --rom tz_ps3 --duration 30

:: Record only the Backglass for 20s
PinballRecorder.exe --autostart --screen-playfield-enabled 0 --screen-fulldmd-enabled 0 --duration 20

:: Override individual screen duration alongside a base config
PinballRecorder.exe --config base.json --autostart --screen-playfield-duration 60 --screen-backglass-duration 30
```

> When using `--autostart`, each enabled screen must have a non-zero duration (set via `--duration`, `--screen-<name>-duration`, or the config file). If all durations are 0, headless mode defaults to **20 seconds** per screen as a safety guard.

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
