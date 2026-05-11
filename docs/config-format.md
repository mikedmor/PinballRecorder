# Configuration File Format

PinballRecorder uses two JSON files next to the `.exe`:

| File | Purpose |
|------|---------|
| `default_config.json` | Per-session settings (screen layout, audio, PinUP table selection). Loaded on startup; overridden by named configs opened via File → Open. |
| `global.json` | Global preferences shared across all config profiles (PinUP DB path, open-folder setting, log setting, recent files). Edited via **File → Preferences…**. |

You can create additional named config files (e.g. per-table configs) and load them via **File → Open** or the `--config` argument.

---

## Session Config Schema (`default_config.json` / named configs)

These fields are saved and loaded with each config profile:

```jsonc
{
  // Where to save recorded files (used as temp storage in PinUP mode)
  "output_folder": "C:/vPinball/PinUPSystem/PupCapture",

  // Filename prefix — output: {prefix}_{Screen}_{timestamp}.mp4
  // Ignored (greyed out) when a PinUP table is selected and "Also keep copies" is off
  "file_prefix": "pinball",

  // Full path to ffmpeg.exe (empty = auto-detect)
  "ffmpeg_path": "",

  // Whether to record system audio to a separate MP3
  "audio_enabled": true,

  // Audio capture device name as shown in the app dropdown
  "audio_device": "[Loopback] Speakers (USB Device)",

  // Audio start delay in seconds (0 = start immediately)
  "audio_delay": 0,

  // Audio recording duration in seconds (0 = auto-match longest enabled screen)
  "audio_duration": 0,

  // Mirror delay+duration from this screen ("Playfield", "Backglass", "FullDMD", or "" for manual)
  "audio_match_screen": "Playfield",

  // Window title to bring to foreground before recording starts (empty = skip)
  "window_title": "",

  // ── PinUP Popper Integration ──────────────────────────────────────────────

  // ROM name of the selected table (saved automatically when a table is picked in the GUI)
  "pinup_game_rom": "tz_ps3",

  // Emulator's POPMedia root folder (populated automatically from the DB)
  "pinup_game_media_dir": "C:/vPinball/PinUPSystem/POPMedia/Visual Pinball X",

  // When true, files are COPIED to POPMedia and originals kept in output_folder
  // When false (default), files are MOVED to POPMedia
  "pinup_also_save": false,

  // ── Per-screen configuration ──────────────────────────────────────────────
  "screens": {
    "Playfield": {
      "enabled": true,
      "monitor": "Monitor 1",   // Label only — informational
      "x": 0,                   // Left edge of capture region (pixels, virtual desktop)
      "y": 0,                   // Top edge of capture region
      "width": 1920,
      "height": 1080,
      "fps": 30,                // Frames per second
      "delay": 5,               // Seconds to wait before starting this stream
      "duration": 20            // Seconds to record (0 = manual stop)
    },
    "Backglass": {
      "enabled": true,
      "monitor": "Monitor 2",
      "x": 1920, "y": 0,
      "width": 1280, "height": 720,
      "fps": 30, "delay": 5, "duration": 20
    },
    "FullDMD": {
      "enabled": true,
      "monitor": "Monitor 2",
      "x": 3200, "y": 900,
      "width": 1280, "height": 180,
      "fps": 30, "delay": 5, "duration": 20
    }
  }
}
```

---

## Field Reference

### Top-level fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `output_folder` | string | `C:\vPinball\PinUPSystem\PupCapture` | Folder where recordings are saved (temp folder in PinUP mode) |
| `file_prefix` | string | `pinball` | Prefix for output filenames |
| `ffmpeg_path` | string | `""` | Path to `ffmpeg.exe` (empty = auto-detect) |
| `audio_enabled` | bool | `true` | Whether to record system audio |
| `audio_device` | string | `""` | Audio capture device name |
| `audio_delay` | int | `0` | Seconds before starting audio capture |
| `audio_duration` | int | `0` | Audio duration in seconds (0 = auto-match longest screen) |
| `audio_match_screen` | string | `"Playfield"` | Screen whose delay/duration the audio mirrors (empty = manual) |
| `window_title` | string | `""` | Window title to focus before recording starts |
| `pinup_game_rom` | string | `""` | ROM name of the selected PinUP table |
| `pinup_game_media_dir` | string | `""` | Emulator POPMedia root (auto-populated from DB) |
| `pinup_also_save` | bool | `false` | Copy files to POPMedia and keep originals in output folder |
| `screens` | object | — | Per-screen configuration (see below) |

> `open_folder_after`, `log_to_file`, `pinup_db_path`, and `recent_files` are **global preferences** — they live in `global.json` only and are not saved per config profile. Edit them via **File → Preferences…**.

### Per-screen fields (inside `"screens"`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Whether to record this screen |
| `monitor` | string | `""` | Monitor label (informational only) |
| `x` | int | `0` | X coordinate of the capture region (virtual desktop pixels) |
| `y` | int | `0` | Y coordinate of the capture region |
| `width` | int | `1920` | Width of the capture region in pixels |
| `height` | int | `1080` | Height of the capture region in pixels |
| `fps` | int | `30` | Frames per second for this stream |
| `delay` | int | `5` | Seconds to wait before starting this stream |
| `duration` | int | `20` | Recording duration in seconds (0 = record until manually stopped) |

---

## Notes

- **Coordinates** use the Windows virtual desktop coordinate system. Use the **🖥 Preview** overlays in the GUI to find the right values visually.
- **Duration = 0** in `--autostart` (headless) mode means the stream records indefinitely — always set a non-zero duration when using automated mode.
- **Audio match screen** defaults to `"Playfield"`. When set, the audio delay and duration fields are disabled and mirror the selected screen's values automatically.
- **PinUP fields** (`pinup_game_rom`, `pinup_game_media_dir`) are populated automatically when you select a table in the GUI; you do not need to set them manually.
- Config files do not need to include all fields — any missing field falls back to the built-in default.

---

## Global Preferences Schema (`global.json`)

These settings are shared across all config profiles and are **not** stored in session config files. Edit via **File → Preferences…**.

```jsonc
{
  // Path to PUPDatabase.db (auto-detected on startup if empty)
  "pinup_db_path": "C:/vPinball/PinUPSystem/PUPDatabase.db",

  // Write a session log to PinballRecorder.log next to the exe
  "log_to_file": false,

  // Open the output folder (or PinUP media dir) in Explorer when recording finishes
  "open_folder_after": false,

  // Last 8 opened config files (managed automatically by the app)
  "recent_files": []
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pinup_db_path` | string | `""` | Path to `PUPDatabase.db`; auto-detected on startup |
| `log_to_file` | bool | `false` | Write session log to `PinballRecorder.log` |
| `open_folder_after` | bool | `false` | Open output/media folder in Explorer after recording |
| `recent_files` | array | `[]` | Paths of recently opened config files (max 8) |
