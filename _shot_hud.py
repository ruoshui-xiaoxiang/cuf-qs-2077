# -*- coding: utf-8 -*-
"""Capture a visible CUF-QS* window via PrintWindow -> BMP -> PNG.

Usage: python _shot_hud.py <out_png_path> [title_filter]
Default title_filter matches any window whose title contains 'CUF-QS'.
If multiple match, captures the first enumerated one; prints all matches.
"""
import ctypes
import struct
import sys
from ctypes import wintypes

out_path = sys.argv[1] if len(sys.argv) > 1 else r"e:\6a8b0dd1b3ead574d77ceda3\_shot.png"
needle = sys.argv[2] if len(sys.argv) > 2 else "CUF-QS"

u32 = ctypes.windll.user32
gdi = ctypes.windll.gdi32


class BMI(ctypes.Structure):
    _pack_ = 1
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPels', wintypes.LONG),
                ('biYPels', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
captured = []


def capture(hwnd):
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u32.GetDC(hwnd)
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(mem, bmp)
    u32.PrintWindow(hwnd, mem, 2)
    bmi = BMI(40, w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    hdr = b'BM' + struct.pack('<IHHI', 54 + w * h * 4, 0, 0, 54)
    bmp_path = out_path + ".bmp"
    with open(bmp_path, "wb") as f:
        f.write(hdr + bytes(bmi) + buf.raw)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    u32.ReleaseDC(hwnd, hdc)
    # BMP -> PNG via PIL if available, else keep BMP
    try:
        from PIL import Image
        Image.open(bmp_path).save(out_path)
    except ImportError:
        import subprocess
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Add-Type -AssemblyName System.Drawing; "
                        "$i=[System.Drawing.Image]::FromFile('%s');"
                        "$i.Save('%s',[System.Drawing.Imaging.ImageFormat]::Png);"
                        "$i.Dispose()" % (bmp_path.replace("'", "''"),
                                          out_path.replace("'", "''"))],
                       check=True)
    import os
    if os.path.exists(out_path) and os.path.exists(bmp_path):
        os.remove(bmp_path)
    print("captured %dx%d -> %s" % (w, h, out_path))


@EnumWindowsProc
def cb(hwnd, lp):
    if u32.IsWindowVisible(hwnd):
        n = u32.GetWindowTextLengthW(hwnd)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            t = buf.value
            if needle in t:
                r = wintypes.RECT()
                u32.GetWindowRect(hwnd, ctypes.byref(r))
                print("found:", t, (r.left, r.top, r.right - r.left, r.bottom - r.top))
                if not captured:
                    captured.append(hwnd)
    return True


u32.EnumWindows(cb, 0)
if captured:
    capture(captured[0])
else:
    print("no window matching:", needle)
    sys.exit(1)
