# Changelog

## [Unreleased]

### Added
- File menu (File → New / Open… / Save / Save As…) for managing named config profiles
- Keyboard shortcuts: Ctrl+N (New), Ctrl+O (Open), Ctrl+S (Save), Ctrl+Shift+S (Save As)
- Title bar shows the active config filename when a non-default file is loaded

### Changed

### Fixed

## [v1.0.0]

### Added
- CLI arguments `--config` and `--autostart` for headless/automated recording (PinUP Popper integration)
- Per-screen FPS setting (default 30, configurable up to 60+)
- Per-screen Delay and Duration settings (moved from global Recording Settings)
- WASAPI loopback audio capture via `pyaudiowpatch` — captures system audio from any app without Stereo Mix
- `[Loopback]` devices listed at top of audio capture device dropdown
- FFmpeg setup dialog accessible via new **⚙ Setup** button at any time (not just on first run)
- Portable FFmpeg download option in setup dialog (saves `ffmpeg.exe` next to the `.exe`)
- Stderr drain threads per FFmpeg process to prevent pipe buffer deadlock
- Diagnostic stats logged per stream after recording completes
- Per-screen parallel launch threads with independent delay countdown
- Auto-close after recording in headless mode
- `README.md`, `LICENSE`, `docs/config-format.md`, `docs/pinup-popper-integration.md`

### Changed
- Audio checkbox label no longer mentions Stereo Mix requirement
- FFmpeg setup dialog description updated to reflect audio is handled without special FFmpeg build
- Global Start Delay / Duration removed from Recording Settings (now per-screen)
- Monitor auto-detect no longer overwrites saved screen config on startup (`force_assign=False`)
- Config path uses `sys.executable` directory when frozen (fixes config persistence in PyInstaller builds)
- Audio duration auto-derived as max of all enabled screen durations
- FFmpeg download option renamed to reflect portable/USB use case

### Fixed
- `_btn` helper keyword argument conflicts (`font`, `padx`, `pady`)
- Odd video dimensions causing libx264 encoding failure (added `crop=trunc(iw/2)*2:trunc(ih/2)*2`)
- Missing `-pix_fmt yuv420p` causing Windows Media Player incompatibility
- 0-byte audio files caused by stderr pipe buffer filling and blocking FFmpeg
- CTRL_BREAK not delivered when using `CREATE_NO_WINDOW` (switched to `CREATE_NEW_CONSOLE` + `SW_HIDE`)
- Config not persisting between runs when built with PyInstaller
- Monitor dropdown overwriting saved positions on every startup
