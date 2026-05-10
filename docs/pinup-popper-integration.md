# PinUP Popper Integration Guide

This guide explains how to integrate PinballRecorder with [PinUP Popper](https://www.nailbuster.com/wikipinup/doku.php) to automatically capture recordings when launching or playing a table.

---

## Overview

PinballRecorder supports a **headless mode** via command-line arguments:

```
PinballRecorder.exe --config "path\to\config.json" --autostart
```

- `--config` — loads a specific JSON config (screen positions, output folder, duration, etc.)
- `--autostart` — starts recording immediately on launch and **exits automatically** when all durations complete

This makes it trivial to trigger from PinUP Popper's scripting system.

---

## Step 1: Set Up Your Config File

Use the GUI to configure your screen regions, audio device, output folder, and FPS exactly how you want them. Your settings are saved to `recorder_config.json` next to the `.exe`.

For PinUP Popper, you likely want a **shared config** (same screen layout for all tables) with a fixed duration. Copy `recorder_config.json` to a named file:

```
C:\vPinball\PinballRecorder\popper_capture.json
```

Open it and ensure each enabled screen has a non-zero `duration`:

```json
"Playfield": {
  "enabled": true,
  "duration": 30,
  ...
}
```

> Set `duration` to however many seconds of gameplay you want to capture. All enabled screens should have the same duration for clean synchronized clips.

---

## Step 2: Identify Your Output Folder

Set `output_folder` in your config to the folder where PinUP Popper expects media:

```json
"output_folder": "C:\\vPinball\\PinUPSystem\\PupCapture"
```

You may also want to set `file_prefix` to the table name if generating table-specific captures:

```json
"file_prefix": "twilight_zone"
```

> **Tip:** You can generate per-table config files programmatically by copying the base config and changing `output_folder` and `file_prefix`.

---

## Step 3: Create a Capture Script

Create a batch file `capture_table.bat`:

```bat
@echo off
set TABLE_NAME=%1
set CONFIG=C:\vPinball\PinballRecorder\popper_capture.json
set RECORDER=C:\vPinball\PinballRecorder\PinballRecorder.exe

"%RECORDER%" --config "%CONFIG%" --autostart
```

Or for per-table configs, generate them dynamically using PowerShell:

```powershell
param([string]$TableName)

$base = Get-Content "C:\vPinball\PinballRecorder\popper_capture.json" | ConvertFrom-Json
$base.file_prefix = $TableName
$base.output_folder = "C:\vPinball\PinUPSystem\PupCapture\$TableName"

New-Item -ItemType Directory -Force -Path $base.output_folder | Out-Null
$base | ConvertTo-Json -Depth 10 | Set-Content "$env:TEMP\pr_capture.json"

& "C:\vPinball\PinballRecorder\PinballRecorder.exe" --config "$env:TEMP\pr_capture.json" --autostart
```

---

## Step 4: Hook Into PinUP Popper

In PinUP Popper, you can trigger external scripts from:

- **Table launch scripts** — run before/after a table launches
- **PinUP Player media triggers** — execute commands at specific playback events
- **PuP-Pack scripts** — call external processes from within a PuP-Pack

A common approach is to add a **launch script** that starts capture when the table loads:

1. Open PinUP Popper Setup
2. Go to **Game Manager** → select your table → **Script**
3. In the launch script, add a call to your batch file:

```
Shell "C:\vPinball\PinballRecorder\capture_table.bat TwilightZone"
```

---

## Behaviour in Headless Mode

| Situation | Behaviour |
|-----------|-----------|
| All screens have `duration > 0` | Records for that duration, then saves files and exits |
| Any screen has `duration = 0` | Records until manually closed (window close button) |
| FFmpeg not found | Logs error, exits after 2 seconds |
| Config file not found | Prints error to console, exits immediately |

The recorder window **is still visible** in headless mode — it shows the log output so you can see what's happening. It will close itself automatically when recording finishes.

---

## Example Config for PinUP Popper

```json
{
  "output_folder": "C:\\vPinball\\PinUPSystem\\PupCapture",
  "file_prefix": "pinball",
  "ffmpeg_path": "C:\\vPinball\\PinballRecorder\\ffmpeg.exe",
  "audio_enabled": true,
  "audio_device": "[Loopback] Speakers (Razer Kraken V4 Pro - Game)",
  "window_title": "",
  "screens": {
    "Playfield": {
      "enabled": true,
      "monitor": "Monitor 1",
      "x": 0, "y": 0,
      "width": 1920, "height": 1080,
      "fps": 60, "delay": 3, "duration": 30
    },
    "Backglass": {
      "enabled": true,
      "monitor": "Monitor 2",
      "x": 1920, "y": 0,
      "width": 1280, "height": 720,
      "fps": 30, "delay": 3, "duration": 30
    },
    "FullDMD": {
      "enabled": true,
      "monitor": "Monitor 2",
      "x": 1920, "y": 720,
      "width": 1280, "height": 360,
      "fps": 30, "delay": 3, "duration": 30
    }
  }
}
```
