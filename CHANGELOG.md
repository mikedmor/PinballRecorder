# Changelog

## [Unreleased]

### Added
- **Application Audio Capture mode**: audio can now be captured directly from one or more specific application windows using the Windows Application Loopback API (Windows 10 2004 / build 19041+). This bypasses the system master volume entirely, so recordings are always at full amplitude regardless of how low the cabinet speakers are set. Select "Application" under Capture Mode in the Audio section, then pick one or more windows from the list. Multiple selections are mixed into a single MP3 using FFmpeg's `amix` filter.
- **Right-click to ignore an app in the Application audio list**: right-clicking any window in the Application capture listbox shows a context menu with "Ignore — hide from list". The exe name is saved to `global.json` and the list refreshes immediately. A "Manage ignored apps…" shortcut opens Preferences.
- **Ignored Audio Apps in Preferences**: File → Preferences now has an "IGNORED AUDIO APPS" section listing every suppressed exe. Individual entries can be removed; "Restore Defaults" resets the list to the built-in set of known-incompatible apps (Chromium browsers, Discord, Windows Media Player, Explorer).

### Changed
- **Application audio window list filters by active WASAPI session**: the listbox now only shows windows whose process (or its direct parent) has an active WASAPI render session — a prerequisite for the Application Loopback API. Apps not currently playing audio are hidden. If no active audio is detected the list falls back to showing all windows. Refresh the list while the target app is playing to ensure it appears.
- **Application audio window list hides incompatible apps**: Chromium-based apps (Chrome, Brave, Edge, Opera, Vivaldi, Discord), apps using DirectSound/WaveOut (Windows Media Player), the Windows shell (Explorer), Steam, and Razer background services are excluded by default — they have visible WASAPI sessions but their audio pipeline cannot be reached by the Application Loopback API due to Job Object sandboxing or API incompatibility. The list is now user-configurable via right-click or Preferences.
- **Screen Configuration coordinates are now monitor-relative**: X and Y fields show offsets from the selected monitor's top-left corner (e.g. X=0, Y=0 means the monitor's origin), rather than global desktop coordinates relative to the primary monitor. Existing configs are migrated automatically on first load.
- **Preview overlay always uses current monitor state**: the Show Preview button and all snap actions (Full Screen, Full Width, etc.) now re-detect monitor layout at the moment they run, so a monitor that rotated or changed resolution after the app started is handled correctly.
- **FullDMD PinUP folder**: recordings are now filed into the `Menu` folder instead of `DMD` to match PinUP Popper's actual capture folder naming
- **Per-screen recording delay now honoured**: each screen's delay is measured from a shared reference time and runs in parallel (threads), so all screens start at their configured moment rather than ignoring the delay entirely
- **Audio delay uses shared time reference**: the audio delay is now computed relative to the same start reference as the video streams, so if the pre-beep countdown already consumed part of the delay, no extra wait is added

