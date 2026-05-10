#!/usr/bin/env python3
"""
Pinball Screen Recorder
A GUI tool for recording multiple pinball cabinet screens simultaneously to separate files.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import subprocess
import threading
import json
import os
import sys
import time
import ctypes
import shutil
import webbrowser
import urllib.request
import zipfile
from ctypes import windll, wintypes
from datetime import datetime

# ─── Monitor Detection ─────────────────────────────────────────────────────────

def enum_display_monitors():
    """Enumerate all connected monitors using the Windows API."""
    monitors = []

    class MONITORINFOEX(ctypes.Structure):
        _fields_ = [
            ("cbSize",    ctypes.c_ulong),
            ("rcMonitor", wintypes.RECT),
            ("rcWork",    wintypes.RECT),
            ("dwFlags",   ctypes.c_ulong),
            ("szDevice",  ctypes.c_wchar * 32),
        ]

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(wintypes.RECT),
        ctypes.POINTER(ctypes.c_ulong),
    )

    def _callback(hMonitor, _hdc, _rect, _data):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        windll.user32.GetMonitorInfoW(hMonitor, ctypes.byref(info))
        r = info.rcMonitor
        monitors.append({
            "x":       r.left,
            "y":       r.top,
            "width":   r.right  - r.left,
            "height":  r.bottom - r.top,
            "name":    info.szDevice,
            "primary": bool(info.dwFlags & 1),
        })
        return True

    windll.user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_callback), 0)
    monitors.sort(key=lambda m: (m["x"], m["y"]))
    return monitors


# ─── Window Enumeration ────────────────────────────────────────────────────────

def enum_windows():
    """Return list of (hwnd, title) for all visible top-level windows."""
    windows = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    )

    def _callback(hwnd, _):
        if windll.user32.IsWindowVisible(hwnd):
            length = windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                windows.append((hwnd, buf.value))
        return True

    windll.user32.EnumWindows(EnumWindowsProc(_callback), 0)
    return windows


def focus_window(hwnd):
    """Bring a window to the foreground."""
    windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
    windll.user32.SetForegroundWindow(hwnd)


# ─── FFmpeg Helpers ────────────────────────────────────────────────────────────

FFMPEG_SEARCH_PATHS = [
    r"C:\vPinball\PinUPSystem\ffmpeg.exe",
    r"C:\vPinball\PinUPSystem\ffmpeg\ffmpeg.exe",
    r"C:\vPinball\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "ffmpeg", "bin", "ffmpeg.exe"),
    os.path.join(os.environ.get("USERPROFILE", ""),  "ffmpeg", "bin", "ffmpeg.exe"),
]


def find_ffmpeg():
    import glob
    # First try shutil.which (searches system PATH)
    which = shutil.which("ffmpeg")
    if which:
        return which
    # Glob-search winget Packages folder (version-agnostic)
    winget_pkgs = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    for candidate in glob.glob(
            os.path.join(winget_pkgs, "Gyan.FFmpeg*", "**", "ffmpeg.exe"),
            recursive=True):
        try:
            r = subprocess.run([candidate, "-version"], capture_output=True, timeout=3)
            if r.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    # Then try known fixed locations
    for path in FFMPEG_SEARCH_PATHS:
        if not path:
            continue
        try:
            r = subprocess.run([path, "-version"], capture_output=True, timeout=3)
            if r.returncode == 0:
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def get_audio_devices(ffmpeg_path):
    """Enumerate dshow audio capture devices, preferring Stereo Mix for system audio."""
    try:
        r = subprocess.run(
            [ffmpeg_path, "-f", "dshow", "-list_devices", "true", "-i", "dummy"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        devices = []
        for line in r.stderr.splitlines():
            # Only friendly-name lines (not Alternative name GUIDs)
            if '"' in line and "Alternative name" not in line and "(audio)" in line:
                parts = line.split('"')
                if len(parts) >= 2 and parts[1].strip():
                    devices.append(parts[1])

        # Sort so Stereo Mix floats to the top — it's the best system-audio loopback
        devices.sort(key=lambda d: (0 if "stereo mix" in d.lower() else 1, d.lower()))
        return devices
    except Exception:
        return []


def get_pyaudio_loopback_devices():
    """List WASAPI loopback devices via pyaudiowpatch (captures system playback audio).
    Each entry is prefixed with '[Loopback] ' so the recorder can route them correctly.
    Returns list of (display_name, device_index) tuples."""
    try:
        import pyaudiowpatch as pyaudio
        p = pyaudio.PyAudio()
        devices = []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d.get("isLoopbackDevice") and d.get("maxInputChannels", 0) > 0:
                devices.append((f"[Loopback] {d['name']}", i,
                                int(d["maxInputChannels"]),
                                int(d["defaultSampleRate"])))
        p.terminate()
        return devices
    except Exception:
        return []


# ─── Config ────────────────────────────────────────────────────────────────────

# When running as a PyInstaller exe, __file__ points to the temp extraction
# folder which changes every run. Use sys.executable (the .exe path) instead.
_APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
CONFIG_FILE = os.path.join(_APP_DIR, "recorder_config.json")

DEFAULT_CONFIG = {
    "output_folder": r"C:\vPinball\PinUPSystem\PupCapture",
    "file_prefix":   "pinball",
    "ffmpeg_path":   "",
    "audio_enabled": True,
    "audio_device":  "",
    "window_title":  "",
    "screens": {
        "Playfield": {"enabled": True, "x": 0,    "y": 0, "width": 1920, "height": 1080, "fps": 30, "delay": 5, "duration": 0},
        "Backglass": {"enabled": True, "x": 1920,  "y": 0, "width": 1280, "height": 720,  "fps": 30, "delay": 5, "duration": 0},
        "FullDMD":   {"enabled": True, "x": 3200, "y": 900, "width": 1280, "height": 180, "fps": 30, "delay": 5, "duration": 0},
    },
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ─── Main Application ──────────────────────────────────────────────────────────

SCREENS      = ["Playfield", "Backglass", "FullDMD"]
SCREEN_COLOR = {"Playfield": "#a6e3a1", "Backglass": "#89dceb", "FullDMD": "#fab387"}

BG        = "#1e1e2e"
FG        = "#cdd6f4"
ACCENT    = "#89b4fa"
FRAME_BG  = "#313244"
BTN_GREEN = "#a6e3a1"
BTN_RED   = "#f38ba8"
WARN      = "#f9e2af"


class PinballRecorder(tk.Tk):

    def __init__(self, cli_config=None, headless=False):
        super().__init__()
        self.configure(bg=BG)
        self.resizable(False, False)

        self._headless  = headless
        # Track which config file is currently loaded (None = default)
        self._config_path = None if cli_config is None else getattr(cli_config, "_path", None)
        # CLI config overrides the saved config when provided
        self.cfg        = cli_config if cli_config is not None else load_config()
        self.processes  = []
        self.recording  = False
        self._win_map   = {}
        self._monitors  = []
        self._overlays  = {}

        self.ffmpeg_path = self.cfg.get("ffmpeg_path") or find_ffmpeg() or ""

        self._apply_styles()
        self._build_ui()
        self._build_menu()
        self._update_title()
        self._refresh_windows()
        self._refresh_audio_devices()

        self._auto_detect_monitors()

        if self.ffmpeg_path:
            self._log(f"FFmpeg: {self.ffmpeg_path}")
        else:
            self._log("⚠  FFmpeg not found – showing setup assistant…")
            if not self._headless:
                self.after(600, self._show_ffmpeg_setup)
            else:
                self._log("❌  Cannot record: FFmpeg path not set. Exiting.")
                self.after(2000, self._on_close)
                return

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self._headless:
            # Guard: if all enabled screens have duration=0 in headless mode,
            # default them to 20 seconds so the app doesn't run forever.
            _HEADLESS_DEFAULT_DUR = 20
            screens = self.cfg.get("screens", {})
            if all(screens[n].get("duration", 0) == 0
                   for n in screens if screens[n].get("enabled", True)):
                self._log(f"⚠  Headless mode: all durations are 0 — defaulting to {_HEADLESS_DEFAULT_DUR}s per screen.")
                for n in screens:
                    if screens[n].get("enabled", True):
                        screens[n]["duration"] = _HEADLESS_DEFAULT_DUR
            self._log("🪄 Headless mode: auto-starting recording…")
            self.after(500, self._start_recording)

    # ── Styles ─────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".",               background=BG,       foreground=FG,     fieldbackground=FRAME_BG)
        s.configure("TFrame",          background=BG)
        s.configure("TLabel",          background=BG,       foreground=FG)
        s.configure("TCheckbutton",    background=BG,       foreground=FG)
        s.configure("TEntry",          fieldbackground=FRAME_BG, foreground=FG)
        s.configure("TCombobox",       fieldbackground=FRAME_BG, foreground=FG)
        s.configure("TLabelframe",     background=BG,       bordercolor=ACCENT)
        s.configure("TLabelframe.Label", background=BG,     foreground=ACCENT,
                    font=("Segoe UI", 9, "bold"))
        s.map("TCombobox", fieldbackground=[("readonly", FRAME_BG)])
        s.map("TCheckbutton", background=[("active", BG)])

    # ── UI Build ───────────────────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, bg=FRAME_BG, fg=ACCENT, font=("Segoe UI", 9), padx=8, pady=3, **kw):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief="flat", cursor="hand2",
                         font=font, padx=padx, pady=pady, **kw)

    def _build_ui(self):
        PAD = 10
        main = ttk.Frame(self, padding=PAD)
        main.pack(fill="both", expand=True)

        self._build_screen_section(main, PAD)
        self._build_audio_section(main, PAD)
        self._build_settings_section(main, PAD)
        self._build_window_section(main, PAD)
        self._build_controls(main, PAD)
        self._build_log(main)

    def _build_menu(self):
        menubar = tk.Menu(self, bg=FRAME_BG, fg=FG, activebackground=ACCENT,
                          activeforeground=BG, relief="flat", tearoff=False)
        file_menu = tk.Menu(menubar, bg=FRAME_BG, fg=FG, activebackground=ACCENT,
                            activeforeground=BG, relief="flat", tearoff=False)
        file_menu.add_command(label="New",         accelerator="Ctrl+N", command=self._cmd_new)
        file_menu.add_command(label="Open…",       accelerator="Ctrl+O", command=self._cmd_open)
        file_menu.add_separator()
        file_menu.add_command(label="Save",        accelerator="Ctrl+S", command=self._cmd_save)
        file_menu.add_command(label="Save As…",    accelerator="Ctrl+Shift+S", command=self._cmd_save_as)
        menubar.add_cascade(label="File", menu=file_menu)
        self.config(menu=menubar)
        self.bind_all("<Control-n>", lambda _: self._cmd_new())
        self.bind_all("<Control-o>", lambda _: self._cmd_open())
        self.bind_all("<Control-s>", lambda _: self._cmd_save())
        self.bind_all("<Control-S>", lambda _: self._cmd_save_as())

    def _update_title(self):
        if self._config_path:
            name = os.path.basename(self._config_path)
            self.title(f"🎯 Pinball Screen Recorder  —  {name}")
        else:
            self.title("🎯 Pinball Screen Recorder")

    def _apply_config(self, cfg, path=None):
        """Load cfg dict into all UI widgets and update state."""
        self.cfg = cfg
        self._config_path = path
        for name in SCREENS:
            v  = self.screen_vars[name]
            sc = cfg.get("screens", {}).get(name, {})
            v["enabled"].set(sc.get("enabled", True))
            v["monitor"].set(sc.get("monitor", ""))
            v["x"].set(str(sc.get("x", 0)))
            v["y"].set(str(sc.get("y", 0)))
            v["width"].set(str(sc.get("width", 1920)))
            v["height"].set(str(sc.get("height", 1080)))
            v["fps"].set(str(sc.get("fps", 30)))
            v["delay"].set(str(sc.get("delay", 0)))
            v["duration"].set(str(sc.get("duration", 0)))
        self.output_folder_var.set(cfg.get("output_folder", ""))
        self.prefix_var.set(cfg.get("file_prefix", "pinball"))
        self.ffmpeg_var.set(cfg.get("ffmpeg_path", "") or self.ffmpeg_path)
        self.audio_enabled.set(cfg.get("audio_enabled", True))
        self.audio_device_var.set(cfg.get("audio_device", ""))
        self.window_var.set(cfg.get("window_title", ""))
        self._update_title()
        self._log(f"Config loaded: {path or '(default)'}")

    def _cmd_new(self):
        """Reset all settings to defaults."""
        if self.recording:
            return
        if not messagebox.askyesno("New Config",
                                   "Discard current settings and load defaults?",
                                   parent=self):
            return
        self._apply_config(dict(DEFAULT_CONFIG), path=None)

    def _cmd_open(self):
        """Browse for a JSON config file and load it."""
        if self.recording:
            return
        path = filedialog.askopenfilename(
            title="Open Config",
            initialdir=_APP_DIR,
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            with open(path) as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
        except Exception as e:
            messagebox.showerror("Open Failed", str(e), parent=self)
            return
        self._apply_config(cfg, path=path)

    def _cmd_save(self):
        """Save to the current file, or fall through to Save As."""
        if self._config_path:
            try:
                with open(self._config_path, "w") as f:
                    json.dump(self._snapshot_config(), f, indent=2)
                self._log(f"Saved: {self._config_path}")
            except Exception as e:
                messagebox.showerror("Save Failed", str(e), parent=self)
        else:
            self._cmd_save_as()

    def _cmd_save_as(self):
        """Save to a new file chosen by the user."""
        path = filedialog.asksaveasfilename(
            title="Save Config As",
            initialdir=_APP_DIR,
            defaultextension=".json",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            with open(path, "w") as f:
                json.dump(self._snapshot_config(), f, indent=2)
            self._config_path = path
            self._update_title()
            self._log(f"Saved as: {path}")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e), parent=self)

    def _build_screen_section(self, parent, pad):
        frame = ttk.LabelFrame(parent, text="  Screen Configuration  ", padding=pad)
        frame.pack(fill="x", pady=(0, pad))

        headers = ["Screen", "On", "Monitor", "X", "Y", "Width", "Height", "FPS", "Delay(s)", "Duration(s)", ""]
        for col, h in enumerate(headers):
            ttk.Label(frame, text=h, foreground=ACCENT,
                      font=("Segoe UI", 9, "bold")).grid(
                row=0, column=col, padx=4, pady=(0, 4), sticky="w")

        self.screen_vars = {}
        for row, name in enumerate(SCREENS, start=1):
            s = self.cfg["screens"].get(name, DEFAULT_CONFIG["screens"][name])
            ev  = tk.BooleanVar(value=s.get("enabled", True))
            mv  = tk.StringVar(value=s.get("monitor", ""))
            xv  = tk.StringVar(value=str(s.get("x", 0)))
            yv  = tk.StringVar(value=str(s.get("y", 0)))
            wv  = tk.StringVar(value=str(s.get("width", 1920)))
            hv  = tk.StringVar(value=str(s.get("height", 1080)))
            fpv = tk.StringVar(value=str(s.get("fps", 30)))
            dly = tk.StringVar(value=str(s.get("delay", 5)))
            dur = tk.StringVar(value=str(s.get("duration", 0)))
            self.screen_vars[name] = {"enabled": ev, "monitor": mv,
                                      "x": xv, "y": yv, "width": wv, "height": hv,
                                      "fps": fpv, "delay": dly, "duration": dur}

            for _v in (ev, mv, xv, yv, wv, hv, fpv, dly, dur):
                _v.trace_add("write", self._schedule_save)
            for _v in (xv, yv, wv, hv):
                _v.trace_add("write", lambda *_, n=name: self._sync_overlay(n))

            ttk.Label(frame, text=name, foreground=SCREEN_COLOR[name],
                      font=("Segoe UI", 9, "bold")).grid(row=row, column=0, padx=4, pady=2, sticky="w")
            ttk.Checkbutton(frame, variable=ev).grid(row=row, column=1, padx=4)

            mon_combo = ttk.Combobox(frame, textvariable=mv, width=11, state="readonly")
            mon_combo.grid(row=row, column=2, padx=4, pady=2)
            mon_combo.bind("<<ComboboxSelected>>", lambda e, n=name: self._on_monitor_selected(n))
            self.screen_vars[name]["_mon_combo"] = mon_combo

            for col, var in enumerate([xv, yv, wv, hv], start=3):
                ttk.Entry(frame, textvariable=var, width=7).grid(row=row, column=col, padx=4, pady=2)

            ttk.Entry(frame, textvariable=fpv, width=4).grid(row=row, column=7, padx=4, pady=2)
            ttk.Entry(frame, textvariable=dly, width=5).grid(row=row, column=8, padx=4, pady=2)
            ttk.Entry(frame, textvariable=dur, width=6).grid(row=row, column=9, padx=4, pady=2)

            self._btn(frame, "🖥 Preview", lambda n=name: self._show_preview_overlay(n),
                      font=("Segoe UI", 8), padx=5, pady=2).grid(row=row, column=10, padx=(6, 0), pady=2)

        self._btn(frame, "🔍  Auto-Detect Monitors", lambda: self._auto_detect_monitors(force_assign=True)).grid(
            row=len(SCREENS) + 1, column=0, columnspan=11, pady=(8, 0), sticky="w")

    def _build_audio_section(self, parent, pad):
        frame = ttk.LabelFrame(parent, text="  Audio  ", padding=pad)
        frame.pack(fill="x", pady=(0, pad))

        self.audio_enabled = tk.BooleanVar(value=self.cfg.get("audio_enabled", True))
        ttk.Checkbutton(frame, text="Record system audio output to a separate MP3",
                         variable=self.audio_enabled).grid(
            row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(frame, text="Capture Device:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.audio_device_var = tk.StringVar(value=self.cfg.get("audio_device", ""))
        self.audio_combo = ttk.Combobox(frame, textvariable=self.audio_device_var,
                                         width=44, state="readonly")
        self.audio_combo.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self._btn(frame, "↻", self._refresh_audio_devices,
                  font=("Segoe UI", 12)).grid(row=1, column=2, padx=(4, 0), pady=(6, 0))

        self.audio_enabled.trace_add("write", self._schedule_save)
        self.audio_device_var.trace_add("write", self._schedule_save)

    def _build_settings_section(self, parent, pad):
        frame = ttk.LabelFrame(parent, text="  Recording Settings  ", padding=pad)
        frame.pack(fill="x", pady=(0, pad))
        frame.columnconfigure(1, weight=1)

        # Output folder
        ttk.Label(frame, text="Output Folder:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.output_folder_var = tk.StringVar(value=self.cfg.get("output_folder", ""))
        ttk.Entry(frame, textvariable=self.output_folder_var, width=42).grid(
            row=0, column=1, sticky="ew")
        self._btn(frame, "…", self._browse_folder, fg=FG, padx=6).grid(
            row=0, column=2, padx=(4, 0))

        # File prefix
        ttk.Label(frame, text="File Prefix:").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.prefix_var = tk.StringVar(value=self.cfg.get("file_prefix", "pinball"))
        ttk.Entry(frame, textvariable=self.prefix_var, width=22).grid(
            row=1, column=1, sticky="w", pady=(6, 0))

        # FFmpeg path
        ttk.Label(frame, text="FFmpeg Path:").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.ffmpeg_var = tk.StringVar(value=self.ffmpeg_path)
        ttk.Entry(frame, textvariable=self.ffmpeg_var, width=42).grid(
            row=2, column=1, sticky="ew", pady=(6, 0))
        ffmpeg_btns = ttk.Frame(frame)
        ffmpeg_btns.grid(row=2, column=2, padx=(4, 0), pady=(6, 0))
        self._btn(ffmpeg_btns, "…", self._browse_ffmpeg, fg=FG, padx=6).pack(side="left", padx=(0, 2))
        self._btn(ffmpeg_btns, "🔍", self._auto_detect_ffmpeg, fg=ACCENT, padx=6).pack(side="left", padx=(0, 2))
        self._btn(ffmpeg_btns, "⚙ Setup", self._show_ffmpeg_setup, fg=ACCENT, padx=6).pack(side="left")

        for _v in (self.output_folder_var, self.prefix_var, self.ffmpeg_var):
            _v.trace_add("write", self._schedule_save)

    def _build_window_section(self, parent, pad):
        frame = ttk.LabelFrame(parent, text="  Window Focus  ", padding=pad)
        frame.pack(fill="x", pady=(0, pad))

        ttk.Label(frame, text="Focus window before recording:").pack(side="left", padx=(0, 8))
        self.window_var = tk.StringVar(value=self.cfg.get("window_title", ""))
        self.window_combo = ttk.Combobox(frame, textvariable=self.window_var, width=36)
        self.window_combo.pack(side="left", padx=(0, 8))
        self._btn(frame, "🔄 Refresh", self._refresh_windows).pack(side="left")
        self.window_var.trace_add("write", self._schedule_save)

    def _build_controls(self, parent, pad):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(0, pad))

        self.start_btn = tk.Button(
            frame, text="⏺  START RECORDING", command=self._start_recording,
            bg=BTN_GREEN, fg="#1e1e2e", font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2", padx=20, pady=8)
        self.start_btn.pack(side="left", padx=(0, 10))

        self.stop_btn = tk.Button(
            frame, text="⏹  STOP", command=self._stop_recording,
            bg=BTN_RED, fg="#1e1e2e", font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2", padx=20, pady=8, state="disabled")
        self.stop_btn.pack(side="left")

        self.status_var = tk.StringVar(value="Ready")
        self.status_lbl = tk.Label(frame, textvariable=self.status_var, bg=BG,
                                    fg=ACCENT, font=("Segoe UI", 10, "bold"))
        self.status_lbl.pack(side="left", padx=(20, 0))

    def _build_log(self, parent):
        frame = ttk.LabelFrame(parent, text="  Log  ", padding=4)
        frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            frame, height=9, bg=FRAME_BG, fg=FG,
            font=("Consolas", 9), relief="flat",
            state="disabled", wrap="word")
        sb = ttk.Scrollbar(frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, msg, color=ACCENT):
        self.status_var.set(msg)
        self.status_lbl.configure(fg=color)

    def _schedule_save(self, *_):
        """Debounced auto-save: waits 600 ms of inactivity before writing."""
        if hasattr(self, "_save_job") and self._save_job:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.after(600, self._do_auto_save)

    def _do_auto_save(self):
        self._save_job = None
        try:
            save_config(self._snapshot_config())
        except Exception:
            pass

    def _sync_overlay(self, name):
        """Reposition an open preview overlay when the field values change."""
        ov = self._overlays.get(name)
        if not ov:
            return
        v = self.screen_vars[name]
        try:
            x = int(v["x"].get() or 0)
            y = int(v["y"].get() or 0)
            w = max(1, int(v["width"].get() or 1))
            h = max(1, int(v["height"].get() or 1))
            ov.geometry(f"{w}x{h}+{x}+{y}")
        except (ValueError, tk.TclError):
            pass

    def _auto_detect_monitors(self, force_assign=False):
        self._monitors = enum_display_monitors()
        self._log(f"Detected {len(self._monitors)} monitor(s):")
        for i, m in enumerate(self._monitors):
            tag = " [PRIMARY]" if m["primary"] else ""
            self._log(f"  Monitor {i + 1}: {m['width']}×{m['height']}  @  ({m['x']}, {m['y']}){tag}")

        mon_keys = [f"Monitor {i+1}" for i in range(len(self._monitors))]

        for name in SCREENS:
            combo = self.screen_vars[name].get("_mon_combo")
            if combo is not None:
                combo["values"] = mon_keys

        # Only overwrite fields when explicitly requested (button click) or
        # when no saved config exists for a screen (first-run defaults).
        saved_screens = self.cfg.get("screens", {})
        for idx, name in enumerate(SCREENS):
            if idx >= len(self._monitors):
                continue
            mon = self._monitors[idx]
            v   = self.screen_vars[name]
            has_saved = name in saved_screens
            if force_assign or not has_saved:
                v["x"].set(str(mon["x"]))
                v["y"].set(str(mon["y"]))
                v["width"].set(str(mon["width"]))
                v["height"].set(str(mon["height"]))
                v["monitor"].set(f"Monitor {idx + 1}")
                self._log(f"  → {name} assigned to Monitor {idx + 1}")
            else:
                # Just update the monitor label to match saved coordinates if possible
                for i, m in enumerate(self._monitors):
                    if (m["x"] == int(v["x"].get() or 0) and
                            m["y"] == int(v["y"].get() or 0)):
                        v["monitor"].set(f"Monitor {i + 1}")
                        break

    def _on_monitor_selected(self, name):
        """Auto-fill X/Y/W/H when user picks a monitor from the dropdown."""
        v   = self.screen_vars[name]
        sel = v["monitor"].get()          # e.g. "Monitor 2"
        try:
            idx = int(sel.split()[1]) - 1
            mon = self._monitors[idx]
            v["x"].set(str(mon["x"]))
            v["y"].set(str(mon["y"]))
            v["width"].set(str(mon["width"]))
            v["height"].set(str(mon["height"]))
        except (IndexError, ValueError):
            pass

    # ── Preview Overlay ────────────────────────────────────────────────────────

    def _get_monitor_for_overlay(self, win):
        """Return the monitor that has the most overlap with the given Toplevel."""
        win.update_idletasks()
        ox, oy = win.winfo_x(), win.winfo_y()
        ow, oh = win.winfo_width(), win.winfo_height()
        best, best_area = None, 0
        for mon in self._monitors:
            ix = max(0, min(ox + ow, mon["x"] + mon["width"])  - max(ox, mon["x"]))
            iy = max(0, min(oy + oh, mon["y"] + mon["height"]) - max(oy, mon["y"]))
            area = ix * iy
            if area > best_area:
                best_area, best = area, mon
        return best or (self._monitors[0] if self._monitors else None)

    def _show_preview_overlay(self, name):
        """Open a transparent, borderless, draggable/resizable overlay to set the capture region."""
        v = self.screen_vars[name]
        try:
            x = int(v["x"].get() or 0)
            y = int(v["y"].get() or 0)
            w = int(v["width"].get()  or 800)
            h = int(v["height"].get() or 600)
        except ValueError:
            x, y, w, h = 0, 0, 800, 600

        color  = SCREEN_COLOR.get(name, ACCENT)
        DARK   = "#1e1e2e"
        MID    = "#2a2a3e"          # slightly lighter dark for button bar
        BORDER = 3                  # coloured border width
        HDR_H  = 28                 # header height
        BTN_H  = 30                 # snap-button bar height
        BOT_H  = 34                 # bottom bar height
        EDGE   = 6                  # resize-handle thickness
        MINW, MINH = 200, HDR_H + BTN_H + BOT_H + 20

        # Close any existing overlay for this screen first
        old_ov = self._overlays.get(name)
        if old_ov:
            try:
                old_ov.destroy()
            except Exception:
                pass

        ov = tk.Toplevel(self)
        self._overlays[name] = ov
        ov.protocol("WM_DELETE_WINDOW", lambda: self._close_overlay(name))
        ov.overrideredirect(True)
        ov.geometry(f"{w}x{h}+{x}+{y}")
        ov.wm_attributes("-alpha", 0.88)
        ov.wm_attributes("-topmost", True)
        ov.configure(bg=color)

        # ── Drag / resize state ────────────────────────────────────────────────
        state = {"drag_x": 0, "drag_y": 0,
                 "res_x": 0,  "res_y": 0,
                 "res_w": 0,  "res_h": 0,
                 "ox": 0,     "oy": 0}

        def _drag_start(e):
            state["drag_x"] = e.x_root - ov.winfo_x()
            state["drag_y"] = e.y_root - ov.winfo_y()

        def _drag_move(e):
            ov.geometry(f"+{e.x_root - state['drag_x']}+{e.y_root - state['drag_y']}")
            _update_dims()

        def _res_start(e):
            state["res_x"] = e.x_root
            state["res_y"] = e.y_root
            state["res_w"] = ov.winfo_width()
            state["res_h"] = ov.winfo_height()
            state["ox"]    = ov.winfo_x()
            state["oy"]    = ov.winfo_y()

        def _make_res_move(dx_o, dx_w, dy_o, dy_h):
            def _move(e):
                dx = e.x_root - state["res_x"]
                dy = e.y_root - state["res_y"]
                nw = max(MINW, state["res_w"] + dx * dx_w)
                nh = max(MINH, state["res_h"] + dy * dy_h)
                nx = state["ox"] + dx * dx_o
                ny = state["oy"] + dy * dy_o
                ov.geometry(f"{int(nw)}x{int(nh)}+{int(nx)}+{int(ny)}")
                _update_dims()
            return _move

        # ── Resize-handle strips ───────────────────────────────────────────────
        def _edge(cursor, dx_o, dx_w, dy_o, dy_h, **kw):
            f = tk.Frame(ov, bg=color, cursor=cursor)
            f.place(**kw)
            f.bind("<ButtonPress-1>", _res_start)
            f.bind("<B1-Motion>",     _make_res_move(dx_o, dx_w, dy_o, dy_h))

        _edge("sb_v_double_arrow", 0, 0, 1,-1, relx=0,   rely=0,   relwidth=1,  height=EDGE)
        _edge("sb_v_double_arrow", 0, 0, 0, 1, relx=0,   rely=1.0, relwidth=1,  height=EDGE, anchor="sw")
        _edge("sb_h_double_arrow", 1,-1, 0, 0, relx=0,   rely=0,   width=EDGE,  relheight=1)
        _edge("sb_h_double_arrow", 0, 1, 0, 0, relx=1.0, rely=0,   width=EDGE,  relheight=1, anchor="ne")
        _edge("size_nw_se",        1,-1, 1,-1, relx=0,   rely=0,   width=EDGE*2, height=EDGE*2)
        _edge("size_ne_sw",        0, 1, 1,-1, relx=1.0, rely=0,   width=EDGE*2, height=EDGE*2, anchor="ne")
        _edge("size_ne_sw",        1,-1, 0, 1, relx=0,   rely=1.0, width=EDGE*2, height=EDGE*2, anchor="sw")
        _edge("size_nw_se",        0, 1, 0, 1, relx=1.0, rely=1.0, width=EDGE*2, height=EDGE*2, anchor="se")

        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(ov, bg=DARK, cursor="fleur")
        header.place(x=EDGE, y=EDGE, relwidth=1, width=-(EDGE*2), height=HDR_H)
        header.bind("<ButtonPress-1>", _drag_start)
        header.bind("<B1-Motion>",     _drag_move)

        # coloured left accent bar inside header
        tk.Frame(header, bg=color, width=4).pack(side="left", fill="y", padx=(0,0))
        tk.Label(header, text=f"  {name}", bg=DARK, fg=color,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(header, text="drag to move", bg=DARK, fg="#585b70",
                 font=("Segoe UI", 8)).pack(side="left", padx=8)

        # ── Snap-button bar ────────────────────────────────────────────────────
        btn_bar = tk.Frame(ov, bg=MID)
        btn_bar.place(x=EDGE, y=EDGE+HDR_H, relwidth=1, width=-(EDGE*2), height=BTN_H)

        # matching 4px accent spacer so buttons align with header text
        tk.Frame(btn_bar, bg=color, width=4).pack(side="left", fill="y")

        def _snap_full_screen():
            mon = self._get_monitor_for_overlay(ov)
            if mon:
                ov.geometry(f"{mon['width']}x{mon['height']}+{mon['x']}+{mon['y']}")
                _update_dims()

        def _snap_full_width():
            mon = self._get_monitor_for_overlay(ov)
            if mon:
                ov.geometry(f"{mon['width']}x{ov.winfo_height()}+{mon['x']}+{ov.winfo_y()}")
                _update_dims()

        def _snap_full_height():
            mon = self._get_monitor_for_overlay(ov)
            if mon:
                ov.geometry(f"{ov.winfo_width()}x{mon['height']}+{ov.winfo_x()}+{mon['y']}")
                _update_dims()

        def _snap_top_half():
            mon = self._get_monitor_for_overlay(ov)
            if mon:
                ov.geometry(f"{mon['width']}x{mon['height']//2}+{mon['x']}+{mon['y']}")
                _update_dims()

        def _snap_bottom_half():
            mon = self._get_monitor_for_overlay(ov)
            if mon:
                half = mon["height"] // 2
                ov.geometry(f"{mon['width']}x{half}+{mon['x']}+{mon['y'] + half}")
                _update_dims()

        for label, cmd in [
            ("⛶ Full Screen", _snap_full_screen),
            ("↔ Full Width",  _snap_full_width),
            ("↕ Full Height", _snap_full_height),
            ("▀ Top Half",    _snap_top_half),
            ("▄ Bottom Half", _snap_bottom_half),
        ]:
            tk.Button(btn_bar, text=label, command=cmd,
                      bg=MID, fg=color, relief="flat",
                      activebackground=FRAME_BG, activeforeground=color,
                      font=("Segoe UI", 8), padx=6, pady=4,
                      cursor="hand2").pack(side="left", padx=1)

        # ── Centre info label (live dimensions) ───────────────────────────────
        dims_var = tk.StringVar()
        dims_lbl = tk.Label(ov, textvariable=dims_var, bg=color, fg=DARK,
                            font=("Segoe UI", 11, "bold"))
        dims_lbl.place(relx=0.5, rely=0.5, anchor="center")

        def _update_dims():
            ov.update_idletasks()
            dims_var.set(f"{ov.winfo_width()} × {ov.winfo_height()}\n"
                         f"@ {ov.winfo_x()}, {ov.winfo_y()}")

        _update_dims()

        # ── Bottom bar ────────────────────────────────────────────────────────
        def _apply():
            ov.update_idletasks()
            v["x"].set(str(ov.winfo_x()))
            v["y"].set(str(ov.winfo_y()))
            v["width"].set(str(ov.winfo_width()))
            v["height"].set(str(ov.winfo_height()))
            mon = self._get_monitor_for_overlay(ov)
            if mon and mon in self._monitors:
                v["monitor"].set(f"Monitor {self._monitors.index(mon) + 1}")
            self._close_overlay(name)
            self._log(f"Preview applied \u2192 {name}: "
                      f"{v['width'].get()}\u00d7{v['height'].get()} @ ({v['x'].get()}, {v['y'].get()})")

        bot = tk.Frame(ov, bg=DARK)
        bot.place(x=EDGE, rely=1.0, y=-(EDGE+BOT_H), relwidth=1,
                  width=-(EDGE*2), height=BOT_H, anchor="sw")

        # thin colour separator line at top of bottom bar
        tk.Frame(bot, bg=color, height=2).pack(fill="x", side="top")

        btn_row = tk.Frame(bot, bg=DARK)
        btn_row.pack(fill="both", expand=True)
        tk.Button(btn_row, text="✓  Apply", command=_apply,
                  bg=DARK, fg="#a6e3a1", relief="flat",
                  activebackground=FRAME_BG, activeforeground="#a6e3a1",
                  font=("Segoe UI", 9, "bold"), padx=12, pady=4,
                  cursor="hand2").pack(side="left", padx=(8, 4), pady=4)
        tk.Button(btn_row, text="✗  Cancel", command=lambda: self._close_overlay(name),
                  bg=DARK, fg="#f38ba8", relief="flat",
                  activebackground=FRAME_BG, activeforeground="#f38ba8",
                  font=("Segoe UI", 9, "bold"), padx=12, pady=4,
                  cursor="hand2").pack(side="left", padx=4, pady=4)

    def _close_overlay(self, name):
        """Destroy a named preview overlay and remove it from the tracking dict."""
        ov = self._overlays.pop(name, None)
        if ov:
            try:
                ov.destroy()
            except Exception:
                pass

    # ── FFmpeg Setup Assistant ────────────────────────────────────────────────

    def _show_ffmpeg_setup(self):
        """Show a friendly dialog to help the user get FFmpeg installed/located."""
        DARK, MID = "#1e1e2e", "#313244"
        BLUE = "#89b4fa"

        dlg = tk.Toplevel(self)
        dlg.title("FFmpeg Setup")
        dlg.configure(bg=DARK)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.wm_attributes("-topmost", True)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(dlg, bg=MID)
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=BLUE, width=4).pack(side="left", fill="y")
        tk.Label(hdr, text="  FFmpeg Setup Assistant", bg=MID, fg=BLUE,
                 font=("Segoe UI", 12, "bold")).pack(side="left", pady=12)

        # ── Body ──────────────────────────────────────────────────────────────
        body = tk.Frame(dlg, bg=DARK, padx=20, pady=12)
        body.pack(fill="both", expand=True)

        tk.Label(body,
                 text=("FFmpeg is required to record video.\n"
                       "It is free, open-source software used by many applications.\n"
                       "System audio loopback capture is handled automatically — no special FFmpeg build needed.\n"
                       "Choose one of the options below to get started:"),
                 bg=DARK, fg=FG, font=("Segoe UI", 9),
                 justify="left", wraplength=430).pack(anchor="w", pady=(0, 14))

        status_var = tk.StringVar(value="")
        status_lbl = tk.Label(body, textvariable=status_var, bg=DARK,
                              fg=WARN, font=("Segoe UI", 8), wraplength=430, justify="left")
        status_lbl.pack(anchor="w", pady=(0, 2))

        progress = ttk.Progressbar(body, mode="indeterminate", length=430)
        # not packed yet — shown only during install

        def _set_status(msg, color=WARN):
            status_var.set(msg)
            status_lbl.configure(fg=color)

        def _after_found(path):
            self.ffmpeg_path = path
            self.ffmpeg_var.set(path)
            self._log(f"FFmpeg configured: {path}")
            _set_status(f"✓  FFmpeg found: {path}", BTN_GREEN)
            progress.stop()
            progress.pack_forget()
            dlg.after(1200, dlg.destroy)

        # Option 1 – winget
        def _install_winget():
            _set_status("Installing via winget… this may take a minute.", WARN)
            progress.pack(anchor="w", pady=(0, 6))
            progress.start(12)
            dlg.update_idletasks()
            def _run():
                try:
                    subprocess.run(
                        ["winget", "install", "--id", "Gyan.FFmpeg",
                         "--silent", "--accept-package-agreements",
                         "--accept-source-agreements"],
                        capture_output=True, text=True, timeout=180,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    found = find_ffmpeg()
                    if found:
                        dlg.after(0, _after_found, found)
                    else:
                        def _fail():
                            progress.stop()
                            progress.pack_forget()
                            _set_status(
                                "⚠  winget finished but ffmpeg.exe could not be located.\n"
                                "Try clicking Browse to find it, or restart the app if it was just added to PATH.",
                                BTN_RED)
                        dlg.after(0, _fail)
                except FileNotFoundError:
                    def _no_winget():
                        progress.stop()
                        progress.pack_forget()
                        _set_status(
                            "winget is not available on this system.\n"
                            "Try the Download option or Browse to an existing ffmpeg.exe.",
                            BTN_RED)
                    dlg.after(0, _no_winget)
                except subprocess.TimeoutExpired:
                    def _timeout():
                        progress.stop()
                        progress.pack_forget()
                        _set_status("winget timed out. Try again or use Browse.", BTN_RED)
                    dlg.after(0, _timeout)
            threading.Thread(target=_run, daemon=True).start()

        # Option 2 – auto-download portable ffmpeg.exe (BtbN build)
        _BTBN_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
                     "latest/ffmpeg-master-latest-win64-gpl.zip")
        _BTBN_INNER = "ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe"

        def _download_btbn():
            _set_status("Downloading FFmpeg (~60 MB) to app folder…", WARN)
            progress.pack(anchor="w", pady=(0, 6))
            progress.start(12)
            dlg.update_idletasks()
            def _run():
                try:
                    dest_dir = _APP_DIR
                    dest_exe = os.path.join(dest_dir, "ffmpeg.exe")
                    zip_path = os.path.join(dest_dir, "_ffmpeg_download.zip")
                    try:
                        def _reporthook(count, block, total):
                            if total > 0:
                                pct = int(count * block * 100 / total)
                                dlg.after(0, _set_status,
                                          f"Downloading… {min(pct,100)}%", WARN)
                        urllib.request.urlretrieve(_BTBN_URL, zip_path, _reporthook)
                        dlg.after(0, _set_status, "Extracting ffmpeg.exe…", WARN)
                        with zipfile.ZipFile(zip_path) as zf:
                            with zf.open(_BTBN_INNER) as src, open(dest_exe, "wb") as dst:
                                dst.write(src.read())
                    finally:
                        try:
                            os.remove(zip_path)
                        except Exception:
                            pass
                    dlg.after(0, _after_found, dest_exe)
                except Exception as exc:
                    def _fail(e=exc):
                        progress.stop(); progress.pack_forget()
                        _set_status(f"Download failed: {e}\nTry the Browse option instead.",
                                    BTN_RED)
                    dlg.after(0, _fail)
            threading.Thread(target=_run, daemon=True).start()

        # Option 3 – open download page
        def _open_download():
            webbrowser.open("https://www.gyan.dev/ffmpeg/builds/")
            _set_status(
                "Download page opened in your browser.\n"
                "Download the 'release essentials' build, extract ffmpeg.exe,\n"
                "then use 'Browse' below to point the app to it.", ACCENT)

        # Option 3 – browse
        def _browse():
            p = filedialog.askopenfilename(
                parent=dlg,
                title="Locate ffmpeg.exe",
                filetypes=[("FFmpeg executable", "ffmpeg.exe"), ("All files", "*.*")])
            if p:
                self.ffmpeg_var.set(p)
                _after_found(p)

        # Option 4 – auto-detect
        def _detect():
            found = find_ffmpeg()
            if found:
                _after_found(found)
            else:
                _set_status("Auto-detect found nothing. Try the other options below.", BTN_RED)

        btn_cfg = dict(bg=MID, fg=ACCENT, relief="flat", cursor="hand2",
                       font=("Segoe UI", 9), activebackground=FRAME_BG,
                       activeforeground=ACCENT, anchor="w", padx=12, pady=7)

        options = [
            ("🪄  Auto-Install via winget  (installs to system PATH)",
             "Silently installs FFmpeg using the Windows Package Manager.",
             _install_winget),
            ("⬇  Download ffmpeg.exe  (portable — saves next to this app)",
             "Downloads ffmpeg.exe directly to the app folder (~60 MB). Ideal for portable/USB use or easy cleanup.",
             _download_btbn),
            ("🌐  Open Download Page  (gyan.dev builds)",
             "Opens the FFmpeg download page in your browser.",
             _open_download),
            ("📂  Browse for ffmpeg.exe",
             "Locate an existing ffmpeg.exe you already have on disk.",
             _browse),
            ("🔍  Re-run Auto-Detect",
             "Searches common install paths and your system PATH again.",
             _detect),
        ]

        for title, desc, cmd in options:
            row = tk.Frame(body, bg=MID, pady=0)
            row.pack(fill="x", pady=3)
            tk.Frame(row, bg=ACCENT, width=3).pack(side="left", fill="y")
            inner = tk.Frame(row, bg=MID)
            inner.pack(side="left", fill="x", expand=True)
            tk.Button(inner, text=title, command=cmd, **btn_cfg).pack(fill="x")
            tk.Label(inner, text=f"  {desc}", bg=MID, fg="#585b70",
                     font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=12, pady=(0, 6))

        # ── Footer ────────────────────────────────────────────────────────────
        foot = tk.Frame(dlg, bg=MID)
        foot.pack(fill="x", side="bottom")
        tk.Frame(foot, bg=ACCENT, height=2).pack(fill="x")
        tk.Button(foot, text="Close", command=dlg.destroy,
                  bg=MID, fg=FG, relief="flat", cursor="hand2",
                  font=("Segoe UI", 9), padx=14, pady=6,
                  activebackground=FRAME_BG).pack(side="right", padx=8, pady=6)

        # size to content and centre over parent
        dlg.update_idletasks()
        dw = max(dlg.winfo_reqwidth(), 480)
        dh = dlg.winfo_reqheight()
        cx = self.winfo_x() + (self.winfo_width()  - dw) // 2
        cy = self.winfo_y() + (self.winfo_height() - dh) // 2
        dlg.geometry(f"{dw}x{dh}+{cx}+{cy}")

    # ── FFmpeg Auto-Detect ─────────────────────────────────────────────────────

    def _auto_detect_ffmpeg(self):
        path = find_ffmpeg()
        if path:
            self.ffmpeg_var.set(path)
            self.ffmpeg_path = path
            self._log(f"FFmpeg auto-detected: {path}")
        else:
            self._log("⚠  FFmpeg not found – please set the path manually.")
            messagebox.showwarning("FFmpeg Not Found",
                "Could not auto-detect FFmpeg.\n"
                "Please browse to ffmpeg.exe manually or add it to your system PATH.")

    def _refresh_audio_devices(self):
        ffmpeg = self.ffmpeg_var.get() if hasattr(self, "ffmpeg_var") else self.ffmpeg_path

        # Loopback devices via pyaudiowpatch (WASAPI loopback — no FFmpeg WASAPI needed)
        loopback_entries = get_pyaudio_loopback_devices()  # list of (label, idx, ch, rate)
        # Store metadata for use at record time
        self._loopback_meta = {label: (idx, ch, rate)
                               for label, idx, ch, rate in loopback_entries}
        loopback_labels = [label for label, *_ in loopback_entries]

        # dshow fallback devices (Stereo Mix, microphones)
        dshow_devices = get_audio_devices(ffmpeg) if ffmpeg else []

        devices = loopback_labels + dshow_devices

        if not devices:
            self.audio_combo["values"] = ["(no audio input devices found)"]
            self.audio_device_var.set("(no audio input devices found)")
            self._log("⚠  No audio input devices found.")
            return

        self.audio_combo["values"] = devices
        current = self.audio_device_var.get()
        if not current or current not in devices:
            self.audio_device_var.set(devices[0])

        if loopback_labels:
            self._log(f"✓ WASAPI loopback available — captures all system audio automatically")
        else:
            self._log("⚠  No loopback devices found — falling back to dshow (Stereo Mix).")
        self._log(f"Found {len(devices)} audio device(s):")
        for d in devices:
            self._log(f"  • {d}")

    def _refresh_windows(self):
        windows = enum_windows()
        self._win_map = {title: hwnd for hwnd, title in windows}
        titles = [t for t in self._win_map if t.strip()]
        self.window_combo["values"] = titles
        self._log(f"Found {len(titles)} open window(s)")

    def _browse_folder(self):
        d = filedialog.askdirectory(initialdir=self.output_folder_var.get())
        if d:
            self.output_folder_var.set(d)

    def _browse_ffmpeg(self):
        p = filedialog.askopenfilename(
            title="Select ffmpeg.exe",
            filetypes=[("FFmpeg executable", "ffmpeg.exe"), ("All files", "*.*")])
        if p:
            self.ffmpeg_var.set(p)
            self.ffmpeg_path = p

    # ── Config snapshot ──────────────────────────────────────────────────

    @staticmethod
    def _ffmpeg_popen(cmd, **kw):
        """Launch FFmpeg with a hidden console window.
        CREATE_NEW_CONSOLE gives it a real console (needed for CTRL_BREAK delivery
        and for dshow callbacks to function). SW_HIDE keeps it invisible.
        stderr is always piped; callers must drain it to avoid pipe-buffer deadlock."""
        si = subprocess.STARTUPINFO()
        si.dwFlags     = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0   # SW_HIDE
        return subprocess.Popen(
            cmd,
            startupinfo=si,
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
            **kw,
        )

    def _snapshot_config(self):
        screens = {}
        for name in SCREENS:
            v = self.screen_vars[name]
            screens[name] = {
                "enabled":  v["enabled"].get(),
                "monitor":  v["monitor"].get(),
                "x":        int(v["x"].get()        or 0),
                "y":        int(v["y"].get()        or 0),
                "width":    int(v["width"].get()    or 1920),
                "height":   int(v["height"].get()   or 1080),
                "fps":      int(v["fps"].get()      or 30),
                "delay":    int(v["delay"].get()    or 0),
                "duration": int(v["duration"].get() or 0),
            }
        return {
            "output_folder": self.output_folder_var.get(),
            "file_prefix":   self.prefix_var.get(),
            "ffmpeg_path":   self.ffmpeg_var.get(),
            "audio_enabled": self.audio_enabled.get(),
            "audio_device":  self.audio_device_var.get(),
            "window_title":  self.window_var.get(),
            "screens":       screens,
        }

    # ── Recording ──────────────────────────────────────────────────────────────

    def _start_recording(self):
        self.ffmpeg_path = self.ffmpeg_var.get()
        if not self.ffmpeg_path:
            self._show_ffmpeg_setup()
            return

        # Close any open preview overlays before recording
        for n in list(self._overlays.keys()):
            self._close_overlay(n)

        cfg = self._snapshot_config()
        save_config(cfg)
        os.makedirs(cfg["output_folder"], exist_ok=True)

        self.recording = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        threading.Thread(target=self._countdown_and_record, args=(cfg,), daemon=True).start()

    def _countdown_and_record(self, cfg):
        # Focus the selected window
        title = cfg.get("window_title", "").strip()
        if title and title in self._win_map:
            self._log(f"Focusing: {title}")
            try:
                focus_window(self._win_map[title])
                time.sleep(0.5)
            except Exception as e:
                self._log(f"  Could not focus window: {e}")

        if not self.recording:
            return

        self.after(0, self._set_status, "🔴  Recording…", BTN_RED)
        self._launch_ffmpeg(cfg)

    def _launch_ffmpeg(self, cfg):
        ffmpeg    = self.ffmpeg_path
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix    = cfg["file_prefix"]
        out_dir   = cfg["output_folder"]

        self.processes = []

        # ── Video streams ──────────────────────────────────────────────────────
        for name in SCREENS:
            s = cfg["screens"][name]
            if not s["enabled"]:
                continue

            out_file = os.path.join(out_dir, f"{prefix}_{name}_{ts}.mp4")
            cmd = [
                ffmpeg, "-y",
                "-f",          "gdigrab",
                "-framerate",  "30",
                "-offset_x",   str(s["x"]),
                "-offset_y",   str(s["y"]),
                "-video_size", f"{s['width']}x{s['height']}",
                "-draw_mouse", "0",
                "-i",          "desktop",
            ]
            if duration > 0:
                cmd += ["-t", str(duration)]
            cmd += [
                "-c:v",      "libx264",
                "-preset",   "ultrafast",
                "-crf",      "18",
                "-pix_fmt",  "yuv420p",
                "-vf",       "crop=trunc(iw/2)*2:trunc(ih/2)*2",
                out_file,
            ]

            self._log(f"▶ {name}  →  {os.path.basename(out_file)}")
            try:
                proc = self._ffmpeg_popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                # Drain stderr in background so the pipe buffer never fills up
                import collections
                err_buf = collections.deque(maxlen=120)
                def _drain(p=proc, b=err_buf):
                    try:
                        for raw in p.stderr:
                            b.append(raw.decode(errors="replace").rstrip())
                    except Exception:
                        pass
                threading.Thread(target=_drain, daemon=True).start()
                self.processes.append({"name": name, "proc": proc,
                                       "file": out_file, "err_buf": err_buf})
            except Exception as e:
                self._log(f"  ERROR starting {name}: {e}")

        # ── Audio stream ──────────────────────────────────────────────────────────
        # Audio duration = max of all enabled screen durations (0 means manual stop)
        all_durs = [cfg["screens"][n].get("duration", 0)
                    for n in SCREENS if cfg["screens"][n].get("enabled")]
        duration = max(all_durs) if all_durs else 0
        if cfg["audio_enabled"]:
            device   = cfg["audio_device"]
            aud_mp3  = os.path.join(out_dir, f"{prefix}_Audio_{ts}.mp3")
            self._log(f"▶ Audio  →  {os.path.basename(aud_mp3)}")
            self._log(f"  device: {device or '(system default)'}")

            loopback_meta = getattr(self, "_loopback_meta", {})

            if device in loopback_meta:
                # ── WASAPI loopback via pyaudiowpatch ────────────────────────
                dev_idx, channels, sample_rate = loopback_meta[device]
                aud_wav = aud_mp3.replace(".mp3", "_raw.wav")
                stop_evt = threading.Event()

                def _record_loopback(wav_path=aud_wav, idx=dev_idx,
                                     ch=channels, rate=sample_rate,
                                     dur=duration, stop=stop_evt):
                    import pyaudiowpatch as pyaudio
                    import wave
                    CHUNK = 1024
                    pa = pyaudio.PyAudio()
                    stream = pa.open(format=pyaudio.paInt16,
                                     channels=ch,
                                     rate=rate,
                                     input=True,
                                     input_device_index=idx,
                                     frames_per_buffer=CHUNK)
                    frames = []
                    start = time.time()
                    try:
                        while not stop.is_set():
                            if dur > 0 and (time.time() - start) >= dur:
                                break
                            frames.append(stream.read(CHUNK, exception_on_overflow=False))
                    finally:
                        stream.stop_stream()
                        stream.close()
                        pa.terminate()
                    with wave.open(wav_path, "wb") as wf:
                        wf.setnchannels(ch)
                        wf.setsampwidth(2)  # paInt16 = 2 bytes
                        wf.setframerate(rate)
                        wf.writeframes(b"".join(frames))

                t = threading.Thread(target=_record_loopback, daemon=True)
                t.start()
                self._log(f"  mode: WASAPI loopback (pyaudiowpatch, device idx={dev_idx})")
                self.processes.append({"name": "Audio", "proc": None,
                                       "file": aud_mp3, "wav_file": aud_wav,
                                       "stop_evt": stop_evt, "thread": t,
                                       "ffmpeg": ffmpeg, "err_buf": []})
            else:
                # ── dshow fallback (Stereo Mix / microphone) ──────────────────
                aud_cmd = [
                    ffmpeg, "-y",
                    "-f",                "dshow",
                    "-rtbufsize",        "256M",
                    "-audio_buffer_size", "50",
                    "-i",                f"audio={device}",
                ]
                if duration > 0:
                    aud_cmd += ["-t", str(duration)]
                aud_cmd += ["-q:a", "2", aud_mp3]
                self._log(f"  mode: dshow | cmd: {' '.join(aud_cmd)}")
                import collections
                err_buf = collections.deque(maxlen=120)
                try:
                    proc = self._ffmpeg_popen(
                        aud_cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                    def _drain_aud(p=proc, b=err_buf):
                        try:
                            for raw in p.stderr:
                                b.append(raw.decode(errors="replace").rstrip())
                        except Exception:
                            pass
                    threading.Thread(target=_drain_aud, daemon=True).start()
                    self.processes.append({"name": "Audio", "proc": proc,
                                           "file": aud_mp3, "err_buf": err_buf})
                except Exception as e:
                    self._log(f"  ERROR starting audio: {e}")

        count = len(self.processes)
        self._log(f"Recording {count} stream(s) simultaneously…")

        # Auto-stop after the longest screen duration (0 = manual)
        all_durs = [cfg["screens"][n].get("duration", 0)
                    for n in SCREENS if cfg["screens"][n].get("enabled")]
        max_dur = max(all_durs) if all_durs else 0
        if max_dur > 0:
            time.sleep(max_dur + 2)
            if self.recording:
                self.after(0, self._stop_recording)

    def _stop_recording(self):
        self.recording = False
        self._set_status("⏳  Finalizing files…", WARN)
        threading.Thread(target=self._do_stop, daemon=True).start()

    def _do_stop(self):
        total = len(self.processes)

        # Signal all processes to stop gracefully.
        # CREATE_NEW_PROCESS_GROUP disconnects stdin, so q\n is unreliable.
        # CTRL_BREAK_EVENT is the correct graceful-stop for isolated process groups —
        # FFmpeg catches it, flushes all buffers, and writes the file trailer.
        for entry in self.processes:
            if entry.get("stop_evt"):   # loopback capture thread
                entry["stop_evt"].set()
                continue
            try:
                ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, entry["proc"].pid)
            except Exception:
                pass
            # Also try stdin q in case this build does read it
            try:
                entry["proc"].stdin.write(b"q\n")
                entry["proc"].stdin.flush()
            except Exception:
                pass

        self.after(0, self._set_status, f"⏳  Finalizing {total} file(s)…", WARN)

        # Wait for all in parallel using per-entry threads
        results = {}
        results_lock = threading.Lock()
        done_event   = threading.Event()
        remaining    = [total]

        def _wait_one(entry):
            name    = entry["name"]
            proc    = entry.get("proc")
            path    = entry["file"]
            err_buf = entry.get("err_buf", [])
            self.after(0, self._log, f"  ⏳ Finalizing: {os.path.basename(path)}")

            if entry.get("stop_evt"):  # ── loopback capture thread ──
                t = entry.get("thread")
                if t:
                    t.join(timeout=15)
                # Convert WAV → MP3 with FFmpeg
                wav = entry.get("wav_file", "")
                ffmpeg_bin = entry.get("ffmpeg", "ffmpeg")
                if wav and os.path.exists(wav):
                    self.after(0, self._log, "  Converting WAV → MP3…")
                    try:
                        subprocess.run(
                            [ffmpeg_bin, "-y", "-i", wav, "-q:a", "2", path],
                            capture_output=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    except Exception as e:
                        self.after(0, self._log, f"  WAV→MP3 error: {e}")
                    finally:
                        try:
                            os.remove(wav)
                        except Exception:
                            pass
            else:  # ── FFmpeg subprocess ──
                saved = False
                try:
                    proc.wait(timeout=30)
                    saved = True
                except Exception:
                    pass

                if not saved:
                    try:
                        ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, proc.pid)
                        proc.wait(timeout=10)
                        saved = True
                    except Exception:
                        pass

                if not saved:
                    try:
                        proc.kill()
                        proc.wait(timeout=3)
                    except Exception:
                        pass

            ok  = os.path.exists(path) and os.path.getsize(path) > 0
            sz  = os.path.getsize(path) if os.path.exists(path) else 0
            err = ""
            # Always log last FFmpeg stats line (frame=/size=) for diagnostics
            stats_line = ""
            for line in reversed(list(err_buf)):
                line = line.strip()
                if line.startswith("size=") or line.startswith("frame="):
                    stats_line = line
                    break
            if stats_line:
                err += f"  ↳ {stats_line[:160]}\n"
            if not ok:
                for line in err_buf:
                    line = line.strip()
                    if line and not line.startswith("frame=") and not line.startswith("size="):
                        err += f"  ↳ {line[:140]}\n"
            elif sz > 0:
                err += f"  ↳ size on disk: {sz:,} bytes\n"

            with results_lock:
                results[name] = (path, ok, err)
                remaining[0] -= 1
                if remaining[0] == 0:
                    done_event.set()

        for entry in self.processes:
            threading.Thread(target=_wait_one, args=(entry,), daemon=True).start()

        done_event.wait(timeout=120)

        # Report results in original order
        for entry in self.processes:
            name = entry["name"]
            if name not in results:
                continue
            # (err now always contains diagnostic lines even on success)
            path, ok, err = results[name]
            if ok:
                self.after(0, self._log, f"✓ Saved: {os.path.basename(path)}")
            else:
                self.after(0, self._log,
                           f"⚠ May be incomplete: {name} → {os.path.basename(path)}")
            for line in err.splitlines():
                self.after(0, self._log, line)

        self.after(0, self._on_recording_finished)

    def _on_recording_finished(self):
        self.processes = []
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self._set_status("✅  Done – files saved!", "#a6e3a1")
        self._log("─" * 48)
        # In headless/CLI mode, auto-close after recording completes
        if getattr(self, "_headless", False):
            self.after(1500, self._on_close)

    # ── Close ──────────────────────────────────────────────────────────────────

    def _on_close(self):
        if self.recording:
            self._stop_recording()
            time.sleep(1.5)
        try:
            save_config(self._snapshot_config())
        except Exception:
            pass
        self.destroy()


# ─── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Pinball Screen Recorder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  PinballRecorder.exe                        # Open GUI with saved settings\n"
            "  PinballRecorder.exe --config my.json      # Open GUI pre-loaded with my.json\n"
            "  PinballRecorder.exe --config my.json --autostart  # Headless: record and exit\n"
        ),
    )
    parser.add_argument(
        "--config", metavar="PATH",
        help="Path to a JSON config file. Overrides the default saved config."
    )
    parser.add_argument(
        "--autostart", action="store_true",
        help="Automatically start recording on launch and exit when done. Requires --config with a duration set per screen."
    )
    args = parser.parse_args()

    cli_cfg = None
    if args.config:
        try:
            with open(args.config) as f:
                cli_cfg = json.load(f)
            # Fill any missing keys from defaults
            for k, v in DEFAULT_CONFIG.items():
                cli_cfg.setdefault(k, v)
        except Exception as e:
            print(f"ERROR: Could not load config '{args.config}': {e}")
            sys.exit(1)

    app = PinballRecorder(cli_config=cli_cfg, headless=args.autostart)
    app.mainloop()
