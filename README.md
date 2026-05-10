# 🎯 Pinball Screen Recorder

A lightweight, portable screen recorder designed for **virtual pinball cabinets** — record your Playfield, Backglass, and DMD displays simultaneously to separate video files, with optional system audio capture.

Built with Python + tkinter. Distributed as a single `.exe` with no installation required.

---

## ✨ Features

- **Multi-screen simultaneous recording** — Playfield, Backglass, and FullDMD each saved to their own MP4
- **Per-screen configuration** — individual FPS, start delay, and duration per screen
- **System audio capture** — records whatever is playing through your speakers via WASAPI loopback
- **Visual preview overlays** — drag and resize live overlays to set capture regions precisely
- **Monitor auto-detection** — automatically maps screens to connected monitors
- **CLI / headless mode** — launch with a JSON config and `--autostart` to record and exit automatically (ideal for PinUP Popper integration)
- **Portable** — runs from a USB drive, no install required
- **Config persistence** — all settings auto-saved to `recorder_config.json` next to the `.exe`

---

## 🚀 Quick Start

1. Download `PinballRecorder.exe` from the [Releases](../../releases) page
2. Place it anywhere (including a USB drive)
3. Run it — it will auto-detect FFmpeg if installed, or guide you through setup
4. Configure your screen regions using the **Preview** overlays
5. Click **▶ START RECORDING**

---

## 📋 Requirements

- **Windows 10 / 11**
- **FFmpeg** — auto-detected from PATH or common install locations. If not found, the built-in setup assistant can install or download it for you.
- **Python 3.11+** (only needed if running from source)

---

## 🖥️ Screen Configuration

| Column | Description |
|--------|-------------|
| On | Enable/disable this screen |
| Monitor | Which monitor this screen maps to |
| X / Y | Top-left coordinate of the capture region |
| Width / Height | Size of the capture region |
| FPS | Frame rate (default: 30, set Playfield to 60 for smoother video) |
| Delay(s) | Seconds to wait before starting this stream (per-screen) |
| Duration(s) | Recording length in seconds (0 = record until Stop is clicked) |

Use the **🖥 Preview** button to open a live draggable/resizable overlay to set the region visually.

---

## 🔊 Audio Capture

Audio is recorded to a separate MP3 file using **WASAPI loopback** — this captures whatever your speakers are playing, from any application.

Select your output device (e.g. `[Loopback] Speakers (USB Device)`) from the **Capture Device** dropdown and click ↻ to refresh the device list.

---

## ⚙️ FFmpeg Setup

PinballRecorder uses FFmpeg for video encoding. The built-in **⚙ Setup** button (next to the FFmpeg Path field) provides:

- **Auto-install via winget** — installs to system PATH
- **Download portable ffmpeg.exe** — saves next to the `.exe`, ideal for USB/portable use
- **Browse** — point to an existing `ffmpeg.exe`
- **Auto-detect** — searches common install paths and PATH

---

## 🤖 CLI / Headless Mode (PinUP Popper Integration)

PinballRecorder supports command-line arguments for automated recording:

```
PinballRecorder.exe [--config PATH] [--autostart]
```

| Argument | Description |
|----------|-------------|
| `--config PATH` | Load a specific JSON config file instead of the default saved config |
| `--autostart` | Start recording immediately on launch and exit when done |

### Example — PinUP Popper Integration

1. Create a config file for your table (e.g. `twilight_zone.json`) — see [Configuration File Format](docs/config-format.md)
2. Set per-screen `duration` values (required for `--autostart` to know when to stop)
3. Call from PinUP Popper's media capture script:

```bat
PinballRecorder.exe --config "C:\Configs\twilight_zone.json" --autostart
```

The recorder will open its window, start recording, and **automatically close** when all durations have elapsed — no user interaction required.

> **Note:** When using `--autostart`, set a non-zero `duration` on each enabled screen so the recorder knows when to stop. If all durations are 0, headless mode will automatically default to **20 seconds** per screen as a safety guard.

---

## 📁 Output Files

Files are saved to the configured **Output Folder** with the naming pattern:

```
{prefix}_{Screen}_{YYYYMMDD_HHMMSS}.mp4
{prefix}_Audio_{YYYYMMDD_HHMMSS}.mp3
```

Example:
```
pinball_Playfield_20260510_141827.mp4
pinball_Backglass_20260510_141827.mp4
pinball_FullDMD_20260510_141827.mp4
pinball_Audio_20260510_141827.mp3
```

---

## 🏗️ Building from Source

```powershell
# Clone and set up
git clone https://github.com/yourusername/PinballRecorder.git
cd PinballRecorder
python -m venv .venv
.venv\Scripts\activate
pip install pyaudiowpatch pyinstaller

# Run from source
python PinballRecorder.py

# Build exe
pyinstaller PinballRecorder.spec
# Output: dist\PinballRecorder.exe
```

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

---

## 🙏 Acknowledgements

- [FFmpeg](https://ffmpeg.org/) — video encoding
- [pyaudiowpatch](https://github.com/s0d3s/PyAudioWPatch) — WASAPI loopback audio capture
- [PinUP Popper](https://www.nailbuster.com/wikipinup/doku.php) — the pinball frontend this was built to complement
