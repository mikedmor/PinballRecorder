# PinUP Popper Integration Guide

This guide explains how to use PinballRecorder's built-in PinUP integration and how to automate recording from PinUP Popper using CLI/headless mode.

---

## Overview

PinballRecorder connects directly to PinUP Popper's database (`PUPDatabase.db`) to:

- Load your full table list with ROM names and emulator media paths
- Preview the exact destination file paths before recording
- Automatically move (or copy) recorded files into the correct POPMedia folder structure after recording

It also supports **headless mode** via command-line arguments for fully automated recording — a config file is optional since all settings can be supplied as arguments:

```
PinballRecorder.exe --autostart --rom tz_ps3 --duration 30
```

---

## Using the GUI Integration

### Step 1: Open PinUP Integration Section

The **PinUP Popper Integration** section is at the bottom of the main window. On startup, PinballRecorder automatically searches common install locations for `PUPDatabase.db`:

- `C:\vPinball\PinUPSystem\PUPDatabase.db`
- `C:\PinUPSystem\PUPDatabase.db`
- `%USERPROFILE%\PinUPSystem\PUPDatabase.db`

If found, it is loaded silently. If not found, a log message appears — no error dialog. You can also browse manually or click **🔍** to search.

### Step 2: Select a Table

Pick a table from the **Table / ROM** dropdown. The list is loaded from the `Games` and `Emulators` tables in the database. The ROM name and emulator media path are read automatically.

A **Destination Preview** grid appears showing the exact output paths:

| Stream | Destination |
|--------|-------------|
| Playfield | `{MediaDir}/PlayField/{ROM}.mp4` |
| Backglass | `{MediaDir}/BackGlass/{ROM}.mp4` |
| FullDMD | `{MediaDir}/DMD/{ROM}.mp4` |
| Audio | `{MediaDir}/Audio/{ROM}.mp3` |

### Step 3: Record

Click **▶ START RECORDING**. When recording finishes, files are automatically moved into the POPMedia folder structure.

If a file already exists at a destination, a dialog asks: **Overwrite**, **Append** (adds a numbered suffix), or **Skip**.

### Step 4: Open Folder

If **"Open output folder when recording finishes"** is checked, the emulator's media directory is opened in Explorer (not the Output Folder) so you can review the moved files.

### Also Keep Copies

Check **"Also keep copies in Output Folder"** to *copy* files to POPMedia while keeping the originals in the Output Folder. When unchecked (the default), files are *moved* — the Output Folder is used as temporary storage only.

---

## Saving a Per-Table Config

After selecting a table, use **File → Save As…** — the file dialog will suggest the ROM name as the filename. This saves a config that remembers the selected table, so the next time you open it, the table selection is restored automatically.

---

## CLI / Headless Mode for Automation

### Command-Line Arguments

```
PinballRecorder.exe [--config PATH] [--autostart] [options…]
```

| Argument | Description |
|----------|-------------|
| `--config PATH` | Base JSON config file (optional — defaults to saved config) |
| `--autostart` | Start recording immediately on launch and exit when done |
| `--rom NAME` | PinUP ROM name — selects the table and routes output to the correct POPMedia folder |
| `--duration SECS` | Recording duration for all enabled screens |
| `--delay SECS` | Start delay for all enabled screens |
| `--output-folder PATH` | Output folder for temporary recording files |
| `--audio-enabled 0\|1` | Enable or disable audio recording |
| `--screen-<name>-duration SECS` | Duration for a specific screen (`playfield`, `backglass`, `fulldmd`) |
| `--screen-<name>-enabled 0\|1` | Enable or disable a specific screen |

See [README.md](../README.md#cli--headless-mode) for the full argument reference.

### Headless Mode Behaviour

| Situation | Behaviour |
|-----------|-----------|
| All screens have `duration > 0` | Records for that duration, saves, exits |
| Any screen has `duration = 0` | Defaults to **20 seconds** as a safety guard |
| FFmpeg not found | Logs error, exits after 2 seconds |
| Config file not found | Prints error, exits immediately |

The recorder window is still visible in headless mode — it shows log output and closes automatically when recording finishes.

---

## PinUP Popper Automation Setup

### Step 1: Configure your base settings

1. Launch PinballRecorder normally
2. Set up your screen regions, audio device, and output folder
3. Use **File → Save** — this saves to `configs/default_config.json`, which headless mode will use when no `--config` is specified

Ensure each enabled screen has a non-zero `duration`:

```json
"Playfield": { "enabled": true, "duration": 30, ... }
```

Or supply `--duration` on the command line (overrides any value in the config file).

### Step 2: Create a Capture Script

There are two approaches. **Option A** (simplest) — pass `--rom` directly, no per-table config files needed:

```bat
@echo off
:: capture_table.bat — called by PinUP Popper with the ROM name as %1
set RECORDER=C:\vPinball\PinballRecorder\PinballRecorder.exe
"%RECORDER%" --autostart --rom %1 --duration 30
```

**Option B** — per-table config files (useful if individual tables need different screen layouts):

```bat
@echo off
set CONFIG=C:\vPinball\PinballRecorder\%1.json
set RECORDER=C:\vPinball\PinballRecorder\PinballRecorder.exe

if not exist "%CONFIG%" (
    echo Config not found: %CONFIG%
    exit /b 1
)

"%RECORDER%" --config "%CONFIG%" --autostart
```

Call either script from PinUP Popper with the ROM name as the argument:

```
capture_table.bat tz_ps3
```

### Step 3: Hook Into PinUP Popper

In PinUP Popper, add a **Table Launch Script** or use **Game Manager → Script**:

1. Open PinUP Popper Setup
2. Go to **Game Manager** → select your table → **Script**
3. Add a call to your batch file:

```
Shell "C:\vPinball\PinballRecorder\capture_table.bat tz_ps3"
```

Or call the exe directly with `--rom` — no batch file required:

```
Shell "C:\vPinball\PinballRecorder\PinballRecorder.exe --autostart --rom tz_ps3 --duration 30"
```

---

## Example Config

```json
{
  "output_folder": "C:\\vPinball\\PinUPSystem\\PupCapture",
  "file_prefix": "pinball",
  "ffmpeg_path": "C:\\vPinball\\PinballRecorder\\ffmpeg.exe",
  "audio_enabled": true,
  "audio_device": "[Loopback] Speakers (USB Device)",
  "audio_delay": 0,
  "audio_duration": 0,
  "audio_match_screen": "Playfield",
  "open_folder_after": false,
  "log_to_file": true,
  "pinup_db_path": "C:\\vPinball\\PinUPSystem\\PUPDatabase.db",
  "pinup_game_rom": "tz_ps3",
  "pinup_game_media_dir": "C:\\vPinball\\PinUPSystem\\POPMedia\\Visual Pinball X",
  "pinup_also_save": false,
  "screens": {
    "Playfield": {
      "enabled": true,
      "monitor": "Monitor 1",
      "x": 0, "y": 0, "width": 1920, "height": 1080,
      "fps": 60, "delay": 5, "duration": 30
    },
    "Backglass": {
      "enabled": true,
      "monitor": "Monitor 2",
      "x": 1920, "y": 0, "width": 1280, "height": 720,
      "fps": 30, "delay": 5, "duration": 30
    },
    "FullDMD": {
      "enabled": true,
      "monitor": "Monitor 2",
      "x": 3200, "y": 900, "width": 1280, "height": 180,
      "fps": 30, "delay": 5, "duration": 30
    }
  }
}
```
