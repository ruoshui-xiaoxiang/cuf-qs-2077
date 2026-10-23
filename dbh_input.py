"""dbh_input.py —— 底特律专用的扫描码输入

为什么单独一份：实测《底特律》**只认扫描码**（SendInput + wVk 完全被忽略，
画面帧差 0.007），扫描码注入正常（帧差 0.47）。剑星那边用 VK 就行，
所以不改 inputs.py，单独给底特律一套，避免互相影响。

用法：
    import dbh_input as di
    di.focus()
    di.hold('W', 1.5)
    di.tap('E')
    di.click('right')
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

SCAN = {
    "ESC": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05,
    "TAB": 0x0F, "Q": 0x10, "W": 0x11, "E": 0x12, "R": 0x13,
    "A": 0x1E, "S": 0x1F, "D": 0x20, "F": 0x21,
    "SHIFT": 0x2A, "CTRL": 0x1D, "SPACE": 0x39, "ENTER": 0x1C,
    "UP": 0x48, "DOWN": 0x50, "LEFT": 0x4B, "RIGHT": 0x4D,
}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _key(scan, down):
    ev = INPUT(type=1, u=_U(ki=KEYBDINPUT(0, scan, 0x0008 if down else 0x000A, 0, None)))
    return user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(INPUT)) == 1


def key_down(name):
    return _key(SCAN[name.upper()], True)


def key_up(name):
    return _key(SCAN[name.upper()], False)


def tap(name, hold=0.06):
    key_down(name)
    time.sleep(hold)
    key_up(name)


def hold(name, seconds):
    key_down(name)
    time.sleep(seconds)
    key_up(name)


def chord(names, seconds):
    for n in names:
        key_down(n)
        time.sleep(0.02)
    time.sleep(seconds)
    for n in reversed(names):
        key_up(n)
        time.sleep(0.02)


def click(button="left"):
    down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
    user32.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(up, 0, 0, 0, 0)


def find_window(title="Detroit"):
    out = []

    def _cb(h, l):
        if user32.IsWindowVisible(h):
            n = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, n, 256)
            if title.lower() in n.value.lower():
                out.append(h)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                          ctypes.c_void_p)(_cb), 0)
    return out[0] if out else None


def focus(title="Detroit"):
    hwnd = find_window(title)
    if not hwnd:
        return False, None
    fg = user32.GetForegroundWindow()
    if fg != hwnd:
        tid_fg = user32.GetWindowThreadProcessId(fg, None)
        tid_cur = ctypes.windll.kernel32.GetCurrentThreadId()
        if tid_fg and tid_fg != tid_cur:
            user32.AttachThreadInput(tid_cur, tid_fg, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if tid_fg and tid_fg != tid_cur:
            user32.AttachThreadInput(tid_cur, tid_fg, False)
        time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd, hwnd