### Fixed
- **Application audio window title change causing "Window not found" at record time**: apps like Discord update their window title with the active channel/activity. The PID stored at selection time is now used as a fallback so recording still proceeds even when the title changes between Refresh and record.
- **Application audio root-process walk climbing too far up the tree**: the process-tree walk in `_find_root_audio_pid` was comparing the current process's own exe name against itself on every iteration rather than checking the parent's exe, so it walked all the way up to `explorer.exe` and then tried every one of its dozens of child processes before giving up.
- **Application audio capture failing with E_ILLEGAL_METHOD_CALL (0x8000000E)**: `ActivateAudioInterfaceAsync` is called in a fresh subprocess (`--capture-audio` internal subcommand) to guarantee a clean COM environment, isolated from any COM state set by tkinter or pyaudiowpatch in the main process. The subprocess output is relayed back to the main log.
- **Application audio capture: root process resolution**: for multi-process apps like browsers (Chrome, Brave, Edge), the window PID is now walked up the process tree to the root parent process before capture begins. Combined with `INCLUDE_TARGET_PROCESS_TREE`, this ensures the Audio Service child process is included in the capture.
- **F8 global start hotkey**: pressing F8 now starts recording from anywhere on the desktop (no need to focus the PinballRecorder window). A short two-tone acknowledgement beep plays immediately when F8 is detected — before any recording delay begins — so the sound is never captured.
- **Start beep**: a short 880 Hz tone plays 1 second before the first recording stream begins, giving audible notice that recording is imminent (the 1-second gap keeps the beep out of the captured audio)
- **Done chime**: a three-note ascending tone plays when all video and audio recordings have finished, so the user is notified even when the window is in the background
- **FFmpeg relative path not found on startup**: a path like `.\ffmpeg.exe` saved in Preferences was checked against the process working directory instead of the app folder, causing the Start button to stay disabled even when FFmpeg was present next to the exe. The path is now resolved relative to the app directory.
- **No way to clear Table/ROM selection**: a Clear button (red) is now shown next to the Refresh button in the PinUP Popper Integration section so the selection can be removed without restarting.
- **Window Focus selection required manual text deletion**: a Clear button is now shown next to the Refresh button in the Window Focus section for one-click clearing.
- WASAPI loopback capture errors (e.g. device failing to open) are now caught and logged instead of silently producing an empty/missing audio file
- WAV→MP3 conversion failures now log the FFmpeg return code and last error output instead of appearing as a silent "May be incomplete" warning
- When the WAV intermediate file is not found after capture, the log now explicitly reports "WASAPI capture likely failed" instead of giving no explanation

## [v2.1.0] - 2026-05-11

### Added
- Headless CLI now accepts all config values as command-line arguments (`--output-folder`, `--file-prefix`, `--rom`, `--screen-*`, `--audio-*`, etc.) so a config JSON file is no longer required for automation

### Changed

### Fixed
- Title bar showed v1.1.0 instead of v2.0.0 — `APP_VERSION` constant in source was never updated before the v2.0.0 build
- SQLite connection in `load_pinup_games` now uses a context manager so the connection closes correctly even if an error occurs mid-query
- Clearing Recent Files in Preferences and then clicking Cancel no longer wipes the in-memory recent-files list; the clear is deferred until OK is confirmed

## [v2.0.0] - 2026-05-11

### Added
- Default recording duration changed from 0 (unlimited) to 20 seconds for all screens — safer out-of-the-box behaviour
- Audio delay/duration now stays in sync live when a match screen is selected and that screen's values are changed (previously only synced when switching the match dropdown)
- **Preferences dialog** (File → Preferences…) for global settings shared across all config profiles:
  - FFmpeg path (global, shared across all configs, stored in `global.json`)
  - PinUP Database path (with browse and auto-detect buttons)
  - Open output folder when recording finishes
  - Save session log to file
  - Recent files list with a Clear button
- Preferences dialog has **PATHS** and **OPTIONS** section headers with a separator for clearer visual grouping
- `configs/` subfolder: all config files (including `default_config.json`) now live in `configs/` next to the exe, keeping the install directory clean; existing `default_config.json` at the root is migrated automatically on first launch
- `global.json` created automatically on first run and holds all preference settings — independent of any config profile
- One-time migration: FFmpeg path saved in an old per-config file is picked up and moved into `global.json` on first launch
- **START RECORDING button is disabled** when FFmpeg is not configured; an inline warning message explains where to set the path
- **PinUP Table/ROM selector and Refresh button are disabled** when no valid PinUP database is configured or found
- Version number shown in title bar (e.g. `Pinball Screen Recorder v1.1.0`)
- Default config file is now `default_config.json` (was `recorder_config.json`) — better reflects its role as the fallback config
- File menu header shows the currently loaded config filename as a non-clickable label at the top of the File menu
- File menu (File → New / Open… / Save / Save As…) for managing named config profiles
- Keyboard shortcuts: Ctrl+N (New), Ctrl+O (Open), Ctrl+S (Save), Ctrl+Shift+S (Save As)
- Title bar shows the active config filename when a non-default file is loaded
- Open Recent submenu (last 8 files) in the File menu
- Save As… dialog suggests the ROM name as the default filename when a PinUP table is selected
- On first run (no saved config), monitors are auto-detected and assigned to screens automatically
- Table/ROM selection now persists across restarts — the combo is re-populated and re-selected after the DB loads on startup
- PinUP Popper Integration section: auto-detects PUPDatabase.db, loads table list, moves recorded files into the correct POPMedia capture folder structure after recording
- PinUP emulator media path is now auto-derived from the `Emulators.DirMedia` field in the DB — no more manual "Capture Folder" entry required
- Destination preview grid in the PinUP section shows exact output paths (Playfield / BackGlass / FullDMD / Audio) as soon as a table is selected
- Audio file is now moved/copied to the POPMedia `Audio` subfolder after recording when a PinUP table is selected
- Audio section now has Delay (s), Duration (s), and a "Match screen" dropdown; selecting a screen copies its delay/duration into the audio fields
- "Also keep copies in Output Folder" checkbox in Recording Settings (shown only when a PinUP game is selected)
- Conflict dialog when a PinUP capture file already exists: Overwrite / Append (numbered suffix) / Skip
- "Open output folder when recording finishes" — when a PinUP table is selected, opens the emulator's media directory instead of the Output Folder
- "Save session log to PinballRecorder.log" checkbox; log always written in headless mode
- FFmpeg version and available hardware encoder info (NVENC / QSV / AMF) shown below the FFmpeg path field in Preferences
- F9 global hotkey to stop recording even when the app window is not focused
- Donate button in the toolbar

