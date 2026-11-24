"""OS signals the agent needs: foreground window, idle time, lock state.

Only Windows is implemented. Tests use a fake with the same three methods.
"""

from __future__ import annotations

import os
import sys
from typing import NamedTuple, Protocol

Rect = tuple[int, int, int, int]  # left, top, right, bottom in physical pixels


class Foreground(NamedTuple):
    process: str  # e.g. "Code.exe"
    title: str
    monitor_rect: Rect | None


class System(Protocol):
    def foreground(self) -> Foreground | None: ...
    def idle_seconds(self) -> float: ...
    def is_locked(self) -> bool: ...
    def visible_windows(self) -> list[Foreground]: ...


def make_system() -> System:
    if sys.platform != "win32":
        raise RuntimeError("Focus tracker currently supports Windows only.")
    return WindowsSystem()


class WindowsSystem:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ct = ctypes
        self._wt = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32, self._kernel32 = user32, kernel32

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        self._LASTINPUTINFO, self._MONITORINFO = LASTINPUTINFO, MONITORINFO

        # Explicit signatures: without them 64-bit handles get truncated.
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetLastInputInfo.restype = wintypes.BOOL
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user32.GetUserObjectInformationW.restype = wintypes.BOOL
        user32.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.CloseDesktop.restype = wintypes.BOOL
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        self._WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.restype = wintypes.BOOL
        user32.EnumWindows.argtypes = [self._WNDENUMPROC, wintypes.LPARAM]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        try:
            dwmapi = ctypes.WinDLL("dwmapi")
            dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
            dwmapi.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ]
            self._dwmapi = dwmapi
        except OSError:
            self._dwmapi = None
        kernel32.GetTickCount.restype = wintypes.DWORD
        kernel32.GetTickCount.argtypes = []
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        self._set_dpi_aware()

    def _set_dpi_aware(self) -> None:
        """Use physical pixels so window rects match what mss captures."""
        ct = self._ct
        try:
            fn = self._user32.SetProcessDpiAwarenessContext
            fn.restype = self._wt.BOOL
            fn.argtypes = [ct.c_void_p]
            if fn(ct.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
                return
        except AttributeError:
            pass
        try:
            ct.WinDLL("shcore").SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass

    def foreground(self) -> Foreground | None:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        return self._describe(hwnd)

    def _describe(self, hwnd: int) -> Foreground:
        ct, wt, user32 = self._ct, self._wt, self._user32
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ct.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ct.byref(pid))
        return Foreground(self._process_name(pid.value), buf.value, self._monitor_rect(hwnd))

    def visible_windows(self) -> list[Foreground]:
        """Every visible, non-minimised, non-cloaked top-level window with a title.

        Occluded windows are included on purpose: better to skip a screenshot than
        to capture a bank page peeking out from behind another window.
        """
        user32 = self._user32
        hwnds: list[int] = []

        def collect(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd) and not self._cloaked(hwnd):
                if user32.GetWindowTextLengthW(hwnd) > 0:
                    hwnds.append(hwnd)
            return True

        user32.EnumWindows(self._WNDENUMPROC(collect), 0)
        return [self._describe(h) for h in hwnds]

    def _cloaked(self, hwnd: int) -> bool:
        """Windows on other virtual desktops and suspended UWP apps are 'cloaked'."""
        if self._dwmapi is None:
            return False
        ct, wt = self._ct, self._wt
        value = wt.DWORD()
        hr = self._dwmapi.DwmGetWindowAttribute(hwnd, 14, ct.byref(value), ct.sizeof(value))
        return hr == 0 and value.value != 0

    def _process_name(self, pid: int) -> str:
        ct, wt, k32 = self._ct, self._wt, self._kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return f"pid:{pid}"
        try:
            size = wt.DWORD(1024)
            buf = ct.create_unicode_buffer(1024)
            if k32.QueryFullProcessImageNameW(handle, 0, buf, ct.byref(size)):
                return os.path.basename(buf.value)
            return f"pid:{pid}"
        finally:
            k32.CloseHandle(handle)

    def _monitor_rect(self, hwnd: int) -> Rect | None:
        ct = self._ct
        hmon = self._user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not hmon:
            return None
        info = self._MONITORINFO()
        info.cbSize = ct.sizeof(self._MONITORINFO)
        if not self._user32.GetMonitorInfoW(hmon, ct.byref(info)):
            return None
        r = info.rcMonitor
        return (r.left, r.top, r.right, r.bottom)

    def idle_seconds(self) -> float:
        ct = self._ct
        lii = self._LASTINPUTINFO()
        lii.cbSize = ct.sizeof(self._LASTINPUTINFO)
        if not self._user32.GetLastInputInfo(ct.byref(lii)):
            return 0.0
        now = self._kernel32.GetTickCount()
        return ((now - lii.dwTime) & 0xFFFFFFFF) / 1000.0  # handles 49.7-day wraparound

    def is_locked(self) -> bool:
        """True on the lock screen, sign-in screen, or UAC secure desktop.

        When the session is locked the input desktop is "Winlogon" (or cannot be
        opened at all), not "Default".
        """
        ct, wt, user32 = self._ct, self._wt, self._user32
        desk = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not desk:
            return True
        try:
            buf = ct.create_unicode_buffer(256)
            needed = wt.DWORD()
            ok = user32.GetUserObjectInformationW(desk, 2, buf, ct.sizeof(buf), ct.byref(needed))
            return (not ok) or buf.value.lower() != "default"
        finally:
            user32.CloseDesktop(desk)
