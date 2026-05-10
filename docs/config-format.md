# Configuration File Format

PinballRecorder saves and loads settings from a JSON file (`recorder_config.json` by default, located next to the `.exe`).

You can create additional named config files (e.g. per-table configs for PinUP Popper integration) and pass them via the `--config` argument.

---

## Full Schema

```jsonc
{
  // Where to save recorded files
  "output_folder": "C:/Users/You/Videos/Pinball",

  // Filename prefix — output: {prefix}_{Screen}_{timestamp}.mp4
  "file_prefix": "pinball",

  // Full path to ffmpeg.exe
  "ffmpeg_path": "C:/path/to/ffmpeg.exe",

  // Whether to record system audio to a separate MP3
  "audio_enabled": true,

  // Audio capture device name as shown in the app dropdown
  // Use "[Loopback] " prefix for WASAPI loopback devices
  "audio_device": "[Loopback] Speakers (USB Device)",

  // Window title to bring to foreground before recording starts (optional)
  "window_title": "",

  "screens": {
    "Playfield": {
      "enabled": true,
      "monitor": "Monitor 1",     // Label only — informational
      "x": 0,                     // Left edge of capture region (pixels)
      "y": 0,                     // Top edge of capture region (pixels)
      "width": 1920,              // Width of capture region
      "height": 1080,             // Height of capture region
      "fps": 60,                  // Frames per second (default: 30)
      "delay": 3,                 // Seconds to wait before starting (default: 5)
      "duration": 30              // Seconds to record (0 = manual stop)
    },
    "Backglass": {
      "enabled": true,
      "monitor": "Monitor 2",
      "x": 1920,
      "y": 0,
      "width": 1280,
      "height": 720,
      "fps": 30,
      "delay": 3,
      "duration": 30
    },
    "FullDMD": {
      "enabled": true,
      "monitor": "Monitor 2",
      "x": 1920,
      "y": 720,
      "width": 1280,
      "height": 360,
      "fps": 30,
      "delay": 3,
      "duration": 30
    }
  }
}
```

---

## Field Reference

### Top-level fields

| Field | Type | Description |
|-------|------|-------------|
| `output_folder` | string | Absolute path to the folder where recordings are saved |
| `file_prefix` | string | Prefix for output filenames |
| `ffmpeg_path` | string | Absolute path to `ffmpeg.exe` |
| `audio_enabled` | bool | Whether to record system audio |
| `audio_device` | string | Audio capture device name (from the app dropdown) |
| `window_title` | string | Window to focus before recording starts (empty = skip) |
| `screens` | object | Per-screen configuration (see below) |

### Per-screen fields (inside `"screens"`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Whether to record this screen |
| `monitor` | string | `""` | Monitor label (informational only) |
| `x` | int | `0` | X coordinate of the capture region (from left edge of virtual desktop) |
| `y` | int | `0` | Y coordinate of the capture region (from top edge of virtual desktop) |
| `width` | int | `1920` | Width of the capture region in pixels |
| `height` | int | `1080` | Height of the capture region in pixels |
| `fps` | int | `30` | Frames per second for this stream |
| `delay` | int | `5` | Seconds to wait before starting this stream |
| `duration` | int | `0` | Recording duration in seconds (0 = record until manually stopped) |

---

## Notes

- **Coordinates** use the Windows virtual desktop coordinate system. Use the **🖥 Preview** button in the GUI to find the right values visually.
- **Duration = 0** in `--autostart` mode means the stream records indefinitely — make sure to set a non-zero duration for all enabled screens when using headless/automated mode.
- **Audio duration** is automatically set to the maximum `duration` across all enabled screens.
- The `monitor` field is cosmetic and does not affect recording — the actual capture region is determined by `x`, `y`, `width`, and `height`.
- Config files do not need to include all fields — any missing field falls back to the built-in default.
