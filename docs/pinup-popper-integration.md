# PinUP Popper Integration Guide

This guide explains how to use PinballRecorder's built-in PinUP integration and how to automate recording from PinUP Popper using CLI/headless mode.

---

## Overview

PinballRecorder connects directly to PinUP Popper's database (`PUPDatabase.db`) to:

- Load your full table list with ROM names and emulator media paths
- Preview the exact destination file paths before recording
- Automatically move (or copy) recorded files into the correct POPMedia folder structure after recording

It also supports **headless mode** via command-line arguments for fully automated recording:

```
PinballRecorder.exe --config "path\to\config.json" --autostart
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
PinballRecorder.exe [--config PATH] [--autostart]
```

| Argument | Description |
|----------|-------------|
| `--config PATH` | Load a specific JSON config file |
| `--autostart` | Start recording immediately on launch and exit when done |

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

### Step 1: Configure and Save a Per-Table Config

1. Launch PinballRecorder normally
2. Set up your screen regions and audio device
3. Select your table from the **Table / ROM** dropdown in the PinUP section
4. Use **File → Save As…** — it will suggest the ROM name (e.g. `tz_ps3.json`)
5. Save the config next to `PinballRecorder.exe`

Ensure each enabled screen has a non-zero `duration` for headless mode:

```json
"Playfield": { "enabled": true, "duration": 30, ... }
```

### Step 2: Create a Capture Script

Create a batch file `capture_table.bat`:

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

Call it from PinUP Popper with the ROM name as the argument:

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

Or use a **shared config** for all tables and rely on the built-in PinUP integration to move files to the correct location automatically — in that case one config file works for all tables:

```
Shell "C:\vPinball\PinballRecorder\PinballRecorder.exe --config C:\vPinball\PinballRecorder\popper_capture.json --autostart"
```

> **Tip:** When using a shared config with the PinUP integration, open the GUI first, select the correct table in the PinUP section, and save the config. PinballRecorder remembers the selected table and will use it in headless mode.

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
