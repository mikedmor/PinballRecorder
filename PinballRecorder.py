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
import sqlite3
import webbrowser
import urllib.request
import zipfile
from ctypes import windll, wintypes
from datetime import datetime

APP_VERSION = "2.1.0"

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


def enum_windows_with_pid():
    """Return list of (hwnd, title, pid) for all visible top-level windows."""
    GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
    result = []
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
                pid = ctypes.c_ulong(0)
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                result.append((hwnd, buf.value, pid.value))
        return True

    windll.user32.EnumWindows(EnumWindowsProc(_callback), 0)
    return result


def _write_wav(path, channels, sample_rate, bits_per_sample, is_float, data):
    """Write a WAV file supporting both PCM (int) and IEEE float formats."""
    import struct
    fmt_tag    = 3 if is_float else 1
    block_align = (bits_per_sample // 8) * channels
    byte_rate   = sample_rate * block_align
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + len(data)))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<IHHIIHH", 16, fmt_tag, channels,
                            sample_rate, byte_rate, block_align, bits_per_sample))
        f.write(b"data")
        f.write(struct.pack("<I", len(data)))
        f.write(data)


# Chromium-based apps have WASAPI sessions but their audio service is sandboxed
# by Windows Job Objects, making Application Loopback capture impossible.
# Apps using DirectSound/WaveOut also have no loopback-accessible WASAPI session.
_UNSUPPORTED_APP_AUDIO_EXES = {
    "chrome.exe", "brave.exe", "msedge.exe", "firefox.exe",
    "opera.exe", "vivaldi.exe", "chromium.exe", "iexplore.exe",
    "discord.exe", "discordapp.exe",      # Electron (Chromium-based)
    "wmplayer.exe",                        # DirectSound/WaveOut
    "explorer.exe",                        # Windows shell / File Explorer (system sounds only)
    "steam.exe",                           # Steam overlay audio not loopback-accessible
    "razerappengine.exe", "razercentralservice.exe",  # Razer background services
}


def _build_pid_info_map():
    """Return {pid: (parent_pid, exe_name_lower)} for all running processes."""
    import ctypes as _ct, ctypes.wintypes as _wt
    TH32CS_SNAPPROCESS = 0x00000002
    class PROCESSENTRY32W(_ct.Structure):
        _fields_ = [("dwSize", _wt.DWORD), ("cntUsage", _wt.DWORD),
                    ("th32ProcessID", _wt.DWORD), ("th32DefaultHeapID", _ct.c_size_t),
                    ("th32ModuleID", _wt.DWORD), ("cntThreads", _wt.DWORD),
                    ("th32ParentProcessID", _wt.DWORD), ("pcPriClassBase", _wt.LONG),
                    ("dwFlags", _wt.DWORD), ("szExeFile", _wt.WCHAR * 260)]
    kernel32 = _ct.WinDLL("kernel32", use_last_error=True)
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    result = {}
    if snap == _wt.HANDLE(-1).value:
        return result
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = _ct.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, _ct.byref(entry)):
            while True:
                result[entry.th32ProcessID] = (entry.th32ParentProcessID,
                                               entry.szExeFile.lower())
                if not kernel32.Process32NextW(snap, _ct.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return result


def _get_wasapi_active_pids():
    """Return the set of PIDs that currently have an active WASAPI render session.

    Only apps using WASAPI (or XAudio2/DirectX which route through WASAPI) will
    appear.  Apps using DirectSound/WaveOut or whose audio service is sandboxed
    (Chromium browsers) will not.  Returns an empty set on any COM error.
    """
    import ctypes as _ct, ctypes.wintypes as _wt, uuid as _uuid

    class _GUID(_ct.Structure):
        _fields_ = [("Data1", _wt.DWORD), ("Data2", _wt.WORD),
                    ("Data3", _wt.WORD),  ("Data4", _ct.c_ubyte * 8)]

    def _guid(s):
        b = _uuid.UUID(s).bytes_le
        return _GUID(int.from_bytes(b[0:4], "little"),
                     int.from_bytes(b[4:6], "little"),
                     int.from_bytes(b[6:8], "little"),
                     (_ct.c_ubyte * 8)(*b[8:]))

    def _vt(obj):
        return _ct.cast(_ct.cast(obj, _ct.POINTER(_ct.c_void_p))[0],
                        _ct.POINTER(_ct.c_void_p))

    def _fn(vt, idx, res, *args):
        return _ct.WINFUNCTYPE(res, _ct.c_void_p, *args)(vt[idx])

    VP, HR, DW, UL = _ct.c_void_p, _ct.HRESULT, _wt.DWORD, _wt.ULONG

    CLSID_MMDeviceEnumerator  = _guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    IID_IMMDeviceEnumerator   = _guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    IID_IAudioSessionManager2 = _guid("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    IID_IAudioSessionControl2 = _guid("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")

    ole32 = _ct.WinDLL("ole32")
    # CoInitialize(STA) is compatible with tkinter's COM state on the main thread.
    # If COM is already initialised (S_FALSE = 1), we still use it; we only
    # call CoUninitialize if *we* were the ones who advanced the refcount.
    hr = ole32.CoInitialize(None)
    _we_init = (hr == 0)  # S_OK; S_FALSE means already init'd, don't balance
    if hr < 0:
        return set()

    pids = set()
    objs = []  # track COM pointers for Release in reverse order
    try:
        def _rel(p):
            if p:
                _fn(_vt(p), 2, UL)(p)

        # IMMDeviceEnumerator
        pEnum = VP()
        if ole32.CoCreateInstance(_ct.byref(CLSID_MMDeviceEnumerator), None, 1,
                                  _ct.byref(IID_IMMDeviceEnumerator),
                                  _ct.byref(pEnum)) < 0 or not pEnum:
            return pids
        objs.append(pEnum)

        # IMMDevice (default render endpoint)
        pDev = VP()
        if _fn(_vt(pEnum), 4, HR, _ct.c_int, _ct.c_int,
               _ct.POINTER(VP))(pEnum, 0, 0, _ct.byref(pDev)) < 0 or not pDev:
            return pids
        objs.append(pDev)

        # IAudioSessionManager2
        pMgr = VP()
        if _fn(_vt(pDev), 3, HR, _ct.POINTER(_GUID), DW, VP,
               _ct.POINTER(VP))(pDev, _ct.byref(IID_IAudioSessionManager2),
                                 1, None, _ct.byref(pMgr)) < 0 or not pMgr:
            return pids
        objs.append(pMgr)

        # IAudioSessionEnumerator
        pSE = VP()
        if _fn(_vt(pMgr), 5, HR, _ct.POINTER(VP))(pMgr, _ct.byref(pSE)) < 0 or not pSE:
            return pids
        objs.append(pSE)

        count = _ct.c_int(0)
        if _fn(_vt(pSE), 3, HR, _ct.POINTER(_ct.c_int))(pSE, _ct.byref(count)) < 0:
            return pids

        for i in range(count.value):
            pCtrl = VP()
            if _fn(_vt(pSE), 4, HR, _ct.c_int,
                   _ct.POINTER(VP))(pSE, i, _ct.byref(pCtrl)) < 0 or not pCtrl:
                continue
            # QI for IAudioSessionControl2 (has GetProcessId at vtable slot 14)
            pCtrl2 = VP()
            hr2 = _fn(_vt(pCtrl), 0, HR, _ct.POINTER(_GUID),
                      _ct.POINTER(VP))(pCtrl, _ct.byref(IID_IAudioSessionControl2),
                                       _ct.byref(pCtrl2))
            _rel(pCtrl)
            if hr2 < 0 or not pCtrl2:
                continue
            pid_val = DW(0)
            if _fn(_vt(pCtrl2), 14, HR, _ct.POINTER(DW))(pCtrl2,
                                                           _ct.byref(pid_val)) >= 0:
                if pid_val.value:
                    pids.add(pid_val.value)
            _rel(pCtrl2)

    except Exception:
        pass
    finally:
        for p in reversed(objs):
            _rel(p)
        if _we_init:
            ole32.CoUninitialize()

    return pids


def _find_root_audio_pid(pid):
    """Walk the process tree upward, staying within the same exe name, to find
    the root ancestor process.  Chrome/Brave/Edge route audio through child
    processes; capturing the root covers the whole tree via INCLUDE_PROCESS_TREE.
    Returns the resolved PID (falls back to the original pid on any error).
    """
    import ctypes as _ct
    import ctypes.wintypes as _wt

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(_ct.Structure):
        _fields_ = [
            ("dwSize",              _wt.DWORD),
            ("cntUsage",            _wt.DWORD),
            ("th32ProcessID",       _wt.DWORD),
            ("th32DefaultHeapID",   _ct.c_size_t),
            ("th32ModuleID",        _wt.DWORD),
            ("cntThreads",          _wt.DWORD),
            ("th32ParentProcessID", _wt.DWORD),
            ("pcPriClassBase",      _wt.LONG),
            ("dwFlags",             _wt.DWORD),
            ("szExeFile",           _wt.WCHAR * 260),
        ]

    kernel32 = _ct.WinDLL("kernel32", use_last_error=True)
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == _wt.HANDLE(-1).value:
        return pid

    try:
        proc_map  = {}   # pid → (parent_pid, exe_name)
        entry     = PROCESSENTRY32W()
        entry.dwSize = _ct.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, _ct.byref(entry)):
            while True:
                proc_map[entry.th32ProcessID] = (
                    entry.th32ParentProcessID,
                    entry.szExeFile.lower(),
                )
                if not kernel32.Process32NextW(snap, _ct.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snap)

    # Walk up the tree as long as the *parent* has the same exe name.
    # proc_map[pid] = (parent_pid, exe_of_pid), so to check the parent's exe
    # we must look up proc_map[parent_pid][1] separately.
    current = pid
    exe = proc_map.get(current, (0, ""))[1]
    while True:
        parent, _ = proc_map.get(current, (0, ""))
        if parent == 0:
            break
        parent_exe = proc_map.get(parent, (0, ""))[1]
        if parent_exe != exe:
            break
        current = parent
    return current


def _get_child_pids(parent_pid):
    """Return direct child PIDs of parent_pid, same-exe-name children first."""
    import ctypes as _ct
    import ctypes.wintypes as _wt

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(_ct.Structure):
        _fields_ = [
            ("dwSize",              _wt.DWORD),
            ("cntUsage",            _wt.DWORD),
            ("th32ProcessID",       _wt.DWORD),
            ("th32DefaultHeapID",   _ct.c_size_t),
            ("th32ModuleID",        _wt.DWORD),
            ("cntThreads",          _wt.DWORD),
            ("th32ParentProcessID", _wt.DWORD),
            ("pcPriClassBase",      _wt.LONG),
            ("dwFlags",             _wt.DWORD),
            ("szExeFile",           _wt.WCHAR * 260),
        ]

    kernel32 = _ct.WinDLL("kernel32", use_last_error=True)
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == _wt.HANDLE(-1).value:
        return []

    try:
        parent_exe = ""
        same_exe, other = [], []
        entry = PROCESSENTRY32W()
        entry.dwSize = _ct.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, _ct.byref(entry)):
            while True:
                if entry.th32ProcessID == parent_pid:
                    parent_exe = entry.szExeFile.lower()
                if not kernel32.Process32NextW(snap, _ct.byref(entry)):
                    break
        # Second pass: collect children
        entry.dwSize = _ct.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, _ct.byref(entry)):
            while True:
                if entry.th32ParentProcessID == parent_pid:
                    if entry.szExeFile.lower() == parent_exe:
                        same_exe.append(entry.th32ProcessID)
                    else:
                        other.append(entry.th32ProcessID)
                if not kernel32.Process32NextW(snap, _ct.byref(entry)):
                    break
        return same_exe + other
    finally:
        kernel32.CloseHandle(snap)


