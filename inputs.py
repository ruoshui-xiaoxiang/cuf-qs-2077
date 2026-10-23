"""inputs.py —— 操作系统级输入（SendInput），不注入 DLL、不碰内存

边界（写进代码，不靠自觉）：
- 只允许白名单键与鼠标相对移动/左键；ESC、ALT、WIN、CTRL、F 功能键、
  PrintScreen 一律拒绝——这些会切出游戏、开菜单或触发截图 UI。
- 不 import pyautogui（本机没装），只用 ctypes，零第三方依赖。
- ⚠ UE4 用 Raw Input 读鼠标视角时，**可能忽略 SendInput 注入的相对位移**。
  这一条必须在真游戏里实测，不能在这里假设成立。selftest 只证明"注入被系统接受"
  （光标动了），不证明"游戏会认"。
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

INPUT_KEYBOARD, INPUT_MOUSE = 1, 0
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


# 动作字典：模型只能用这些名字，名字里不含 vk 码，避免它自己拼键
ACTIONS = {
    "fwd":   dict(vk=0x57),                       # W
    "back":  dict(vk=0x53),                       # S
    "left":  dict(vk=0x41),                       # A
    "right": dict(vk=0x44),                       # D
    "sprint": dict(vk=0x02, extended=True),       # Shift
    "jump":  dict(vk=0x20),                       # Space
    "interact": dict(vk=0x45),                    # E
    "dodge": dict(vk=0x51),                       # Q
    "attack": dict(vk=0x4C),                      # LMB 由 click 承担，这里留 L 位
    # ↓ 10-23 为「盘技能」扩的候选位。哪个真能放出来由 probe_moves.py 实测决定，
    #   不靠我背键位表——背错就是把空动画报成技能。
    "key_r": dict(vk=0x52), "key_f": dict(vk=0x46), "key_v": dict(vk=0x56),
    "key_b": dict(vk=0x42), "key_c": dict(vk=0x43), "key_x": dict(vk=0x58),
    "key_g": dict(vk=0x47), "key_t": dict(vk=0x54),
    "f3": dict(vk=0x72),   # UE4SS LineTraceMod 绑定键（只读工具）
    "key_1": dict(vk=0x31), "key_2": dict(vk=0x32),
    "key_3": dict(vk=0x33), "key_4": dict(vk=0x34), "key_5": dict(vk=0x35),
}
MOUSE_ACTIONS = ("look", "click", "click_right")
DENY_HINT = ("禁止键一律不可用：ESC/ALT/WIN/CTRL/F1-F12/PRTSC/"
             "任务管理器组合键——会切出游戏或打开会污染画面的 UI")


def _send(*inputs):
    return user32.SendInput(len(inputs), ctypes.byref(inputs[0]),
                            ctypes.sizeof(INPUT)) == len(inputs)


def tap_key(name, hold_s=0.08):
    spec = ACTIONS.get(name)
    if spec is None:
        raise ValueError("动作 %r 不在白名单。%s" % (name, DENY_HINT))
    vk, ext = spec["vk"], 1 if spec.get("extended") else 0
    down = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(
        vk, 0, ext, 0, None)))
    up = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(
        vk, 0, ext | KEYEVENTF_KEYUP, 0, None)))
    ok = _send(down)
    time.sleep(hold_s)
    return ok and _send(up)


def hold_key(name, hold_s=0.6):
    """按住走位（剑星里 W 需要持续按住才走，tap 只能挪半步）。"""
    spec = ACTIONS.get(name)
    if spec is None:
        raise ValueError("动作 %r 不在白名单。%s" % (name, DENY_HINT))
    vk, ext = spec["vk"], 1 if spec.get("extended") else 0
    down = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(vk, 0, ext, 0, None)))
    up = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(
        vk, 0, ext | KEYEVENTF_KEYUP, 0, None)))
    if not _send(down):
        return False
    time.sleep(hold_s)
    return _send(up)


def look_rel(dx, dy):
    """鼠标相对转向。dx/dy 会被夹到 ±400，防止一次甩过头。"""
    dx = max(-400, min(400, int(dx)))
    dy = max(-400, min(400, int(dy)))
    ev = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, None)))
    return _send(ev)


def click_left():
    d = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)))
    u = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)))
    return _send(d) and _send(u)


def click_right(hold_s=0.06):
    """右键（剑星默认＝重击/蓄力类）。hold_s 控制按住时长，
    有些重击要按住才起手，所以别写死成一次点击。"""
    d = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_RIGHTDOWN, 0, None)))
    u = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_RIGHTUP, 0, None)))
    ok = _send(d)
    time.sleep(hold_s)
    return ok and _send(u)


def cursor_pos():
    """自检用：证明注入被系统接受（光标动了），不代表游戏认。"""
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _key(vk, down):
    flags = 0 if down else KEYEVENTF_KEYUP
    if vk in (0x09, 0x20, 0x31, 0x32, 0x33, 0x34):    # Tab/Space/数字 需扩展码的少，统一不加
        flags |= 0
    return INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(vk, 0, flags, 0, None)))


def reset_training():
    """TAB+1 = 训练场「重新开始」。只开这一个组合键。

    刻意不开通用地 chord()：TAB+H 会隐藏 HUD（我就读不到血条了）、
    TAB+C 直接结束训练、TAB+2/3/4 改无敌人与能量设定——
    那几个都会**改变实验条件**，需要人来决定，不该是我随手能按的。
    """
    ok = _send(_key(0x09, True))                       # Tab down
    time.sleep(0.03)
    ok = _send(_key(0x31, True)) and ok                # 1 down
    time.sleep(0.06)
    ok = _send(_key(0x31, False)) and ok               # 1 up
    ok = _send(_key(0x09, False)) and ok               # Tab up
    return ok, "TAB+1 reset"


def _chord(vk_mod, vk_key, mod_ext=False):
    ok = _send(_key(vk_mod, True))
    time.sleep(0.03)
    ok = _send(_key(vk_key, True)) and ok
    time.sleep(0.06)
    ok = _send(_key(vk_key, False)) and ok
    return _send(_key(vk_mod, False)) and ok


def dump_actors():
    """Ctrl+7 = UE4SS DumpAllActors（只读，产物落在 ue4ss 目录）。
    刻意只开这一个组合：它是排障工具，不改变游戏状态。"""
    return _chord(0x11, 0x67), "CTRL+7 DumpAllActors"      # VK_CONTROL, VK_NUMPAD7
