"""Return keyboard focus after a viewer launched by the picker opens on Windows."""

import ctypes
import sys
from ctypes import wintypes


def foreground_owner():
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return pid.value


def bring_to_foreground(root, focus_widget=None):
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        hwnd = user32.GetAncestor(wintypes.HWND(root.winfo_id()), 2) or root.winfo_id()
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    (focus_widget or root).focus_force()


def hand_back_after_launch(root, viewer_pid, initiated_in_picker, focus_widget=None):
    """Watch a short startup window; never steal focus from another app."""
    if not initiated_in_picker:
        return
    for delay in (100, 300, 700, 1300, 2200):
        def check():
            if foreground_owner() == viewer_pid:
                bring_to_foreground(root, focus_widget)

        root.after(delay, check)