def _app_loopback_capture(pid, wav_path, stop_evt, duration, on_log=None):
    """Capture audio from a specific process PID using the Windows Application
    Loopback API (requires Windows 10 build 19041+).
    Blocks until stop_evt is set or duration expires, then writes WAV to wav_path.
    """
    import sys as _sys, ctypes as _ct, ctypes.wintypes as _wt, threading as _thr
    import time as _time

    build = _sys.getwindowsversion().build
    if build < 19041:
        raise RuntimeError(
            f"Application Loopback requires Windows 10 2004+ (build 19041). "
            f"Current build: {build}"
        )

    # ── GUID bytes helper ─────────────────────────────────────────────────────
    def _guid(s):
        s = s.strip("{}")
        p = s.split("-")
        d1 = int(p[0], 16).to_bytes(4, "little")
        d2 = int(p[1], 16).to_bytes(2, "little")
        d3 = int(p[2], 16).to_bytes(2, "little")
        d4 = bytes.fromhex(p[3] + p[4])
        return (_ct.c_byte * 16)(*d1, *d2, *d3, *d4)

    IID_IAudioClient        = _guid("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
    IID_IAudioCaptureClient = _guid("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    IID_ICompletionHandler  = _guid("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    IID_IUnknown            = _guid("{00000000-0000-0000-C000-000000000046}")
    IID_IAgileObject        = _guid("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    KSDATAFORMAT_SUBTYPE_FLOAT = bytes(_guid("{00000003-0000-0010-8000-00AA00389B71}"))
    VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\{2eef81be-33fa-4800-9670-1cd474972c3f}"

    _IID_COMPLETION_BYTES = bytes(IID_ICompletionHandler)
    _IID_UNKNOWN_BYTES    = bytes(IID_IUnknown)
    _IID_AGILE_BYTES      = bytes(IID_IAgileObject)

    # ── Structures ────────────────────────────────────────────────────────────
    class WAVEFORMATEX(_ct.Structure):
        _fields_ = [("wFormatTag",      _wt.WORD),
                    ("nChannels",       _wt.WORD),
                    ("nSamplesPerSec",  _wt.DWORD),
                    ("nAvgBytesPerSec", _wt.DWORD),
                    ("nBlockAlign",     _wt.WORD),
                    ("wBitsPerSample",  _wt.WORD),
                    ("cbSize",          _wt.WORD)]

    class _WFEXT_SAMPLES(_ct.Union):
        _fields_ = [("wValidBitsPerSample", _wt.WORD),
                    ("wSamplesPerBlock",     _wt.WORD),
                    ("wReserved",            _wt.WORD)]

    class WAVEFORMATEXTENSIBLE(_ct.Structure):
        _fields_ = [("Format",        WAVEFORMATEX),
                    ("Samples",       _WFEXT_SAMPLES),
                    ("dwChannelMask", _wt.DWORD),
                    ("SubFormat",     _ct.c_byte * 16)]

    class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(_ct.Structure):
        _fields_ = [("TargetProcessId",    _wt.DWORD),
                    ("ProcessLoopbackMode", _ct.c_int)]

    class AUDIOCLIENT_ACTIVATION_PARAMS(_ct.Structure):
        _fields_ = [("ActivationType",       _ct.c_int),
                    ("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]

    class _BlobData(_ct.Structure):
        _fields_ = [("cbSize",    _wt.DWORD),
                    ("pBlobData", _ct.c_void_p)]

    class _PV_Union(_ct.Union):
        _fields_ = [("blob", _BlobData), ("_pad", _ct.c_byte * 16)]

    class PROPVARIANT(_ct.Structure):
        _fields_ = [("vt", _wt.WORD),
                    ("r1", _wt.WORD), ("r2", _wt.WORD), ("r3", _wt.WORD),
                    ("u",  _PV_Union)]

    WAVE_FORMAT_EXTENSIBLE     = 0xFFFE
    AUDCLNT_SHAREMODE_SHARED   = 0
    AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
    AUDCLNT_BUFFERFLAGS_SILENT   = 0x2
    VT_BLOB = 0x41

    # ── IActivateAudioInterfaceCompletionHandler COM vtable ───────────────────
    HR       = _ct.c_long
    QI_FT    = _ct.WINFUNCTYPE(HR, _ct.c_void_p, _ct.c_void_p,
                                _ct.POINTER(_ct.c_void_p))
    ADDREF_FT  = _ct.WINFUNCTYPE(_ct.c_ulong, _ct.c_void_p)
    RELEASE_FT = _ct.WINFUNCTYPE(_ct.c_ulong, _ct.c_void_p)
    DONE_FT    = _ct.WINFUNCTYPE(HR, _ct.c_void_p, _ct.c_void_p)

    completed_event = _thr.Event()
    async_op_box    = [None]

    def _qi(this, riid, ppv):
        if riid:
            try:
                queried = bytes((_ct.c_byte * 16).from_address(riid))
                # Accept IUnknown, IActivateAudioInterfaceCompletionHandler, and
                # IAgileObject so COM treats the handler as free-threaded and does
                # not attempt cross-apartment marshaling.
                if queried in (_IID_COMPLETION_BYTES, _IID_UNKNOWN_BYTES,
                               _IID_AGILE_BYTES):
                    if ppv:
                        _ct.cast(ppv, _ct.POINTER(_ct.c_void_p))[0] = this
                    return 0   # S_OK
            except Exception:
                pass
        return 0x80004002   # E_NOINTERFACE

    def _addref(this):   return 1
    def _release(this):  return 1
    def _completed(this, op):
        async_op_box[0] = op
        completed_event.set()
        return 0

    _qi_cb   = QI_FT(_qi)
    _ar_cb   = ADDREF_FT(_addref)
    _rel_cb  = RELEASE_FT(_release)
    _done_cb = DONE_FT(_completed)

    class _VTable(_ct.Structure):
        _fields_ = [("qi",        QI_FT),
                    ("addref",    ADDREF_FT),
                    ("release",   RELEASE_FT),
                    ("completed", DONE_FT)]

    vtable  = _VTable(_qi_cb, _ar_cb, _rel_cb, _done_cb)

    class _Handler(_ct.Structure):
        _fields_ = [("lpVtbl", _ct.POINTER(_VTable))]

    handler     = _Handler(_ct.pointer(vtable))
    handler_ptr = _ct.addressof(handler)

    # ── Activation params ─────────────────────────────────────────────────────
    act_params = AUDIOCLIENT_ACTIVATION_PARAMS()
    act_params.ActivationType = 1   # AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
    act_params.ProcessLoopbackParams.TargetProcessId    = pid
    act_params.ProcessLoopbackParams.ProcessLoopbackMode = 0  # INCLUDE_TARGET_PROCESS_TREE

    pv            = PROPVARIANT()
    pv.vt         = VT_BLOB
    pv.u.blob.cbSize   = _ct.sizeof(act_params)
    pv.u.blob.pBlobData = _ct.addressof(act_params)

    ole32    = _ct.WinDLL("ole32")
    mmdevapi = _ct.WinDLL("mmdevapi")

    # ActivateAudioInterfaceAsync requires MTA (returns RO_E_WRONG_STATE from STA).
    hr_init = ole32.CoInitializeEx(None, 0x0)   # COINIT_MULTITHREADED
    if on_log:
        on_log(f"  CoInitializeEx(MTA): 0x{hr_init & 0xFFFFFFFF:08X}")
    if hr_init not in (0, 1):
        raise RuntimeError(f"CoInitializeEx failed: 0x{hr_init & 0xFFFFFFFF:08X}")
    try:
        fn = mmdevapi.ActivateAudioInterfaceAsync
        fn.restype  = HR
        fn.argtypes = [_ct.c_wchar_p, _ct.c_void_p, _ct.c_void_p,
                       _ct.c_void_p, _ct.POINTER(_ct.c_void_p)]

        async_op_out = _ct.c_void_p()
        hr = fn(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
                _ct.addressof(IID_IAudioClient),
                _ct.addressof(pv),
                handler_ptr,
                _ct.byref(async_op_out))
        if hr < 0:
            raise RuntimeError(f"ActivateAudioInterfaceAsync: 0x{hr & 0xFFFFFFFF:08X}")

        # MTA: callback arrives on an RPC worker thread, no message pump needed.
        if not completed_event.wait(timeout=10):
            raise RuntimeError("Audio activation timed out (10s)")

        async_op = async_op_box[0]
        if not async_op:
            raise RuntimeError("No async_op pointer after activation")

        # ── GetActivateResult (IActivateAudioInterfaceAsyncOperation vtable[3]) ──
        GAR_FT = _ct.WINFUNCTYPE(HR, _ct.c_void_p,
                                   _ct.POINTER(_ct.c_long),
                                   _ct.POINTER(_ct.c_void_p))
        op_vp   = _ct.cast(_ct.c_void_p(async_op), _ct.POINTER(_ct.c_void_p))
        op_vtbl = _ct.cast(_ct.c_void_p(op_vp[0]),  _ct.POINTER(_ct.c_void_p))
        GetActivateResult = GAR_FT(op_vtbl[3])

        act_hr = _ct.c_long()
        ac_ptr = _ct.c_void_p()
        hr = GetActivateResult(async_op, _ct.byref(act_hr), _ct.byref(ac_ptr))
        if hr < 0 or act_hr.value < 0:
            raise RuntimeError(
                f"GetActivateResult: hr=0x{hr&0xFFFFFFFF:08X} "
                f"activate=0x{act_hr.value&0xFFFFFFFF:08X}"
            )

        audio_client = ac_ptr.value
        ac_vp   = _ct.cast(_ct.c_void_p(audio_client), _ct.POINTER(_ct.c_void_p))
        ac_vtbl = _ct.cast(_ct.c_void_p(ac_vp[0]),     _ct.POINTER(_ct.c_void_p))

        # ── GetMixFormat (IAudioClient vtable[8]) ─────────────────────────────
        GMF_FT = _ct.WINFUNCTYPE(HR, _ct.c_void_p, _ct.POINTER(_ct.c_void_p))
        GetMixFormat = GMF_FT(ac_vtbl[8])

        fmt_ptr = _ct.c_void_p()
        hr = GetMixFormat(audio_client, _ct.byref(fmt_ptr))
        if hr < 0:
            raise RuntimeError(f"GetMixFormat: 0x{hr&0xFFFFFFFF:08X}")

        base_fmt        = _ct.cast(fmt_ptr, _ct.POINTER(WAVEFORMATEX)).contents
        channels        = base_fmt.nChannels
        sample_rate     = base_fmt.nSamplesPerSec
        bits_per_sample = base_fmt.wBitsPerSample
        is_float        = base_fmt.wFormatTag == 3   # WAVE_FORMAT_IEEE_FLOAT

        if base_fmt.wFormatTag == WAVE_FORMAT_EXTENSIBLE:
            ext      = _ct.cast(fmt_ptr, _ct.POINTER(WAVEFORMATEXTENSIBLE)).contents
            is_float = (bytes(ext.SubFormat) == KSDATAFORMAT_SUBTYPE_FLOAT)

        # ── Initialize (vtable[3]) ────────────────────────────────────────────
        INIT_FT = _ct.WINFUNCTYPE(HR, _ct.c_void_p, _ct.c_int, _wt.DWORD,
                                   _ct.c_longlong, _ct.c_longlong,
                                   _ct.c_void_p, _ct.c_void_p)
        Initialize = INIT_FT(ac_vtbl[3])
        hr = Initialize(audio_client, AUDCLNT_SHAREMODE_SHARED,
                        AUDCLNT_STREAMFLAGS_LOOPBACK,
                        2_000_000, 0, fmt_ptr, None)
        ole32.CoTaskMemFree(fmt_ptr)
        if hr < 0:
            raise RuntimeError(f"IAudioClient::Initialize: 0x{hr&0xFFFFFFFF:08X}")

        # ── GetService → IAudioCaptureClient (vtable[14]) ────────────────────
        GS_FT = _ct.WINFUNCTYPE(HR, _ct.c_void_p, _ct.c_void_p,
                                  _ct.POINTER(_ct.c_void_p))
        GetService = GS_FT(ac_vtbl[14])

        cc_ptr = _ct.c_void_p()
        hr = GetService(audio_client,
                        _ct.addressof(IID_IAudioCaptureClient),
                        _ct.byref(cc_ptr))
        if hr < 0:
            raise RuntimeError(f"GetService(CaptureClient): 0x{hr&0xFFFFFFFF:08X}")

        capture_client = cc_ptr.value
        cc_vp   = _ct.cast(_ct.c_void_p(capture_client), _ct.POINTER(_ct.c_void_p))
        cc_vtbl = _ct.cast(_ct.c_void_p(cc_vp[0]),       _ct.POINTER(_ct.c_void_p))

        GB_FT  = _ct.WINFUNCTYPE(HR, _ct.c_void_p,
                                   _ct.POINTER(_ct.c_void_p),
                                   _ct.POINTER(_wt.UINT), _ct.POINTER(_wt.DWORD),
                                   _ct.POINTER(_ct.c_uint64),
                                   _ct.POINTER(_ct.c_uint64))
        RB_FT  = _ct.WINFUNCTYPE(HR, _ct.c_void_p, _wt.UINT)
        GNP_FT = _ct.WINFUNCTYPE(HR, _ct.c_void_p, _ct.POINTER(_wt.UINT))
        GetBuffer         = GB_FT(cc_vtbl[3])
        ReleaseBuffer     = RB_FT(cc_vtbl[4])
        GetNextPacketSize = GNP_FT(cc_vtbl[5])

        # ── Start (vtable[10]) ────────────────────────────────────────────────
        START_FT = _ct.WINFUNCTYPE(HR, _ct.c_void_p)
        STOP_FT  = _ct.WINFUNCTYPE(HR, _ct.c_void_p)
        Start = START_FT(ac_vtbl[10])
        Stop  = STOP_FT(ac_vtbl[11])

        hr = Start(audio_client)
        if hr < 0:
            raise RuntimeError(f"IAudioClient::Start: 0x{hr&0xFFFFFFFF:08X}")

        if on_log:
            on_log(f"  app loopback: PID {pid}, {channels}ch, "
                   f"{sample_rate}Hz, {'float' if is_float else 'int'}{bits_per_sample}")

        # ── Capture loop ──────────────────────────────────────────────────────
        frames          = []
        bytes_per_frame = (bits_per_sample // 8) * channels
        t0              = _time.time()

        while not stop_evt.is_set():
            if duration > 0 and (_time.time() - t0) >= duration:
                break

            pkt_size = _wt.UINT(0)
            GetNextPacketSize(capture_client, _ct.byref(pkt_size))

            while pkt_size.value > 0:
                data_vp = _ct.c_void_p()
                nframes = _wt.UINT(0)
                flags   = _wt.DWORD(0)
                dev_pos = _ct.c_uint64(0)
                qpc_pos = _ct.c_uint64(0)

                hr = GetBuffer(capture_client,
                               _ct.byref(data_vp), _ct.byref(nframes),
                               _ct.byref(flags),   _ct.byref(dev_pos),
                               _ct.byref(qpc_pos))
                if hr >= 0 and nframes.value > 0:
                    nbytes = nframes.value * bytes_per_frame
                    if flags.value & AUDCLNT_BUFFERFLAGS_SILENT:
                        frames.append(bytes(nbytes))
                    else:
                        frames.append(_ct.string_at(data_vp, nbytes))
                    ReleaseBuffer(capture_client, nframes)
                else:
                    break
                GetNextPacketSize(capture_client, _ct.byref(pkt_size))

            _time.sleep(0.01)

        Stop(audio_client)

        if frames:
            _write_wav(wav_path, channels, sample_rate, bits_per_sample,
                       is_float, b"".join(frames))
            if on_log:
                on_log(f"  WAV captured: {sum(len(f) for f in frames):,} bytes "
                       f"({len(frames)} chunk(s))")
        else:
            if on_log:
                on_log(f"  ⚠ No audio frames captured from PID {pid}")

    finally:
        ole32.CoUninitialize()


def _app_loopback_subprocess(pid, wav_path, stop_evt, duration, on_log=None):
    """Run _app_loopback_capture in a fresh subprocess to isolate COM state.

    The main process may have COM initialized by tkinter (STA) on the GUI thread.
    Even though capture runs on a daemon thread, spawning a fresh Python process
    guarantees a completely clean COM environment for the capture thread.
    """
    stop_file = wav_path + ".capstop"
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--capture-audio",
               str(pid), wav_path, str(duration), stop_file]
    else:
        cmd = [sys.executable, os.path.abspath(__file__),
               "--capture-audio", str(pid), wav_path, str(duration), stop_file]

    if on_log:
        on_log(f"  capture subprocess: PID {pid}")

    _env = os.environ.copy()
    _env["PYTHONIOENCODING"] = "utf-8"   # ensure print() can write any Unicode
    _env["PYTHONUTF8"] = "1"             # Python 3.7+ UTF-8 mode

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=_env,
    )

    def _relay():
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line and on_log:
                on_log(line)
    t_relay = threading.Thread(target=_relay, daemon=True)
    t_relay.start()

    stop_evt.wait()

    try:
        open(stop_file, "w").close()
    except OSError:
        pass

    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        if on_log:
            on_log("  ⚠ Audio capture subprocess timed out")

    try:
        os.unlink(stop_file)
    except OSError:
        pass

    t_relay.join(timeout=5)


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
    _NO_WIN = subprocess.CREATE_NO_WINDOW

    def _verify(path):
        try:
            r = subprocess.run([path, "-version"], capture_output=True,
                               timeout=5, creationflags=_NO_WIN)
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    # Check the app directory and the dist/ subdirectory (present when running from source)
    for _candidate in (
        os.path.join(_APP_DIR, "ffmpeg.exe"),
        os.path.join(_APP_DIR, "dist", "ffmpeg.exe"),
    ):
        if os.path.exists(_candidate) and _verify(_candidate):
            return _candidate

    # Then try shutil.which (searches system PATH)
    which = shutil.which("ffmpeg")
    if which and _verify(which):
        return which

    # Glob-search winget Packages folder (version-agnostic)
    winget_pkgs = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    for candidate in glob.glob(
            os.path.join(winget_pkgs, "Gyan.FFmpeg*", "**", "ffmpeg.exe"),
            recursive=True):
        if _verify(candidate):
            return candidate

    # Then try known fixed locations
    for path in FFMPEG_SEARCH_PATHS:
        if path and _verify(path):
            return path

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


# ─── Donate ───────────────────────────────────────────────────────────────────
# Replace this URL with your actual PayPal donation link.
DONATE_URL = "https://www.paypal.com/donate/?hosted_button_id=EWURMZE35WTT2"

# ─── PinUP Popper Helpers ─────────────────────────────────────────────────────

PINUP_DB_SEARCH_PATHS = [
    r"C:\vPinball\PinUPSystem\PUPDatabase.db",
    r"C:\PinUPSystem\PUPDatabase.db",
    os.path.join(os.environ.get("USERPROFILE", ""), "PinUPSystem", "PUPDatabase.db"),
]

# Maps our screen names to PinUP's capture folder names
PINUP_SCREEN_FOLDERS = {
    "Playfield": "PlayField",
    "Backglass": "BackGlass",
    "FullDMD":   "Menu",
    "Audio":     "Audio",
}


def find_pinup_db():
    for path in PINUP_DB_SEARCH_PATHS:
        if os.path.exists(path):
            return path
    return None


def load_pinup_games(db_path):
    """Return list of game dicts from PinUP Popper DB.

    Each dict has keys: ``display`` (str), ``rom`` (str),
    ``media_dir`` (str), ``emulator`` (str).
    ``media_dir`` is the ``Emulators.DirMedia`` value for that game's emulator,
    i.e. the root POPMedia folder, e.g.
    ``C:\\vPinball\\PinUPSystem\\POPMedia\\Visual Pinball X``.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT g.GameDisplay, g.GameFileName,
                       COALESCE(e.DirMedia, '')  AS DirMedia,
                       COALESCE(e.EmuName,  '')  AS EmuName
                FROM   Games g
                LEFT JOIN Emulators e ON g.EMUID = e.EMUID
                WHERE  g.GameFileName IS NOT NULL AND g.GameFileName != ''
                ORDER  BY g.GameDisplay COLLATE NOCASE
            """)
            rows = cur.fetchall()
        result = []
        for display, filename, dir_media, emu_name in rows:
            rom = os.path.splitext(filename)[0]  # strip .vpx / .exe
            result.append({
                "display":   (display or rom).strip(),
                "rom":       rom,
                "media_dir": dir_media.strip() if dir_media else "",
                "emulator":  emu_name.strip()  if emu_name  else "",
            })
        return result
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
CONFIGS_DIR  = os.path.join(_APP_DIR, "configs")
CONFIG_FILE  = os.path.join(CONFIGS_DIR, "default_config.json")
GLOBAL_FILE  = os.path.join(_APP_DIR, "global.json")

DEFAULT_PREFS = {
    "ffmpeg_path":        "",
    "pinup_db_path":      "",
    "log_to_file":        False,
    "recent_files":       [],
    "open_folder_after":  False,
    "ignored_audio_apps": sorted(_UNSUPPORTED_APP_AUDIO_EXES),
}


def load_prefs():
    if os.path.exists(GLOBAL_FILE):
        try:
            with open(GLOBAL_FILE) as f:
                prefs = json.load(f)
            for k, v in DEFAULT_PREFS.items():
                prefs.setdefault(k, v)
            return prefs
        except Exception:
            pass
    prefs = dict(DEFAULT_PREFS)
    save_prefs(prefs)   # create global.json on first run
    return prefs


def save_prefs(prefs):
    with open(GLOBAL_FILE, "w") as f:
        json.dump(prefs, f, indent=2)

DEFAULT_CONFIG = {
    "output_folder":        r"C:\vPinball\PinUPSystem\PupCapture",
    "file_prefix":          "pinball",
    "ffmpeg_path":          "",
    "audio_enabled":        True,
    "audio_capture_mode":   "device",
    "audio_device":         "",
    "audio_app_windows":    [],
    "audio_delay":          0,
    "audio_duration":       0,
    "audio_match_screen":   "Playfield",
    "window_title":         "",
    "pinup_game_media_dir": "",
    "pinup_game_rom":       "",
    "pinup_also_save":      False,
    "coords_v2":            True,
    "screens": {
        "Playfield": {"enabled": True, "x": 0, "y": 0, "width": 1920, "height": 1080, "fps": 30, "delay": 5, "duration": 20},
        "Backglass": {"enabled": True, "x": 0, "y": 0, "width": 1280, "height": 720,  "fps": 30, "delay": 5, "duration": 20},
        "FullDMD":   {"enabled": True, "x": 0, "y": 0, "width": 1280, "height": 180,  "fps": 30, "delay": 5, "duration": 20},
    },
}


def load_config():
    # Migrate default_config.json from old root location to configs/ subfolder
    _old_cfg = os.path.join(_APP_DIR, "default_config.json")
    if not os.path.exists(CONFIG_FILE) and os.path.exists(_old_cfg):
        os.makedirs(CONFIGS_DIR, exist_ok=True)
        shutil.move(_old_cfg, CONFIG_FILE)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            return _deep_merge_config(cfg)
        except Exception:
            pass
    return _deep_merge_config(dict(DEFAULT_CONFIG))


def _deep_merge_config(cfg):
    """Apply DEFAULT_CONFIG defaults at every level, including nested screen dicts."""
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    # Deep-merge per-screen defaults
    for sname, sdefault in DEFAULT_CONFIG["screens"].items():
        screen = cfg["screens"].setdefault(sname, dict(sdefault))
        for sk, sv in sdefault.items():
            screen.setdefault(sk, sv)
    return cfg


def save_config(cfg, path=None):
    target = path or CONFIG_FILE
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
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
        self._win_map              = {}
        self._monitors             = []
        self._overlays             = {}
        self._app_audio_window_map = {}   # title → pid, populated by _refresh_app_audio_list

        self._pinup_game_data    = []   # list of game dicts {display, rom, media_dir, emulator}
        self._pinup_display_map = {}   # display name  → game dict
        self._pinup_rom_map     = {}   # rom name      → game dict
        self._recording_files     = {}   # screen_name -> file path for last recording
        self._recording_cfg       = {}   # snapshot of cfg used for last recording
        self._f9_was_down         = False
        self._f8_was_down         = False
        self._log_fh              = None  # open file handle when log-to-file is on
        self.prefs       = load_prefs()
        self.ffmpeg_path = self.prefs.get("ffmpeg_path") or ""
        if not self.ffmpeg_path:
            _detected = find_ffmpeg()
            if _detected:
                self.ffmpeg_path = _detected
                self.prefs["ffmpeg_path"] = _detected
                save_prefs(self.prefs)
        # One-time migration: pick up ffmpeg_path saved in an old per-config file
        if not self.ffmpeg_path:
            _legacy_ff = self.cfg.get("ffmpeg_path", "")
            if _legacy_ff and os.path.exists(_legacy_ff):
                self.ffmpeg_path = _legacy_ff
                self.prefs["ffmpeg_path"] = _legacy_ff
                save_prefs(self.prefs)
        self._apply_styles()
        self._build_ui()
        self._build_menu()
        self._update_title()
        self._refresh_windows()
        self._refresh_audio_devices()
        self._refresh_app_audio_list(
            restore_titles=self.cfg.get("audio_app_windows", []))

        _first_run = not os.path.exists(CONFIG_FILE)
        self._auto_detect_monitors(force_assign=_first_run)
        self._load_pinup_db()
        self._restore_pinup_selection_from_cfg()
        # Auto-find PinUP DB on startup if not already configured
        if not self.prefs.get("pinup_db_path") or not os.path.exists(self.prefs.get("pinup_db_path", "")):
            found_db = find_pinup_db()
            if found_db:
                self._log(f"PinUP DB auto-detected: {found_db}")
                self.prefs["pinup_db_path"] = found_db
                save_prefs(self.prefs)
                self.pinup_db_var.set(found_db)
                self._load_pinup_db()
            else:
                self._log("PinUP DB not found in common locations (configure manually if needed).")

        if self.cfg.get("log_to_file"):
            self._open_log_file()

        # Start polling F9 as a global stop hotkey
        self.after(200, self._poll_hotkey)

        if self.ffmpeg_path:
            self.ffmpeg_path = self._resolve_ffmpeg_path()
            if hasattr(self, "ffmpeg_var"):
                self.ffmpeg_var.set(self.ffmpeg_path)
            self._log(f"FFmpeg: {self.ffmpeg_path}")
            self.after(500, self._get_ffmpeg_info)
        else:
            self._log("⚠  FFmpeg not found – showing setup assistant…")
            if not self._headless:
                self.after(600, self._show_ffmpeg_setup)
            else:
                self._log("❌  Cannot record: FFmpeg path not set. Exiting.")
                self.after(2000, self._on_close)
                return

        self.after(100, self._update_ffmpeg_state)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self._headless:
            # Always log to file in headless mode (no visible console)
            if not self._log_fh:
                self._open_log_file()
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
        # Base defaults
        s.configure(".",
                    background=BG, foreground=FG,
                    fieldbackground=FRAME_BG, troughcolor=BG,
                    bordercolor=ACCENT, insertcolor=FG,
                    selectbackground=ACCENT, selectforeground=BG)
        # Individual widget types
        s.configure("TFrame",       background=BG)
        s.configure("TLabel",       background=BG,       foreground=FG)
        s.configure("TCheckbutton", background=BG,       foreground=FG,
                    indicatorcolor=FRAME_BG)
        s.configure("TEntry",
                    background=FRAME_BG, fieldbackground=FRAME_BG,
                    foreground=FG,       insertcolor=FG,
                    bordercolor=ACCENT,  lightcolor=FRAME_BG, darkcolor=FRAME_BG)
        s.configure("TCombobox",
                    background=FRAME_BG, fieldbackground=FRAME_BG,
                    foreground=FG,       arrowcolor=FG,
                    bordercolor=ACCENT,  lightcolor=FRAME_BG, darkcolor=FRAME_BG)
        s.configure("TScrollbar",
                    background=FRAME_BG, troughcolor=BG,
                    arrowcolor=FG,       bordercolor=BG,
                    lightcolor=FRAME_BG, darkcolor=FRAME_BG)
        s.configure("TProgressbar",
                    background=ACCENT,   troughcolor=FRAME_BG,
                    bordercolor=BG)
        s.configure("TButton",
                    background=FRAME_BG, foreground=ACCENT,
                    bordercolor=ACCENT,  lightcolor=FRAME_BG, darkcolor=FRAME_BG)
        # State maps
        s.map("TCheckbutton",
              background=[("active", BG)],
              indicatorcolor=[("selected", ACCENT), ("!selected", FRAME_BG)])
        s.map("TCombobox",
              background=     [("readonly", FRAME_BG)],
              fieldbackground= [("readonly", FRAME_BG)],
              foreground=      [("readonly", FG)],
              arrowcolor=      [("disabled", "#585b70")])
        s.map("TEntry",
              fieldbackground= [("disabled", BG)])
        s.map("TButton",
              background=      [("active", ACCENT)],
              foreground=      [("active", BG)])

    # ── UI Build ───────────────────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, bg=FRAME_BG, fg=ACCENT, font=("Segoe UI", 9), padx=8, pady=3, **kw):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief="flat", cursor="hand2",
                         font=font, padx=padx, pady=pady, **kw)

    def _build_ui(self):
        PAD = 10
        main = tk.Frame(self, bg=BG, padx=PAD, pady=PAD)
        main.pack(fill="both", expand=True)

        self._build_screen_section(main, PAD)
        self._build_audio_section(main, PAD)
        self._build_settings_section(main, PAD)
        self._build_pinup_section(main, PAD)
        self._build_window_section(main, PAD)
        self._build_controls(main, PAD)
        self._build_log(main)

    def _build_menu(self):
        menubar = tk.Menu(self, bg=FRAME_BG, fg=FG, activebackground=ACCENT,
                          activeforeground=BG, relief="flat", tearoff=False)
        file_menu = tk.Menu(menubar, bg=FRAME_BG, fg=FG, activebackground=ACCENT,
                            activeforeground=BG, relief="flat", tearoff=False)
        self._file_menu = file_menu
        file_menu.add_command(label="  📄 default_config.json",
                              state="disabled", foreground=ACCENT,
                              font=("Segoe UI", 8))
        self._file_menu_cfg_label_idx = 0
        file_menu.add_separator()
        file_menu.add_command(label="New",         accelerator="Ctrl+N", command=self._cmd_new)
        file_menu.add_command(label="Open…",       accelerator="Ctrl+O", command=self._cmd_open)
        self._recent_menu = tk.Menu(file_menu, bg=FRAME_BG, fg=FG, activebackground=ACCENT,
                                    activeforeground=BG, relief="flat", tearoff=False)
        file_menu.add_cascade(label="Open Recent", menu=self._recent_menu)
        self._rebuild_recent_menu()
        file_menu.add_separator()
        file_menu.add_command(label="Save",        accelerator="Ctrl+S", command=self._cmd_save)
        file_menu.add_command(label="Save As…",    accelerator="Ctrl+Shift+S", command=self._cmd_save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Preferences…", command=self._show_preferences)
        menubar.add_cascade(label="File", menu=file_menu)
        self.config(menu=menubar)
        self.bind_all("<Control-n>", lambda _: self._cmd_new())
        self.bind_all("<Control-o>", lambda _: self._cmd_open())
        self.bind_all("<Control-s>", lambda _: self._cmd_save())
        self.bind_all("<Control-S>", lambda _: self._cmd_save_as())

    def _update_title(self):
        base = f"Pinball Screen Recorder v{APP_VERSION}"
        if self._config_path:
            name = os.path.basename(self._config_path)
            self.title(f"{base}  —  {name}")
        else:
            name = os.path.basename(CONFIG_FILE)
            self.title(base)
        if hasattr(self, "_file_menu"):
            self._file_menu.entryconfigure(
                self._file_menu_cfg_label_idx,
                label=f"  📄 {name}")

    def _monitor_by_label(self, label, monitors=None):
        """Return the monitor dict matching a label like 'Monitor 2', or the first monitor."""
        if monitors is None:
            monitors = enum_display_monitors()
        for i, m in enumerate(monitors):
            if f"Monitor {i + 1}" in label:
                return m
        return monitors[0] if monitors else {"x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True}

    def _migrate_config_coords(self, cfg):
        """Convert absolute coords in old configs (pre-coords_v2) to monitor-relative."""
        monitors = enum_display_monitors()
        for screen, sv in cfg.get("screens", {}).items():
            label = sv.get("monitor", "")
            mon = self._monitor_by_label(label, monitors)
            sv["x"] = sv.get("x", 0) - mon["x"]
            sv["y"] = sv.get("y", 0) - mon["y"]
        cfg["coords_v2"] = True

    def _apply_config(self, cfg, path=None):
        """Load cfg dict into all UI widgets and update state."""
        if not cfg.get("coords_v2"):
            self._migrate_config_coords(cfg)
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
        self.audio_enabled.set(cfg.get("audio_enabled", True))
        self.audio_capture_mode_var.set(cfg.get("audio_capture_mode", "device"))
        self._toggle_audio_mode()
        self.audio_device_var.set(cfg.get("audio_device", ""))
        self._refresh_app_audio_list(restore_titles=cfg.get("audio_app_windows", []))
        self.audio_delay_var.set(str(cfg.get("audio_delay", 0)))
        self.audio_duration_var.set(str(cfg.get("audio_duration", 0)))
        self.audio_match_var.set(cfg.get("audio_match_screen", ""))
        self.window_var.set(cfg.get("window_title", ""))
        # PinUP fields
        self.pinup_also_save_var.set(cfg.get("pinup_also_save", False))
        # Restore game selection: config stores ROM name, combo shows display name.
        # The pinup_db_var write-trace fires _load_pinup_db after 300ms which could
        # clobber the combo values list; we schedule the selection restore to run
        # after that delayed reload completes.
        self._load_pinup_db()
        rom = cfg.get("pinup_game_rom", "")
        g   = self._pinup_rom_map.get(rom)
        self.pinup_game_combo.set(g["display"] if g else "")
        self._update_pinup_preview()
        # Re-apply after any deferred trace reload (300ms) to ensure selection sticks
        def _reapply_selection():
            g2 = self._pinup_rom_map.get(rom)
            if g2:
                self.pinup_game_combo.set(g2["display"])
                self._update_pinup_preview()
        self.after(400, _reapply_selection)
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
            initialdir=CONFIGS_DIR,
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            with open(path) as f:
                cfg = json.load(f)
            _deep_merge_config(cfg)
        except Exception as e:
            messagebox.showerror("Open Failed", str(e), parent=self)
            return
        self._add_recent_file(path)
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
        # Suggest ROM name (or display name) as the default filename when a table is selected
        rom = self._get_pinup_rom()
        disp = self.pinup_game_var.get() if hasattr(self, "pinup_game_var") else ""
        suggest = rom or "".join(c for c in disp if c.isalnum() or c in "_- ").strip()
        initial = suggest if suggest else (os.path.basename(self._config_path) if self._config_path else "")
        path = filedialog.asksaveasfilename(
            title="Save Config As",
            initialdir=CONFIGS_DIR,
            initialfile=initial,
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
            self._add_recent_file(path)
            self._update_title()
            self._log(f"Saved as: {path}")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e), parent=self)

    # ── Recent Files ───────────────────────────────────────────────────────────

    def _add_recent_file(self, path):
        path   = os.path.normpath(path)
        recent = self.prefs.setdefault("recent_files", [])
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        self.prefs["recent_files"] = recent[:8]
        save_prefs(self.prefs)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        if not hasattr(self, "_recent_menu") or self._recent_menu is None:
            return
        self._recent_menu.delete(0, "end")
        recent = self.prefs.get("recent_files", [])
        if not recent:
            self._recent_menu.add_command(label="(none)", state="disabled")
        else:
            for p in recent:
                self._recent_menu.add_command(
                    label=os.path.basename(p),
                    command=lambda p=p: self._cmd_open_recent(p))

    def _cmd_open_recent(self, path):
        if self.recording:
            return
        if not os.path.exists(path):
            messagebox.showerror("Not Found",
                f"Could not find:\n{path}\n\nRemoving from recent list.", parent=self)
            recent = self.prefs.get("recent_files", [])
            if path in recent:
                recent.remove(path)
            save_prefs(self.prefs)
            self._rebuild_recent_menu()
            return
        try:
            with open(path) as f:
                cfg = json.load(f)
            _deep_merge_config(cfg)
        except Exception as e:
            messagebox.showerror("Open Failed", str(e), parent=self)
            return
        self._add_recent_file(path)
        self._apply_config(cfg, path=path)

    # ── PinUP Popper Integration ───────────────────────────────────────────────

    def _build_pinup_section(self, parent, pad):
        frame = tk.LabelFrame(parent, text="  PinUP Popper Integration  ",
                               bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold"),
                               relief="groove", bd=1, padx=pad, pady=pad)
        frame.pack(fill="x", pady=(0, pad))
        frame.columnconfigure(1, weight=1)

        # pinup_db_var is a hidden variable (DB path is configured in Preferences)
        self.pinup_db_var = tk.StringVar(value=self.prefs.get("pinup_db_path", ""))

        # ── Row 0: Game / ROM selector ───────────────────────────────────────
        tk.Label(frame, text="Table / ROM:", bg=BG, fg=FG).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self.pinup_game_var = tk.StringVar()
        self.pinup_game_combo = ttk.Combobox(frame, textvariable=self.pinup_game_var,
                                              width=42, state="readonly")
        self.pinup_game_combo.grid(row=0, column=1, sticky="ew")
        btn_col = tk.Frame(frame, bg=BG)
        btn_col.grid(row=0, column=2, padx=(4, 0))
        self._pinup_refresh_btn = self._btn(btn_col, "Refresh", self._load_pinup_db)
        self._pinup_refresh_btn.pack(side="left")
        self._pinup_clear_btn = self._btn(btn_col, "Clear",
                                          lambda: self.pinup_game_var.set(""),
                                          fg="#f38ba8")
        self._pinup_clear_btn.pack(side="left", padx=(4, 0))

        # ── Row 1: Status label ──────────────────────────────────────────────
        self.pinup_status_var = tk.StringVar(value="")
        tk.Label(frame, textvariable=self.pinup_status_var,
                 bg=BG, fg="#585b70", font=("Segoe UI", 8)).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # ── Row 2: Destination preview ───────────────────────────────────────
        pf = tk.Frame(frame, bg=BG)
        pf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 2))
        pf.columnconfigure(1, weight=1)
        self._pinup_preview_frame = pf
        self._pinup_preview_vars  = {}

        tk.Label(pf, text="Output Preview:",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 8, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))

        _SCREEN_COLORS = {"Playfield": "#a6e3a1", "Backglass": "#89b4fa", "FullDMD": "#fab387", "Audio": "#cba6f7"}
        for i, sname in enumerate(SCREENS):
            color = _SCREEN_COLORS.get(sname, FG)
            tk.Label(pf, text=f"  {sname}:",
                     bg=BG, fg=color, font=("Segoe UI", 8)).grid(
                row=i + 1, column=0, sticky="w")
            var = tk.StringVar(value="—")
            self._pinup_preview_vars[sname] = var
            tk.Label(pf, textvariable=var,
                     bg=BG, fg="#7f849c", font=("Segoe UI", 8),
                     anchor="w").grid(row=i + 1, column=1, sticky="ew", padx=(8, 0))

        # Audio row — audio is never moved to POPMedia, always stays in Output Folder
        audio_row = len(SCREENS) + 1
        tk.Label(pf, text="  Audio:",
                 bg=BG, fg=_SCREEN_COLORS["Audio"], font=("Segoe UI", 8)).grid(
            row=audio_row, column=0, sticky="w")
        audio_var = tk.StringVar(value="—")
        self._pinup_preview_vars["Audio"] = audio_var
        tk.Label(pf, textvariable=audio_var,
                 bg=BG, fg="#7f849c", font=("Segoe UI", 8),
                 anchor="w").grid(row=audio_row, column=1, sticky="ew", padx=(8, 0))

        pf.grid_remove()   # hidden until a game with a known media_dir is selected

        # ── Traces ───────────────────────────────────────────────────────────
        self.pinup_db_var.trace_add("write",  lambda *_: self.after(300, self._load_pinup_db))
        self.pinup_game_var.trace_add("write", self._schedule_save)
        self.pinup_game_var.trace_add("write", lambda *_: self._update_pinup_preview())

    def _resolve_ffmpeg_path(self, path=None):
        """Return an absolute path to ffmpeg, resolving relative paths against _APP_DIR."""
        p = path if path is not None else self.ffmpeg_path
        if p and not os.path.isabs(p):
            p = os.path.normpath(os.path.join(_APP_DIR, p))
        return p

    def _update_ffmpeg_state(self):
        """Enable or disable the Start button based on whether FFmpeg is configured."""
        if not hasattr(self, "start_btn") or self.recording:
            return
        ok = bool(self.ffmpeg_path and os.path.isfile(self._resolve_ffmpeg_path()))
        if ok:
            self.start_btn.configure(state="normal", bg=BTN_GREEN, fg="#1e1e2e", cursor="hand2")
        else:
            self.start_btn.configure(state="disabled", bg="#45475a", fg="#6c7086", cursor="arrow")
            if hasattr(self, "ffmpeg_info_var"):
                self.ffmpeg_info_var.set(
                    "⚠  FFmpeg not configured — open File → Preferences to set the path")

    def _show_preferences(self):
        """Modal Preferences dialog for global (cross-config) settings."""
        dlg = tk.Toplevel(self)
        dlg.title("Preferences")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.wm_attributes("-topmost", True)

        PAD = 14
        frm = tk.Frame(dlg, bg=BG, padx=PAD, pady=PAD)
        frm.pack(fill="both")
        frm.columnconfigure(1, weight=1)

        r = 0
        # Section header
        tk.Label(frm, text="Global preferences are shared across all config profiles.",
                 bg=BG, fg="#585b70", font=("Segoe UI", 8)).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 10))
        r += 1

        # ── PATHS section header ───────────────────────────────────────────────────────────
        tk.Label(frm, text="PATHS", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 8, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 6))
        r += 1

        # ── FFmpeg ────────────────────────────────────────────────────────────
        tk.Label(frm, text="FFmpeg Path:", bg=BG, fg=FG).grid(
            row=r, column=0, sticky="w", padx=(0, 6))
        ffmpeg_var_dlg = tk.StringVar(value=self.ffmpeg_path)
        ttk.Entry(frm, textvariable=ffmpeg_var_dlg, width=44).grid(row=r, column=1, sticky="ew")
        ff_btns = tk.Frame(frm, bg=BG)
        ff_btns.grid(row=r, column=2, padx=(4, 0))

        def _browse_ff():
            p = filedialog.askopenfilename(
                title="Select ffmpeg.exe",
                filetypes=[("FFmpeg executable", "ffmpeg.exe"), ("All files", "*.*")],
                parent=dlg)
            if p:
                ffmpeg_var_dlg.set(p)

        def _detect_ff():
            found = find_ffmpeg()
            if found:
                ffmpeg_var_dlg.set(found)
            else:
                messagebox.showwarning("Not Found",
                    "Could not auto-detect FFmpeg.\nBrowse to ffmpeg.exe or add it to PATH.",
                    parent=dlg)

        self._btn(ff_btns, "…",       _browse_ff,              fg=FG,    padx=6).pack(side="left", padx=(0, 2))
        self._btn(ff_btns, "🔍",      _detect_ff,              fg=ACCENT, padx=6).pack(side="left", padx=(0, 2))
        self._btn(ff_btns, "⚙ Setup", self._show_ffmpeg_setup, fg=ACCENT, padx=6).pack(side="left")
        r += 1
        # FFmpeg version / codec info (live — populated from in-memory var)
        tk.Label(frm, textvariable=self.ffmpeg_info_var,
                 bg=BG, fg="#585b70", font=("Segoe UI", 8)).grid(
            row=r, column=1, columnspan=2, sticky="w", pady=(0, 4))
        r += 1

        # ── PinUP Database ────────────────────────────────────────────────────
        tk.Label(frm, text="PinUP Database:", bg=BG, fg=FG).grid(
            row=r, column=0, sticky="w", padx=(0, 6))
        db_var = tk.StringVar(value=self.prefs.get("pinup_db_path", ""))
        ttk.Entry(frm, textvariable=db_var, width=44).grid(row=r, column=1, sticky="ew")
        db_btns = tk.Frame(frm, bg=BG)
        db_btns.grid(row=r, column=2, padx=(4, 0))

        def _browse_db():
            p = filedialog.askopenfilename(
                title="Select PUPDatabase.db",
                filetypes=[("SQLite Database", "*.db"), ("All files", "*.*")],
                parent=dlg)
            if p:
                db_var.set(p)

        def _detect_db():
            found = find_pinup_db()
            if found:
                db_var.set(found)
            else:
                messagebox.showinfo("Not Found",
                    "Could not auto-detect PUPDatabase.db.\n"
                    "Please browse to it manually.", parent=dlg)

        self._btn(db_btns, "…",  _browse_db,  fg=FG,    padx=6).pack(side="left", padx=(0, 2))
        self._btn(db_btns, "🔍", _detect_db,  fg=ACCENT, padx=6).pack(side="left")
        r += 1

        ttk.Separator(frm).grid(row=r, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        r += 1
        tk.Label(frm, text="OPTIONS", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 8, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        r += 1

        # ── Open folder after recording ───────────────────────────────────────
        open_var = tk.BooleanVar(value=self.prefs.get("open_folder_after", False))
        tk.Checkbutton(frm, text="Open output folder when recording finishes",
                       variable=open_var,
                       bg=BG, fg=FG, activebackground=BG, selectcolor=FRAME_BG).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(10, 0))
        r += 1

        # ── Log to file ───────────────────────────────────────────────────────
        log_var = tk.BooleanVar(value=self.prefs.get("log_to_file", False))
        tk.Checkbutton(frm,
                       text="Save session log to PinballRecorder.log (always on in headless mode)",
                       variable=log_var,
                       bg=BG, fg=FG, activebackground=BG, selectcolor=FRAME_BG).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(4, 0))
        r += 1

        # ── Recent files ──────────────────────────────────────────────────────
        tk.Label(frm, text="Recent Files:", bg=BG, fg=FG).grid(
            row=r, column=0, sticky="w", pady=(10, 0))
        local_recent = list(self.prefs.get("recent_files", []))
        recent_cnt = tk.StringVar(value=f"{len(local_recent)} saved file(s)")
        tk.Label(frm, textvariable=recent_cnt,
                 bg=BG, fg="#585b70", font=("Segoe UI", 8)).grid(
            row=r, column=1, sticky="w", pady=(10, 0))

        def _clear_recent():
            nonlocal local_recent
            local_recent = []
            recent_cnt.set("0 saved file(s)")

        self._btn(frm, "Clear", _clear_recent, fg=FG, padx=8).grid(
            row=r, column=2, padx=(4, 0), pady=(10, 0))
        r += 1

        # ── Ignored audio applications ────────────────────────────────────────
        ttk.Separator(frm).grid(row=r, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        r += 1
        tk.Label(frm, text="IGNORED AUDIO APPS", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 8, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(6, 4))
        r += 1
        tk.Label(frm, text="Hidden from the\nApplication capture list:",
                 bg=BG, fg=FG, justify="left").grid(
            row=r, column=0, sticky="nw", padx=(0, 6))

        _ign_outer = tk.Frame(frm, bg=BG)
        _ign_outer.grid(row=r, column=1, sticky="ew")
        _ign_outer.columnconfigure(0, weight=1)
        local_ignored = list(self.prefs.get("ignored_audio_apps",
                                            sorted(_UNSUPPORTED_APP_AUDIO_EXES)))
        _ign_lb = tk.Listbox(
            _ign_outer, height=5,
            bg=FRAME_BG, fg=FG,
            selectbackground=ACCENT, selectforeground=BG,
            font=("Segoe UI", 9), relief="flat", bd=1,
            highlightthickness=1, highlightcolor=ACCENT,
            exportselection=False,
        )
        _ign_lb.grid(row=0, column=0, sticky="ew")
        _ign_sb = ttk.Scrollbar(_ign_outer, orient="vertical",
                                 command=_ign_lb.yview)
        _ign_sb.grid(row=0, column=1, sticky="ns")
        _ign_lb.configure(yscrollcommand=_ign_sb.set)

        def _ign_rebuild():
            _ign_lb.delete(0, tk.END)
            for exe in local_ignored:
                _ign_lb.insert(tk.END, exe)

        _ign_rebuild()

        _ign_btns = tk.Frame(frm, bg=BG)
        _ign_btns.grid(row=r, column=2, sticky="nw", padx=(4, 0))

        def _ign_remove():
            for i in reversed(_ign_lb.curselection()):
                local_ignored.pop(i)
            _ign_rebuild()

        def _ign_restore():
            local_ignored.clear()
            local_ignored.extend(sorted(_UNSUPPORTED_APP_AUDIO_EXES))
            _ign_rebuild()

        self._btn(_ign_btns, "Remove", _ign_remove, fg=FG, padx=8).pack(
            pady=(0, 4), fill="x")
        self._btn(_ign_btns, "Restore\nDefaults", _ign_restore, fg=FG, padx=8).pack(
            fill="x")
        r += 1

        # ── Separator + buttons ───────────────────────────────────────────────
        ttk.Separator(frm).grid(row=r, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        r += 1
        btn_row = tk.Frame(frm, bg=BG)
        btn_row.grid(row=r, column=0, columnspan=3, sticky="e", pady=(10, 0))

        def _apply():
            new_ffmpeg = ffmpeg_var_dlg.get()
            self.prefs["ffmpeg_path"]        = new_ffmpeg
            self.ffmpeg_path                 = new_ffmpeg
            self.ffmpeg_var.set(new_ffmpeg)  # triggers info detection via trace
            self.prefs["pinup_db_path"]      = db_var.get()
            self.prefs["open_folder_after"]  = open_var.get()
            self.prefs["log_to_file"]        = log_var.get()
            self.prefs["recent_files"]       = local_recent
            self.prefs["ignored_audio_apps"] = list(local_ignored)
            save_prefs(self.prefs)
            # Sync in-memory tk vars so recording and log logic picks up changes
            self.pinup_db_var.set(db_var.get())
            self.open_folder_var.set(open_var.get())
            self.log_to_file_var.set(log_var.get())
            self._rebuild_recent_menu()
            self._update_ffmpeg_state()
            self._refresh_app_audio_list()
            dlg.destroy()

        self._btn(btn_row, "OK", _apply,
                  bg=ACCENT, fg=BG, padx=14).pack(side="right", padx=(6, 0))
        self._btn(btn_row, "Cancel", dlg.destroy, padx=14).pack(side="right")

    def _load_pinup_db(self):
        db = self.pinup_db_var.get() if hasattr(self, "pinup_db_var") else ""
        _db_ok = bool(db and os.path.exists(db))
        # Enable/disable table selector based on whether a valid DB is loaded
        if hasattr(self, "pinup_game_combo"):
            self.pinup_game_combo.configure(state="readonly" if _db_ok else "disabled")
        if hasattr(self, "_pinup_refresh_btn"):
            self._pinup_refresh_btn.configure(state="normal" if _db_ok else "disabled")
        if hasattr(self, "_pinup_clear_btn"):
            self._pinup_clear_btn.configure(state="normal" if _db_ok else "disabled")
        if not _db_ok:
            if hasattr(self, "pinup_status_var"):
                self.pinup_status_var.set(
                    "No database configured — set path in ⚙ Preferences." if not db
                    else f"Database not found: {os.path.basename(db)}")
            return
        games = load_pinup_games(db)
        self._pinup_game_data   = games
        self._pinup_display_map = {g["display"]: g for g in games}
        self._pinup_rom_map     = {g["rom"]:     g for g in games}
        if hasattr(self, "pinup_game_combo"):
            self.pinup_game_combo["values"] = [g["display"] for g in games]
        if hasattr(self, "pinup_status_var"):
            no_media = sum(1 for g in games if not g["media_dir"])
            suffix   = f"  ({no_media} without media path)" if no_media else ""
            self.pinup_status_var.set(
                f"Loaded {len(games)} table(s){suffix}." if games
                else "No tables found in database.")

    def _restore_pinup_selection_from_cfg(self):
        """Restore the pinup game combo selection from self.cfg after the DB is loaded."""
        rom = self.cfg.get("pinup_game_rom", "")
        if not rom:
            return
        g = self._pinup_rom_map.get(rom)
        if g and hasattr(self, "pinup_game_combo"):
            self.pinup_game_combo.set(g["display"])
            self._update_pinup_preview()
        elif hasattr(self, "pinup_game_combo") and not g:
            self._log(f"⚠  Saved table ROM '{rom}' not found in loaded DB — selection cleared.")

    def _get_pinup_rom(self):
        """Return ROM name for the currently selected game display name."""
        disp = self.pinup_game_var.get() if hasattr(self, "pinup_game_var") else ""
        g    = self._pinup_display_map.get(disp)
        return g["rom"] if g else ""

    def _get_pinup_media_dir(self):
        """Return the emulator's POPMedia folder path for the selected game."""
        disp = self.pinup_game_var.get() if hasattr(self, "pinup_game_var") else ""
        g    = self._pinup_display_map.get(disp)
        return g["media_dir"] if g else ""

    def _update_pinup_preview(self):
        """Refresh the destination-path preview labels for the selected game."""
        if not hasattr(self, "_pinup_preview_vars"):
            return
        disp  = self.pinup_game_var.get() if hasattr(self, "pinup_game_var") else ""
        gdata = self._pinup_display_map.get(disp)
        has_pinup = bool(gdata and gdata.get("media_dir"))

        if has_pinup:
            media_dir = gdata["media_dir"]
            rom       = gdata["rom"]
            for sname, var in self._pinup_preview_vars.items():
                folder = PINUP_SCREEN_FOLDERS.get(sname, sname)
                ext    = ".mp3" if sname == "Audio" else ".mp4"
                path   = os.path.join(media_dir, folder, f"{rom}{ext}")
                var.set(path if len(path) <= 74 else "\u2026" + path[-73:])
            self._pinup_preview_frame.grid()
        else:
            self._pinup_preview_frame.grid_remove()

        self._update_output_folder_state()

    def _update_output_folder_state(self):
        """Grey out File Prefix (not Output Folder) when PinUP mode active and 'also save' is off."""
        if not hasattr(self, "_output_folder_entry"):
            return
        has_pinup = bool(self._get_pinup_media_dir())
        locked    = has_pinup and not self.pinup_also_save_var.get()
        prefix_state = "disabled" if locked else "normal"
        self._prefix_entry.configure(state=prefix_state)
        if has_pinup:
            self._pinup_output_note.grid()
            self._pinup_also_save_cb.grid()
        else:
            self._pinup_output_note.grid_remove()
            self._pinup_also_save_cb.grid_remove()

    # ── FFmpeg Info ────────────────────────────────────────────────────────────

    def _get_ffmpeg_info(self):
        ffmpeg = self._resolve_ffmpeg_path(
            self.ffmpeg_var.get() if hasattr(self, "ffmpeg_var") else self.ffmpeg_path)
        if not ffmpeg or not os.path.exists(ffmpeg):
            return
        def _run():
            try:
                import re
                r = subprocess.run([ffmpeg, "-version"],
                                   capture_output=True, text=True, timeout=5,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                m   = re.search(r"ffmpeg version (\S+)", r.stdout)
                ver = m.group(1) if m else "?"
                r2  = subprocess.run([ffmpeg, "-encoders"],
                                     capture_output=True, text=True, timeout=5,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                hw = []
                if "h264_nvenc" in r2.stdout: hw.append("NVENC")
                if "h264_qsv"   in r2.stdout: hw.append("QSV")
                if "h264_amf"   in r2.stdout: hw.append("AMF")
                hw_str = f"  •  HW encoders: {', '.join(hw)}" if hw else "  •  No HW encoders detected"
                self.after(0, self.ffmpeg_info_var.set, f"v{ver}{hw_str}")
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    # ── Global Hotkeys (F8 = start, F9 = stop) ─────────────────────────────────

    def _poll_hotkey(self):
        """Poll F8/F9 every 100 ms — works even when the app window is not focused."""
        try:
            GetAsyncKeyState = ctypes.windll.user32.GetAsyncKeyState
            f8 = bool(GetAsyncKeyState(0x77) & 0x8000)  # VK_F8
            f9 = bool(GetAsyncKeyState(0x78) & 0x8000)  # VK_F9

            if f8 and not self._f8_was_down and not self.recording:
                threading.Thread(target=self._f8_start, daemon=True).start()
            if f9 and not self._f9_was_down and self.recording:
                self.after(0, self._stop_recording)

            self._f8_was_down = f8
            self._f9_was_down = f9
        except Exception:
            pass
        self.after(100, self._poll_hotkey)

    def _f8_start(self):
        """Play a short acknowledgement beep, then start recording.
        Runs in a background thread so the sound fully completes before any
        capture begins — safe even when all recording delays are zero.
        """
        try:
            import winsound
            winsound.Beep(440, 100)
            winsound.Beep(660, 150)
        except Exception:
            pass
        self.after(0, self._start_recording)

    # ── Log File ──────────────────────────────────────────────────────────────

    def _open_log_file(self):
        log_path = os.path.join(_APP_DIR, "PinballRecorder.log")
        try:
            self._log_fh = open(log_path, "a", encoding="utf-8")
            self._log_fh.write(f"\n{'='*60}\n")
            self._log_fh.write(f"Session: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._log_fh.write(f"{'='*60}\n")
            self._log_fh.flush()
        except Exception as e:
            self._log_fh = None
            self._log(f"⚠  Could not open log file: {e}")

    def _on_log_to_file_changed(self, *_):
        if not hasattr(self, "log_to_file_var"):
            return
        if self.log_to_file_var.get():
            if not self._log_fh:
                self._open_log_file()
                self._log("Log file enabled.")
        else:
            if self._log_fh:
                try:
                    self._log_fh.close()
                except Exception:
                    pass
                self._log_fh = None
                self._log("Log file disabled.")

    # ── PinUP File Move ────────────────────────────────────────────────────────

    def _move_to_pinup(self):
        """Move recorded files into the PinUP POPMedia folder structure."""
        rec_cfg      = self._recording_cfg
        rom          = rec_cfg.get("pinup_game_rom", "")
        capture_root = rec_cfg.get("pinup_game_media_dir", "")
        if not rom or not capture_root:
            return

        all_streams = list(SCREENS) + ["Audio"]
        for screen_name in all_streams:
            src = self._recording_files.get(screen_name)
            if not src or not os.path.exists(src):
                continue
            ext         = os.path.splitext(src)[1]
            folder_name = PINUP_SCREEN_FOLDERS.get(screen_name, screen_name)
            dest_dir    = os.path.join(capture_root, folder_name)
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, f"{rom}{ext}")

            if os.path.exists(dest):
                answer = self._pinup_conflict_dialog(screen_name, dest)
                if answer == "cancel":
                    self._log(f"  Skipped (cancelled): {screen_name}")
                    continue
                elif answer == "append":
                    n = 1
                    while True:
                        dest = os.path.join(dest_dir, f"{rom}_{n:02d}{ext}")
                        if not os.path.exists(dest):
                            break
                        n += 1
                # "overwrite" — dest already set, shutil.move will replace
            try:
                if rec_cfg.get("pinup_also_save"):
                    shutil.copy2(src, dest)
                    self._log(f"✓ PinUP [{screen_name}] copied → {dest}")
                else:
                    shutil.move(src, dest)
                    self._log(f"✓ PinUP [{screen_name}] → {dest}")
            except Exception as e:
                self._log(f"  ERROR moving {screen_name}: {e}")

        if rec_cfg.get("open_folder_after") and os.path.isdir(capture_root):
            try:
                os.startfile(capture_root)
            except Exception:
                pass

    def _pinup_conflict_dialog(self, screen_name, dest_path):
        """Modal dialog asking Overwrite / Append / Skip. Returns the choice string."""
        result = {"v": "cancel"}

        dlg = tk.Toplevel(self)
        dlg.title("File Already Exists")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.wm_attributes("-topmost", True)

        tk.Label(dlg, text=f"A capture already exists for  {screen_name}:",
                 bg=BG, fg=FG, font=("Segoe UI", 9)).pack(padx=24, pady=(18, 4))
        tk.Label(dlg, text=os.path.basename(dest_path),
                 bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold")).pack(padx=24)
        tk.Label(dlg, text="What would you like to do?",
                 bg=BG, fg=FG, font=("Segoe UI", 9)).pack(padx=24, pady=(10, 14))

        row = tk.Frame(dlg, bg=BG)
        row.pack(padx=24, pady=(0, 18))

        def _pick(v):
            result["v"] = v
            dlg.destroy()

        self._btn(row, "Overwrite",        lambda: _pick("overwrite"),
                  bg=BTN_RED,   fg="#1e1e2e", padx=10).pack(side="left", padx=(0, 6))
        self._btn(row, "Append (01, 02…)", lambda: _pick("append"),
                  bg=BTN_GREEN, fg="#1e1e2e", padx=10).pack(side="left", padx=(0, 6))
        self._btn(row, "Skip",             lambda: _pick("cancel"),
                  padx=10).pack(side="left")

        dlg.update_idletasks()
        cx = self.winfo_x() + (self.winfo_width()  - dlg.winfo_reqwidth())  // 2
        cy = self.winfo_y() + (self.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{cx}+{cy}")
        dlg.wait_window()
        return result["v"]

    def _build_screen_section(self, parent, pad):
        frame = tk.LabelFrame(parent, text="  Screen Configuration  ",
                               bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold"),
                               relief="groove", bd=1, padx=pad, pady=pad)
        frame.pack(fill="x", pady=(0, pad))

        headers = ["Screen", "On", "Monitor", "X", "Y", "Width", "Height", "FPS", "Delay(s)", "Duration(s)", ""]
        for col, h in enumerate(headers):
            tk.Label(frame, text=h, bg=BG, fg=ACCENT,
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

            tk.Label(frame, text=name, bg=BG, fg=SCREEN_COLOR[name],
                     font=("Segoe UI", 9, "bold")).grid(row=row, column=0, padx=4, pady=2, sticky="w")
            tk.Checkbutton(frame, variable=ev, bg=BG, fg=FG,
                           activebackground=BG, selectcolor=FRAME_BG).grid(row=row, column=1, padx=4)

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

        self._btn(frame, "🔍 Auto-Detect Monitors", lambda: self._auto_detect_monitors(force_assign=True)).grid(
            row=len(SCREENS) + 1, column=0, columnspan=11, pady=(8, 0), sticky="w")

    def _build_audio_section(self, parent, pad):
        frame = tk.LabelFrame(parent, text="  Audio  ",
                               bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold"),
                               relief="groove", bd=1, padx=pad, pady=pad)
        frame.pack(fill="x", pady=(0, pad))
        frame.columnconfigure(1, weight=1)

        # Row 0: enable checkbox
        self.audio_enabled = tk.BooleanVar(value=self.cfg.get("audio_enabled", True))
        tk.Checkbutton(frame, text="Record audio to a separate MP3",
                       variable=self.audio_enabled,
                       bg=BG, fg=FG, activebackground=BG, selectcolor=FRAME_BG).grid(
            row=0, column=0, columnspan=4, sticky="w")

        # Row 1: capture mode radio buttons
        self.audio_capture_mode_var = tk.StringVar(
            value=self.cfg.get("audio_capture_mode", "device"))
        _mode_row = tk.Frame(frame, bg=BG)
        _mode_row.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        tk.Label(_mode_row, text="Capture Mode:", bg=BG, fg=FG).pack(side="left", padx=(0, 8))
        tk.Radiobutton(_mode_row, text="Device", variable=self.audio_capture_mode_var,
                       value="device", bg=BG, fg=FG, activebackground=BG,
                       selectcolor=FRAME_BG,
                       command=self._toggle_audio_mode).pack(side="left", padx=(0, 6))
        tk.Radiobutton(_mode_row, text="Application", variable=self.audio_capture_mode_var,
                       value="application", bg=BG, fg=FG, activebackground=BG,
                       selectcolor=FRAME_BG,
                       command=self._toggle_audio_mode).pack(side="left")

        # Row 2: device frame (shown in device mode)
        self._audio_device_frame = tk.Frame(frame, bg=BG)
        self._audio_device_frame.grid(row=2, column=0, columnspan=4,
                                       sticky="ew", pady=(6, 0))
        self._audio_device_frame.columnconfigure(1, weight=1)
        tk.Label(self._audio_device_frame, text="Capture Device:",
                 bg=BG, fg=FG).grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.audio_device_var = tk.StringVar(value=self.cfg.get("audio_device", ""))
        self.audio_combo = ttk.Combobox(self._audio_device_frame,
                                         textvariable=self.audio_device_var,
                                         width=44, state="readonly")
        self.audio_combo.grid(row=0, column=1, sticky="ew")
        self._btn(self._audio_device_frame, "Refresh",
                  self._refresh_audio_devices).grid(row=0, column=2, padx=(4, 0))

        # Row 3: application frame (shown in application mode)
        self._audio_app_frame = tk.Frame(frame, bg=BG)
        self._audio_app_frame.grid(row=3, column=0, columnspan=4,
                                    sticky="ew", pady=(6, 0))
        self._audio_app_frame.columnconfigure(0, weight=1)

        _app_hdr = tk.Frame(self._audio_app_frame, bg=BG)
        _app_hdr.grid(row=0, column=0, sticky="ew")
        _app_hdr.columnconfigure(0, weight=1)
        tk.Label(_app_hdr,
                 text="Applications (select one or more — Ctrl+click for multi-select):",
                 bg=BG, fg=FG).grid(row=0, column=0, sticky="w")
        self._btn(_app_hdr, "Refresh",
                  self._refresh_app_audio_list).grid(row=0, column=1, padx=(4, 0))

        _lb_frame = tk.Frame(self._audio_app_frame, bg=BG)
        _lb_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        _lb_frame.columnconfigure(0, weight=1)
        self._app_audio_listbox = tk.Listbox(
            _lb_frame, selectmode=tk.EXTENDED, height=4,
            bg=FRAME_BG, fg=FG,
            selectbackground=ACCENT, selectforeground=BG,
            font=("Segoe UI", 9), activestyle="none",
            relief="flat", bd=1,
            highlightthickness=1, highlightcolor=ACCENT,
        )
        self._app_audio_listbox.grid(row=0, column=0, sticky="ew")
        _sb = ttk.Scrollbar(_lb_frame, orient="vertical",
                             command=self._app_audio_listbox.yview)
        _sb.grid(row=0, column=1, sticky="ns")
        self._app_audio_listbox.configure(yscrollcommand=_sb.set)
        self._app_audio_listbox.bind("<<ListboxSelect>>",
                                      lambda e: self._schedule_save())
        self._app_audio_listbox.bind("<Button-3>", self._on_audio_app_right_click)

        # Set initial visibility based on saved mode
        self._toggle_audio_mode()

        # Row 4: Delay / Duration / Match-screen — flat single row
        _timing = tk.Frame(frame, bg=BG)
        _timing.grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))

        tk.Label(_timing, text="Delay (s):", bg=BG, fg=FG).pack(side="left", padx=(0, 6))
        self.audio_delay_var = tk.StringVar(value=str(self.cfg.get("audio_delay", 0)))
        self._audio_delay_spin = ttk.Spinbox(_timing, textvariable=self.audio_delay_var,
                                              from_=0, to=300, width=6)
        self._audio_delay_spin.pack(side="left")

        tk.Label(_timing, text="Duration (s, 0=auto):", bg=BG, fg=FG).pack(side="left", padx=(20, 6))
        self.audio_duration_var = tk.StringVar(value=str(self.cfg.get("audio_duration", 0)))
        self._audio_duration_spin = ttk.Spinbox(_timing, textvariable=self.audio_duration_var,
                                                 from_=0, to=3600, width=6)
        self._audio_duration_spin.pack(side="left")

        tk.Label(_timing, text="Match screen:", bg=BG, fg=FG).pack(side="left", padx=(20, 6))
        self.audio_match_var = tk.StringVar(value=self.cfg.get("audio_match_screen", ""))
        match_combo = ttk.Combobox(_timing, textvariable=self.audio_match_var,
                                    values=[""] + SCREENS, width=12, state="readonly")
        match_combo.pack(side="left")

        def _on_match_screen(*_):
            sname = self.audio_match_var.get()
            matched = bool(sname and sname in self.screen_vars)
            spin_state = "disabled" if matched else "normal"
            self._audio_delay_spin.configure(state=spin_state)
            self._audio_duration_spin.configure(state=spin_state)
            if matched:
                self.audio_delay_var.set(self.screen_vars[sname]["delay"].get())
                self.audio_duration_var.set(self.screen_vars[sname]["duration"].get())
        self.audio_match_var.trace_add("write", _on_match_screen)
        # When a matched screen's delay or duration changes, keep audio in sync
        for _sname in SCREENS:
            for _field in ("delay", "duration"):
                self.screen_vars[_sname][_field].trace_add(
                    "write",
                    lambda *_, n=_sname: _on_match_screen() if self.audio_match_var.get() == n else None)
        # Apply initial state in case config restored a match
        self.after(100, _on_match_screen)

        for _v in (self.audio_enabled, self.audio_capture_mode_var,
                   self.audio_device_var,
                   self.audio_delay_var, self.audio_duration_var, self.audio_match_var):
            _v.trace_add("write", self._schedule_save)

    def _build_settings_section(self, parent, pad):
        frame = tk.LabelFrame(parent, text="  Recording Settings  ",
                               bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold"),
                               relief="groove", bd=1, padx=pad, pady=pad)
        frame.pack(fill="x", pady=(0, pad))
        frame.columnconfigure(1, weight=1)

        # Output folder
        tk.Label(frame, text="Output Folder:", bg=BG, fg=FG).grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.output_folder_var = tk.StringVar(value=self.cfg.get("output_folder", ""))
        self._output_folder_entry = ttk.Entry(frame, textvariable=self.output_folder_var, width=42)
        self._output_folder_entry.grid(row=0, column=1, sticky="ew")
        self._output_folder_browse = self._btn(frame, "…", self._browse_folder, fg=FG, padx=6)
        self._output_folder_browse.grid(row=0, column=2, padx=(4, 0))

        # File prefix
        tk.Label(frame, text="File Prefix:", bg=BG, fg=FG).grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.prefix_var = tk.StringVar(value=self.cfg.get("file_prefix", "pinball"))
        self._prefix_entry = ttk.Entry(frame, textvariable=self.prefix_var, width=22)
        self._prefix_entry.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(6, 0))

        # PinUP mode note + "Also keep" checkbox (hidden until a PinUP game is selected)
        self._pinup_output_note = tk.Label(
            frame,
            text="ⓘ  Files will be moved to POPMedia after recording — output folder used as temp.",
            bg=BG, fg=WARN, font=("Segoe UI", 8))
        self._pinup_output_note.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._pinup_output_note.grid_remove()

        self.pinup_also_save_var = tk.BooleanVar(value=self.cfg.get("pinup_also_save", False))
        self._pinup_also_save_cb = tk.Checkbutton(
            frame, text="Also keep copies in Output Folder",
            variable=self.pinup_also_save_var,
            bg=BG, fg=FG, activebackground=BG, selectcolor=FRAME_BG,
            command=self._update_output_folder_state)
        self._pinup_also_save_cb.grid(row=3, column=0, columnspan=3, sticky="w")
        self._pinup_also_save_cb.grid_remove()

        # FFmpeg — path is managed in File → Preferences; vars kept for recording logic
        self.ffmpeg_var = tk.StringVar(value=self.ffmpeg_path)
        self.ffmpeg_info_var = tk.StringVar(value="")

        # Prefs-backed vars — no UI widgets here; edit via File → Preferences
        self.open_folder_var = tk.BooleanVar(value=self.prefs.get("open_folder_after", False))
        self.log_to_file_var = tk.BooleanVar(value=self.prefs.get("log_to_file", False))
        self.log_to_file_var.trace_add("write", self._on_log_to_file_changed)

        for _v in (self.output_folder_var, self.prefix_var, self.pinup_also_save_var):
            _v.trace_add("write", self._schedule_save)
        self.ffmpeg_var.trace_add("write", lambda *_: self.after(400, self._get_ffmpeg_info))

    def _build_window_section(self, parent, pad):
        frame = tk.LabelFrame(parent, text="  Window Focus  ",
                               bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold"),
                               relief="groove", bd=1, padx=pad, pady=pad)
        frame.pack(fill="x", pady=(0, pad))
        frame.columnconfigure(1, weight=1)

        tk.Label(frame, text="Focus window before recording:", bg=BG, fg=FG).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self.window_var = tk.StringVar(value=self.cfg.get("window_title", ""))
        self.window_combo = ttk.Combobox(frame, textvariable=self.window_var, width=36)
        self.window_combo.grid(row=0, column=1, sticky="ew")
        win_btn_col = tk.Frame(frame, bg=BG)
        win_btn_col.grid(row=0, column=2, padx=(4, 0))
        self._btn(win_btn_col, "Refresh", self._refresh_windows).pack(side="left")
        self._btn(win_btn_col, "Clear",
                  lambda: self.window_var.set(""),
                  fg="#f38ba8").pack(side="left", padx=(4, 0))
        self.window_var.trace_add("write", self._schedule_save)

    def _build_controls(self, parent, pad):
        frame = tk.Frame(parent, bg=BG)
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

        self._btn(frame, "♥  Donate", lambda: webbrowser.open(DONATE_URL),
                  fg="#f38ba8", font=("Segoe UI", 8), padx=8, pady=4).pack(side="right")
        tk.Label(frame, text="F8 = start  |  F9 = stop", bg=BG, fg="#585b70",
                 font=("Segoe UI", 8)).pack(side="right", padx=(0, 10))

    def _build_log(self, parent):
        frame = tk.LabelFrame(parent, text="  Log  ",
                               bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold"),
                               relief="groove", bd=1, padx=4, pady=4)
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
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        if self._log_fh:
            try:
                self._log_fh.write(line)
                self._log_fh.flush()
            except Exception:
                pass

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
            self._save_config(self._snapshot_config())
        except Exception:
            pass

    def _save_config(self, cfg):
        """Save cfg to the currently open named file AND always to CONFIG_FILE."""
        save_config(cfg, CONFIG_FILE)
        if self._config_path and os.path.normpath(self._config_path) != os.path.normpath(CONFIG_FILE):
            save_config(cfg, self._config_path)

    def _sync_overlay(self, name):
        """Reposition an open preview overlay when the field values change."""
        ov = self._overlays.get(name)
        if not ov:
            return
        v = self.screen_vars[name]
        try:
            rel_x = int(v["x"].get() or 0)
            rel_y = int(v["y"].get() or 0)
            w = max(1, int(v["width"].get() or 1))
            h = max(1, int(v["height"].get() or 1))
            mon = self._monitor_by_label(v["monitor"].get(), self._monitors)
            ov.geometry(f"{w}x{h}+{mon['x'] + rel_x}+{mon['y'] + rel_y}")
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
                v["x"].set("0")
                v["y"].set("0")
                v["width"].set(str(mon["width"]))
                v["height"].set(str(mon["height"]))
                v["monitor"].set(f"Monitor {idx + 1}")
                self._log(f"  → {name} assigned to Monitor {idx + 1}")

    def _on_monitor_selected(self, name):
        """Auto-fill X/Y/W/H when user picks a monitor from the dropdown."""
        v   = self.screen_vars[name]
        sel = v["monitor"].get()          # e.g. "Monitor 2"
        try:
            idx = int(sel.split()[1]) - 1
            mon = self._monitors[idx]
            v["x"].set("0")
            v["y"].set("0")
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
        self._monitors = enum_display_monitors()
        v = self.screen_vars[name]
        try:
            rel_x = int(v["x"].get() or 0)
            rel_y = int(v["y"].get() or 0)
            w = int(v["width"].get()  or 800)
            h = int(v["height"].get() or 600)
        except ValueError:
            rel_x, rel_y, w, h = 0, 0, 800, 600
        mon = self._monitor_by_label(v["monitor"].get(), self._monitors)
        x = mon["x"] + rel_x
        y = mon["y"] + rel_y

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
            self._monitors = enum_display_monitors()
            mon = self._get_monitor_for_overlay(ov)
            if mon:
                ov.geometry(f"{mon['width']}x{mon['height']}+{mon['x']}+{mon['y']}")
                _update_dims()

        def _snap_full_width():
            self._monitors = enum_display_monitors()
            mon = self._get_monitor_for_overlay(ov)
            if mon:
                ov.geometry(f"{mon['width']}x{ov.winfo_height()}+{mon['x']}+{ov.winfo_y()}")
                _update_dims()

        def _snap_full_height():
            self._monitors = enum_display_monitors()
            mon = self._get_monitor_for_overlay(ov)
            if mon:
                ov.geometry(f"{ov.winfo_width()}x{mon['height']}+{ov.winfo_x()}+{mon['y']}")
                _update_dims()

        def _snap_top_half():
            self._monitors = enum_display_monitors()
            mon = self._get_monitor_for_overlay(ov)
            if mon:
                ov.geometry(f"{mon['width']}x{mon['height']//2}+{mon['x']}+{mon['y']}")
                _update_dims()

        def _snap_bottom_half():
            self._monitors = enum_display_monitors()
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
            monitors = enum_display_monitors()
            self._monitors = monitors
            mon = self._get_monitor_for_overlay(ov)
            if mon and mon in self._monitors:
                v["monitor"].set(f"Monitor {self._monitors.index(mon) + 1}")
            else:
                mon = self._monitors[0] if self._monitors else {"x": 0, "y": 0}
            v["x"].set(str(ov.winfo_x() - mon["x"]))
            v["y"].set(str(ov.winfo_y() - mon["y"]))
            v["width"].set(str(ov.winfo_width()))
            v["height"].set(str(ov.winfo_height()))
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
            # Use a relative path when ffmpeg.exe lives next to the app itself
            if os.path.normcase(os.path.dirname(os.path.abspath(path))) == \
               os.path.normcase(os.path.abspath(_APP_DIR)):
                path = os.path.join(".", os.path.basename(path))
            self.ffmpeg_path = path
            self.ffmpeg_var.set(path)
            self.prefs["ffmpeg_path"] = path
            save_prefs(self.prefs)
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
            self.prefs["ffmpeg_path"] = path
            save_prefs(self.prefs)
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
            self._log(f"✓ WASAPI loopback ready ({len(loopback_labels)} device(s))")
        elif dshow_devices:
            self._log(f"⚠  No loopback devices — using dshow fallback ({len(dshow_devices)} device(s))")
        else:
            self._log("⚠  No audio input devices found.")

    def _toggle_audio_mode(self):
        """Show/hide device vs application capture frames based on current mode."""
        if self.audio_capture_mode_var.get() == "device":
            self._audio_device_frame.grid()
            self._audio_app_frame.grid_remove()
        else:
            self._audio_device_frame.grid_remove()
            self._audio_app_frame.grid()

    def _get_audio_app_windows(self):
        """Return list of currently selected window titles from the app listbox."""
        try:
            return [self._app_audio_listbox.get(i)
                    for i in self._app_audio_listbox.curselection()]
        except Exception:
            return []

    def _refresh_app_audio_list(self, restore_titles=None):
        """Repopulate the application audio listbox from visible windows.
        restore_titles: list of titles to pre-select; None = preserve current selection.
        """
        if restore_titles is None:
            old_sel = set(self._get_audio_app_windows())
        else:
            old_sel = set(restore_titles)

        windows = enum_windows_with_pid()
        pid_info = _build_pid_info_map()  # {pid: (parent_pid, exe_lower)}

        # Only show windows whose process has an active WASAPI render session —
        # a prerequisite for Application Loopback capture. Expand to include the
        # direct parent of each session PID (audio workers are often children).
        # If no sessions are active at all, fall back to showing everything.
        active_pids = _get_wasapi_active_pids()
        if active_pids:
            expanded = set(active_pids)
            for spid in active_pids:
                parent = pid_info.get(spid, (0, ""))[0]
                if parent:
                    expanded.add(parent)
        else:
            expanded = set()

        ignored = set(self.prefs.get("ignored_audio_apps",
                                     sorted(_UNSUPPORTED_APP_AUDIO_EXES)))
        self._app_audio_window_map = {}
        self._app_audio_title_to_exe = {}
        entries = []
        seen = set()
        for _hwnd, title, pid in windows:
            if not title or not title.strip() or title in seen:
                continue
            exe = pid_info.get(pid, (0, ""))[1]
            if exe in ignored:
                continue
            if expanded and pid not in expanded:
                continue
            seen.add(title)
            self._app_audio_window_map[title] = pid
            self._app_audio_title_to_exe[title] = exe
            entries.append(title)

        self._app_audio_listbox.delete(0, tk.END)
        for title in entries:
            self._app_audio_listbox.insert(tk.END, title)

        for i, title in enumerate(entries):
            if title in old_sel:
                self._app_audio_listbox.selection_set(i)

        if expanded:
            self._log(f"Found {len(entries)} window(s) with active audio for capture"
                      " (refresh while app is playing to detect it)")
        else:
            self._log(f"Found {len(entries)} open window(s) for audio capture"
                      " (no active audio detected — showing all)")

    def _on_audio_app_right_click(self, event):
        """Right-click context menu on the application audio listbox."""
        lb = self._app_audio_listbox
        idx = lb.nearest(event.y)
        if idx < 0 or idx >= lb.size():
            return
        lb.selection_clear(0, tk.END)
        lb.selection_set(idx)
        title = lb.get(idx)
        exe = self._app_audio_title_to_exe.get(title, "")

        menu = tk.Menu(self, tearoff=0, bg=FRAME_BG, fg=FG,
                       activebackground=ACCENT, activeforeground=BG,
                       relief="flat")

        def _ignore():
            ignored = list(self.prefs.get("ignored_audio_apps",
                                          sorted(_UNSUPPORTED_APP_AUDIO_EXES)))
            if exe and exe not in ignored:
                ignored.append(exe)
                ignored.sort()
                self.prefs["ignored_audio_apps"] = ignored
                save_prefs(self.prefs)
                self._log(f"Ignored: {exe} — hidden from application audio list"
                          " (manage in File → Preferences)")
            self._refresh_app_audio_list()

        if exe:
            menu.add_command(label=f"Ignore '{exe}' — hide from list", command=_ignore)
        else:
            menu.add_command(label="(process not identifiable)", state="disabled")
        menu.add_separator()
        menu.add_command(label="Manage ignored apps…",
                         command=self._show_preferences)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

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
            self.prefs["ffmpeg_path"] = p
            save_prefs(self.prefs)

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
            "output_folder":        self.output_folder_var.get(),
            "file_prefix":          self.prefix_var.get(),
            "audio_enabled":        self.audio_enabled.get(),
            "audio_capture_mode":   self.audio_capture_mode_var.get(),
            "audio_device":         self.audio_device_var.get(),
            "audio_app_windows":    self._get_audio_app_windows(),
            "audio_delay":          int(self.audio_delay_var.get()    or 0),
            "audio_duration":       int(self.audio_duration_var.get() or 0),
            "audio_match_screen":   self.audio_match_var.get(),
            "window_title":         self.window_var.get(),
            "pinup_game_media_dir": self._get_pinup_media_dir(),
            "pinup_game_rom":       self._get_pinup_rom(),
            "pinup_also_save":      self.pinup_also_save_var.get(),
            "coords_v2":            True,
            "screens":              screens,
        }

    # ── Recording ──────────────────────────────────────────────────────────────

    def _start_recording(self):
        self.ffmpeg_path = self._resolve_ffmpeg_path(self.ffmpeg_var.get())
        if not self.ffmpeg_path:
            self._show_ffmpeg_setup()
            return

        # Close any open preview overlays before recording
        for n in list(self._overlays.keys()):
            self._close_overlay(n)

        cfg = self._snapshot_config()
        cfg["open_folder_after"] = self.prefs.get("open_folder_after", False)
        self._save_config(cfg)
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

        # Find the earliest moment any stream starts so we can beep 1s before it.
        enabled_delays = [cfg["screens"][n].get("delay", 0)
                          for n in SCREENS if cfg["screens"][n].get("enabled")]
        if cfg.get("audio_enabled"):
            enabled_delays.append(cfg.get("audio_delay", 0))
        first_start = min(enabled_delays) if enabled_delays else 0

        pre_beep = max(0.0, first_start - 1.0)
        if pre_beep > 0:
            time.sleep(pre_beep)

        try:
            import winsound
            winsound.Beep(880, 200)   # short high beep – recording about to start
        except Exception:
            pass

        post_beep = first_start - pre_beep  # 0.0 or 1.0
        if post_beep > 0:
            time.sleep(post_beep)

        # t_ref marks the logical zero of the recording timeline. Per-screen delays
        # inside _launch_ffmpeg are measured from this point; any time already
        # consumed above is accounted for by setting t_ref = now - first_start.
        t_ref = time.time() - first_start
        self._launch_ffmpeg(cfg, t_ref)

    def _launch_ffmpeg(self, cfg, t_ref=None):
        ffmpeg    = self.ffmpeg_path
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix    = cfg["file_prefix"]
        out_dir   = cfg["output_folder"]
        # t_ref: the logical moment when all delays are measured from.
        # Callers that pre-consume part of the delay (e.g. the start-beep sleep)
        # pass their reference time so remaining per-screen delays are correct.
        t_ref     = t_ref if t_ref is not None else time.time()

        self.processes       = []
        self._recording_files = {}   # screen_name -> output path
        self._recording_cfg   = cfg  # snapshot used for post-recording actions

        import collections
        proc_lock    = threading.Lock()
        ready_events = []

        # ── Video streams ──────────────────────────────────────────────────────
        # Each screen starts in its own thread so their delays run in parallel
        # and each stream begins at the correct time relative to t_ref.
        live_monitors = enum_display_monitors()
        for name in SCREENS:
            s = cfg["screens"][name]
            if not s["enabled"]:
                continue

            # Convert monitor-relative coords to global desktop coords for gdigrab
            mon = self._monitor_by_label(s.get("monitor", ""), live_monitors)
            offset_x = mon["x"] + s["x"]
            offset_y = mon["y"] + s["y"]

            out_file = os.path.join(out_dir, f"{prefix}_{name}_{ts}.mp4")
            cmd = [
                ffmpeg, "-y",
                "-f",          "gdigrab",
                "-framerate",  str(s["fps"]),
                "-offset_x",   str(offset_x),
                "-offset_y",   str(offset_y),
                "-video_size", f"{s['width']}x{s['height']}",
                "-draw_mouse", "0",
                "-i",          "desktop",
            ]
            if s["duration"] > 0:
                cmd += ["-t", str(s["duration"])]
            cmd += [
                "-c:v",      "libx264",
                "-preset",   "ultrafast",
                "-crf",      "18",
                "-pix_fmt",  "yuv420p",
                "-vf",       "crop=trunc(iw/2)*2:trunc(ih/2)*2",
                out_file,
            ]

            ready = threading.Event()
            ready_events.append(ready)

            def _start_screen(sname=name, scfg=s, _cmd=cmd, ofile=out_file, evt=ready):
                dly = scfg.get("delay", 0)
                if dly > 0:
                    remaining = max(0.0, dly - (time.time() - t_ref))
                    if remaining > 0:
                        time.sleep(remaining)
                self._log(f"▶ {sname}  →  {os.path.basename(ofile)}")
                try:
                    proc = self._ffmpeg_popen(
                        _cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                    # Drain stderr in background so the pipe buffer never fills up
                    err_buf = collections.deque(maxlen=120)
                    def _drain(p=proc, b=err_buf):
                        try:
                            for raw in p.stderr:
                                b.append(raw.decode(errors="replace").rstrip())
                        except Exception:
                            pass
                    threading.Thread(target=_drain, daemon=True).start()
                    with proc_lock:
                        self.processes.append({"name": sname, "proc": proc,
                                               "file": ofile, "err_buf": err_buf})
                        self._recording_files[sname] = ofile
                except Exception as e:
                    self._log(f"  ERROR starting {sname}: {e}")
                finally:
                    evt.set()

            threading.Thread(target=_start_screen, daemon=True).start()

        # Wait for all screen launch threads to complete before starting audio
        for evt in ready_events:
            evt.wait(timeout=120)

        # ── Audio stream ──────────────────────────────────────────────────────────
        # Resolve audio delay and duration from config
        aud_delay    = cfg.get("audio_delay", 0)
        aud_duration = cfg.get("audio_duration", 0)
        if aud_duration == 0:
            # Fall back to the longest enabled screen duration (0 = manual stop)
            all_durs     = [cfg["screens"][n].get("duration", 0)
                            for n in SCREENS if cfg["screens"][n].get("enabled")]
            aud_duration = max(all_durs) if all_durs else 0
        duration = aud_duration
        if cfg["audio_enabled"]:
            device        = cfg["audio_device"]
            capture_mode  = cfg.get("audio_capture_mode", "device")
            aud_mp3       = os.path.join(out_dir, f"{prefix}_Audio_{ts}.mp3")
            loopback_meta = getattr(self, "_loopback_meta", {})

            if aud_delay > 0:
                aud_remaining = max(0.0, aud_delay - (time.time() - t_ref))
                if aud_remaining > 0:
                    self._log(f"  delay (audio): {aud_remaining:.0f}s")
                    time.sleep(aud_remaining)

            if capture_mode == "application":
                # ── Application loopback (per-process, full volume) ───────────
                selected_titles = cfg.get("audio_app_windows", [])
                if not selected_titles:
                    self._log("  ⚠ Audio: no applications selected for capture")
                else:
                    windows_now   = enum_windows_with_pid()
                    title_to_pid  = {t: p for _, t, p in windows_now if t.strip()}
                    seen_pids     = set()
                    pid_list      = []
                    for title in selected_titles:
                        pid = title_to_pid.get(title)
                        if not pid:
                            # Window title may have changed (e.g. Discord updates
                            # title with channel name). Fall back to the PID that
                            # was stored when the user made the selection.
                            pid = self._app_audio_window_map.get(title)
                            if pid:
                                self._log(f"  ℹ Audio window title changed; "
                                          f"using stored PID {pid} for: {title!r}")
                        if pid and pid not in seen_pids:
                            seen_pids.add(pid)
                            pid_list.append((pid, title))
                        elif not pid:
                            self._log(f"  ⚠ Window not found for audio: {title!r}")

                    if not pid_list:
                        self._log("  ⚠ No matching windows found for application audio")
                    else:
                        stop_evt  = threading.Event()
                        wav_files = []
                        threads   = []

                        for i, (pid, title) in enumerate(pid_list):
                            root_pid = _find_root_audio_pid(pid)
                            if root_pid != pid:
                                self._log(f"  resolved PID {pid} → root PID {root_pid}")
                            wav_path = aud_mp3.replace(".mp3", f"_appraw{i}.wav")
                            wav_files.append(wav_path)
                            pid = root_pid
                            self._log(f"▶ Audio [{title}]  →  PID {pid}")

                            def _capture_app(p=pid, w=wav_path,
                                             se=stop_evt, dur=duration):
                                try:
                                    _app_loopback_subprocess(
                                        p, w, se, dur,
                                        on_log=lambda m: self.after(0, self._log, m))
                                except Exception as exc:
                                    self.after(0, self._log,
                                               f"  ⚠ App loopback error (PID {p}): {exc}")

                            t = threading.Thread(target=_capture_app, daemon=True)
                            t.start()
                            threads.append(t)

                        def _coordinator(ts=threads):
                            for th in ts:
                                th.join(timeout=120)

                        coord = threading.Thread(target=_coordinator, daemon=True)
                        coord.start()

                        self._log(f"▶ Audio  →  {os.path.basename(aud_mp3)}")
                        self._log(f"  mode: application loopback "
                                  f"({len(pid_list)} process(es))")
                        self.processes.append({
                            "name":         "Audio",
                            "proc":         None,
                            "file":         aud_mp3,
                            "wav_files":    wav_files,
                            "stop_evt":     stop_evt,
                            "thread":       coord,
                            "ffmpeg":       ffmpeg,
                            "err_buf":      [],
                            "capture_mode": "application",
                        })
                        self._recording_files["Audio"] = aud_mp3

            elif device in loopback_meta:
                # ── WASAPI loopback via pyaudiowpatch ────────────────────────
                self._log(f"▶ Audio  →  {os.path.basename(aud_mp3)}")
                self._log(f"  device: {device}")
                dev_idx, channels, sample_rate = loopback_meta[device]
                aud_wav = aud_mp3.replace(".mp3", "_raw.wav")
                stop_evt = threading.Event()

                def _record_loopback(wav_path=aud_wav, idx=dev_idx,
                                     ch=channels, rate=sample_rate,
                                     dur=duration, stop=stop_evt):
                    import pyaudiowpatch as pyaudio
                    import wave
                    CHUNK = 1024
                    try:
                        pa = pyaudio.PyAudio()
                        frames = []

                        # Callback-mode capture: PortAudio fills frames on its own
                        # thread so our loop never blocks. This means stop_evt
                        # terminates the thread within one sleep(0.05) cycle instead
                        # of blocking indefinitely inside stream.read().
                        def _cb(in_data, frame_count, time_info, status):
                            frames.append(in_data)
                            return (None, pyaudio.paContinue)

                        stream = pa.open(format=pyaudio.paInt16,
                                         channels=ch,
                                         rate=rate,
                                         input=True,
                                         input_device_index=idx,
                                         frames_per_buffer=CHUNK,
                                         stream_callback=_cb)
                        stream.start_stream()
                        t0 = time.time()
                        try:
                            while not stop.is_set() and stream.is_active():
                                if dur > 0 and (time.time() - t0) >= dur:
                                    break
                                time.sleep(0.05)
                        finally:
                            stream.stop_stream()
                            stream.close()
                            pa.terminate()

                        if not frames:
                            self.after(0, self._log,
                                       "  ⚠ No audio data captured – device may not "
                                       "be producing audio (check device selection)")
                        with wave.open(wav_path, "wb") as wf:
                            wf.setnchannels(ch)
                            wf.setsampwidth(2)  # paInt16 = 2 bytes
                            wf.setframerate(rate)
                            wf.writeframes(b"".join(frames))
                        self.after(0, self._log,
                                   f"  WAV captured: {len(frames)} chunk(s), "
                                   f"{os.path.getsize(wav_path):,} bytes")
                    except Exception as exc:
                        self.after(0, self._log, f"  ⚠ WASAPI capture error: {exc}")

                t = threading.Thread(target=_record_loopback, daemon=True)
                t.start()
                self._log(f"  mode: WASAPI loopback")
                self.processes.append({"name": "Audio", "proc": None,
                                       "file": aud_mp3, "wav_file": aud_wav,
                                       "stop_evt": stop_evt, "thread": t,
                                       "ffmpeg": ffmpeg, "err_buf": []})
                self._recording_files["Audio"] = aud_mp3
            else:
                # ── dshow fallback (Stereo Mix / microphone) ──────────────────
                self._log(f"▶ Audio  →  {os.path.basename(aud_mp3)}")
                self._log(f"  device: {device or '(system default)'}")
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
                self._log(f"  mode: dshow")
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
                    self._recording_files["Audio"] = aud_mp3
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

            if entry.get("capture_mode") == "application":  # ── app loopback ──
                t = entry.get("thread")
                if t:
                    t.join(timeout=30)
                wav_files  = entry.get("wav_files", [])
                ffmpeg_bin = entry.get("ffmpeg", "ffmpeg")
                existing   = [w for w in wav_files
                              if os.path.exists(w) and os.path.getsize(w) > 0]
                if not existing:
                    self.after(0, self._log,
                               "  ⚠ No WAV data captured from any application")
                elif len(existing) == 1:
                    self.after(0, self._log, "  Converting WAV → MP3…")
                    try:
                        r = subprocess.run(
                            [ffmpeg_bin, "-y", "-i", existing[0], "-q:a", "2", path],
                            capture_output=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        if r.returncode != 0:
                            err_txt = r.stderr.decode(errors="replace").strip()
                            self.after(0, self._log,
                                       f"  ⚠ WAV→MP3 failed: "
                                       f"{err_txt[-200:] if err_txt else '(no output)'}")
                    except Exception as exc:
                        self.after(0, self._log, f"  WAV→MP3 error: {exc}")
                else:
                    self.after(0, self._log,
                               f"  Mixing {len(existing)} stream(s) → MP3…")
                    mix_cmd = [ffmpeg_bin, "-y"]
                    for w in existing:
                        mix_cmd += ["-i", w]
                    mix_cmd += [
                        "-filter_complex",
                        f"amix=inputs={len(existing)}:duration=longest:normalize=0",
                        "-q:a", "2", path,
                    ]
                    try:
                        r = subprocess.run(mix_cmd, capture_output=True, timeout=120,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                        if r.returncode != 0:
                            err_txt = r.stderr.decode(errors="replace").strip()
                            self.after(0, self._log,
                                       f"  ⚠ WAV mix failed: "
                                       f"{err_txt[-200:] if err_txt else '(no output)'}")
                    except Exception as exc:
                        self.after(0, self._log, f"  WAV mix error: {exc}")
                for w in wav_files:
                    try:
                        os.remove(w)
                    except Exception:
                        pass

            elif entry.get("stop_evt"):  # ── WASAPI device loopback ──
                t = entry.get("thread")
                if t:
                    t.join(timeout=15)
                # Convert WAV → MP3 with FFmpeg
                wav = entry.get("wav_file", "")
                ffmpeg_bin = entry.get("ffmpeg", "ffmpeg")
                if wav and os.path.exists(wav):
                    self.after(0, self._log, "  Converting WAV → MP3…")
                    try:
                        r = subprocess.run(
                            [ffmpeg_bin, "-y", "-i", wav, "-q:a", "2", path],
                            capture_output=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        if r.returncode != 0:
                            err_txt = r.stderr.decode(errors="replace").strip()
                            self.after(0, self._log,
                                       f"  ⚠ WAV→MP3 failed (code {r.returncode}): "
                                       f"{err_txt[-200:] if err_txt else '(no output)'}")
                    except Exception as exc:
                        self.after(0, self._log, f"  WAV→MP3 error: {exc}")
                    finally:
                        try:
                            os.remove(wav)
                        except Exception:
                            pass
                else:
                    self.after(0, self._log,
                               "  ⚠ WAV file not found – WASAPI capture likely failed")
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

        # Play a completion sound so the user notices even if the window is in the background
        try:
            import winsound
            for freq, dur in [(523, 150), (659, 150), (784, 300)]:
                winsound.Beep(freq, dur)
        except Exception:
            pass

        rec_cfg = self._recording_cfg
        # Move files to PinUP capture folder structure if configured
        if (rec_cfg.get("pinup_game_rom") and
                rec_cfg.get("pinup_game_media_dir") and
                self._recording_files):
            self.after(0, self._move_to_pinup)
        elif rec_cfg.get("open_folder_after"):
            out_dir = rec_cfg.get("output_folder", "")
            if out_dir and os.path.isdir(out_dir):
                try:
                    os.startfile(out_dir)
                except Exception:
                    pass

        # In headless/CLI mode, auto-close after recording completes
        if getattr(self, "_headless", False):
            self.after(1500, self._on_close)

    # ── Close ──────────────────────────────────────────────────────────────────

    def _on_close(self):
        if self.recording:
            self._stop_recording()
            time.sleep(1.5)
        try:
            self._save_config(self._snapshot_config())
        except Exception:
            pass
        if self._log_fh:
            try:
                self._log_fh.close()
            except Exception:
                pass
        self.destroy()


# ─── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Internal audio-capture subprocess mode ─────────────────────────────────
    # Invoked by _app_loopback_subprocess to run capture in a fresh process.
    # argv: <script> --capture-audio <pid> <wav_path> <duration> <stop_file>
    if len(sys.argv) >= 6 and sys.argv[1] == "--capture-audio":
        import time as _time_mod

        _pid      = int(sys.argv[2])
        _wav      = sys.argv[3]
        _dur      = float(sys.argv[4])
        _stopfile = sys.argv[5]

        _stop = threading.Event()

        def _watch_stop():
            while not _stop.is_set():
                if os.path.exists(_stopfile):
                    _stop.set()
                    return
                _time_mod.sleep(0.25)

        threading.Thread(target=_watch_stop, daemon=True).start()

        def _log(m): print(m, flush=True)

        # Try the specified PID first; if the Windows audio engine returns
        # ERROR_FILE_NOT_FOUND (0x80070002) it means no active loopback endpoint
        # exists for that process tree.  This can happen when:
        #   - the target process hasn't produced audio yet (session not yet open)
        #   - the app's Audio Service runs under a different child PID
        # Fallback: try each direct child process in order.
        _ERROR_NOT_FOUND = "0x80070002"
        _pids_to_try = [_pid] + _get_child_pids(_pid)
        _last_err = None
        _captured = False

        for _try_pid in _pids_to_try:
            if _stop.is_set():
                break
            if _try_pid != _pid:
                _log(f"  trying child PID {_try_pid}")
            try:
                _app_loopback_capture(_try_pid, _wav, _stop, _dur, on_log=_log)
                _captured = True
                break
            except RuntimeError as _e:
                _last_err = str(_e)
                if _ERROR_NOT_FOUND not in _last_err:
                    break  # unexpected error — don't try other PIDs

        if not _captured and _last_err and not _stop.is_set():
            if _ERROR_NOT_FOUND in _last_err:
                _log("  ! No active audio session found for this app or its children.")
                _log("    Ensure the application is actively playing audio when recording starts.")
                _log("    For browser audio, try WASAPI loopback mode instead.")
            else:
                _log(f"  ERROR: {_last_err}")

        # os._exit skips Python/tkinter cleanup that can block indefinitely
        # when Tcl/Tk is imported but never fully initialised in this process.
        os._exit(0)

    import argparse

    parser = argparse.ArgumentParser(
        description="Pinball Screen Recorder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  PinballRecorder.exe\n"
            "      Open GUI with saved settings\n"
            "  PinballRecorder.exe --config my.json\n"
            "      Open GUI pre-loaded with my.json\n"
            "  PinballRecorder.exe --config my.json --autostart\n"
            "      Headless: record using my.json and exit when done\n"
            "  PinballRecorder.exe --autostart --rom mygame --duration 30\n"
            "      Headless: record all screens for 30s using saved config, tag as ROM 'mygame'\n"
            "  PinballRecorder.exe --autostart --screen-playfield-enabled 0 --screen-fulldmd-enabled 0 --duration 20\n"
            "      Headless: record Backglass only for 20s\n"
        ),
    )

    # ── Config file ────────────────────────────────────────────────────────────
    parser.add_argument("--config", metavar="PATH",
                        help="Base JSON config file. CLI args override individual values.")
    parser.add_argument("--autostart", action="store_true",
                        help="Start recording immediately and exit when done.")

    # ── Top-level config overrides ─────────────────────────────────────────────
    parser.add_argument("--output-folder", metavar="PATH",
                        help="Output folder for recordings.")
    parser.add_argument("--file-prefix", metavar="STR",
                        help="Filename prefix for recorded files.")
    parser.add_argument("--rom", metavar="NAME",
                        help="PinUP ROM/game name (selects the PinUP table and output folder).")
    parser.add_argument("--window-title", metavar="STR",
                        help="Window title for window-focus capture mode.")

    # ── Global duration / delay shorthand (applies to all enabled screens + audio) ──
    parser.add_argument("--delay", metavar="SECS", type=float,
                        help="Recording start delay in seconds for all enabled screens.")
    parser.add_argument("--duration", metavar="SECS", type=float,
                        help="Recording duration in seconds for all enabled screens.")

    # ── Audio overrides ────────────────────────────────────────────────────────
    parser.add_argument("--audio-enabled", metavar="0|1", type=int, choices=[0, 1],
                        help="Enable (1) or disable (0) audio recording.")
    parser.add_argument("--audio-device", metavar="NAME",
                        help="Audio capture device name or substring.")
    parser.add_argument("--audio-delay", metavar="SECS", type=float,
                        help="Audio recording start delay in seconds.")
    parser.add_argument("--audio-duration", metavar="SECS", type=float,
                        help="Audio recording duration in seconds.")

    # ── Per-screen overrides: --screen-<name>-<field> ─────────────────────────
    # Supported fields: enabled, x, y, width, height, fps, delay, duration
    for _sname in ("playfield", "backglass", "fulldmd"):
        g = parser.add_argument_group(f"{_sname} screen")
        g.add_argument(f"--screen-{_sname}-enabled", metavar="0|1", type=int, choices=[0, 1],
                       help=f"Enable (1) or disable (0) the {_sname} screen.")
        g.add_argument(f"--screen-{_sname}-x",        metavar="PX",   type=int)
        g.add_argument(f"--screen-{_sname}-y",        metavar="PX",   type=int)
        g.add_argument(f"--screen-{_sname}-width",    metavar="PX",   type=int)
        g.add_argument(f"--screen-{_sname}-height",   metavar="PX",   type=int)
        g.add_argument(f"--screen-{_sname}-fps",      metavar="FPS",  type=int)
        g.add_argument(f"--screen-{_sname}-delay",    metavar="SECS", type=float)
        g.add_argument(f"--screen-{_sname}-duration", metavar="SECS", type=float)

    args = parser.parse_args()

    # ── Build config: start from file (or saved default), then apply CLI args ──
    cli_cfg = None
    if args.config:
        try:
            with open(args.config) as f:
                cli_cfg = json.load(f)
        except Exception as e:
            print(f"ERROR: Could not load config '{args.config}': {e}")
            sys.exit(1)
    elif args.autostart:
        # Headless without a config file: start from the saved/default config
        cli_cfg = load_config()

    if cli_cfg is not None:
        cli_cfg = _deep_merge_config(cli_cfg)

        # Apply top-level overrides
        if args.output_folder:
            cli_cfg["output_folder"] = args.output_folder
        if args.file_prefix:
            cli_cfg["file_prefix"] = args.file_prefix
        if args.rom:
            cli_cfg["pinup_game_rom"] = args.rom
        if args.window_title:
            cli_cfg["window_title"] = args.window_title

        # Global delay/duration shorthand — applies to all enabled screens
        if args.delay is not None:
            for sname in SCREENS:
                cli_cfg["screens"][sname]["delay"] = args.delay
        if args.duration is not None:
            for sname in SCREENS:
                cli_cfg["screens"][sname]["duration"] = args.duration

        # Audio overrides
        if args.audio_enabled is not None:
            cli_cfg["audio_enabled"] = bool(args.audio_enabled)
        if args.audio_device:
            cli_cfg["audio_device"] = args.audio_device
        if args.audio_delay is not None:
            cli_cfg["audio_delay"] = args.audio_delay
        if args.audio_duration is not None:
            cli_cfg["audio_duration"] = args.audio_duration

        # Per-screen overrides
        _screen_cli_map = {"playfield": "Playfield", "backglass": "Backglass", "fulldmd": "FullDMD"}
        for _slug, _sname in _screen_cli_map.items():
            for _field in ("enabled", "x", "y", "width", "height", "fps", "delay", "duration"):
                _val = getattr(args, f"screen_{_slug}_{_field}", None)
                if _val is not None:
                    if _field == "enabled":
                        cli_cfg["screens"][_sname][_field] = bool(_val)
                    else:
                        cli_cfg["screens"][_sname][_field] = _val

    app = PinballRecorder(cli_config=cli_cfg, headless=args.autostart)
    app.mainloop()