### Changed
- **PinUP Popper Integration** section: database path row removed — path is now configured in Preferences only; status label shows the configured DB filename or "not configured"
- **Recording Settings** section: FFmpeg path entry and buttons removed from main window; FFmpeg status line still shown (version/codec info, or warning when not configured)
- `pinup_db_path`, `log_to_file`, `open_folder_after`, and `recent_files` moved out of per-session config files and into `global.json` — these settings are now truly global and no longer saved/loaded per-profile
- Recording Settings section: "Open output folder" and "Save session log" checkboxes removed from main screen; now in Preferences dialog
- Capture Device combobox now stretches to fill available width (was fixed-width)
- File Prefix field now stretches to fill the full row width (was truncated to a fixed short width)
- Audio section Delay / Duration / Match screen controls are now in a single flat row for consistent alignment
- "Refresh" buttons (Tables, Capture Device, Window Focus) no longer use emoji — fixes vertical misalignment of icon vs. text on Windows
- Title bar no longer shows 🎯 emoji — cleaner look, especially for taskbar/Alt+Tab
- Auto-Detect Monitors button spacing normalized to match other buttons
- Default `audio_match_screen` is now `"Playfield"` — audio delay/duration automatically follows the Playfield screen on fresh installs
- Per-screen FPS setting now correctly applied to FFmpeg `-framerate` (was hardcoded to 30)
- Per-screen Duration now correctly passed to FFmpeg `-t` per stream (was using undefined variable)
- Audio device refresh log simplified — no longer lists every device name
- README.md, docs/config-format.md, and docs/pinup-popper-integration.md fully rewritten to reflect all current features

### Fixed
- FFmpeg placed next to the exe (e.g. `ffmpeg.exe` in the same folder) is now detected automatically on first launch and saved to `global.json` — the setup assistant no longer appears unnecessarily
- PinUP database configured in Preferences is now correctly loaded on startup; the integration section no longer shows "No database configured" when a path is already saved in `global.json`
- Download FFmpeg option in the Setup Assistant now saves a relative path (`.\ffmpeg.exe`) instead of an absolute path, matching the behaviour of the auto-detect scan
- `NameError` crash when any screen had a non-zero Duration set (duration variable used before assignment)
- Dark theme not applying correctly to LabelFrame interiors on Windows 11 (clam theme)
- Config auto-save now writes to **both** `default_config.json` and the currently open named config file
- Loading a config now deep-merges per-screen defaults from `DEFAULT_CONFIG` — older configs no longer silently lose newer per-screen fields
- Audio Delay and Duration spinboxes are disabled automatically when a Match Screen is selected
- All three refresh buttons (Capture Device, Table/ROM, Window Focus) use a consistent `🔄 Refresh` icon+text style
- Window Focus section converted from pack to grid layout for proper alignment
- When a PinUP table is selected, only the File Prefix is greyed out (not the Output Folder)

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
