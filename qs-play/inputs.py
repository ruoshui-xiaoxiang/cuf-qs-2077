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
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040


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
    # 10-24 游戏内「键盘与鼠标」键位页实拍：突进（按住）与闪躲都是 Shift。
    # 之前写的 0x02 是右键，不是 Shift——那轮“Shift 冲刺无效”的结论作废。
    # 关键：游戏只认 Shift 的扫描码(0x2A)，纯 VK 注入不生效（实测 22cm vs 214cm）。
    "sprint": dict(vk=0x10, scan=0x2A),           # Shift 按住 = 突进
    "jump":  dict(vk=0x20),                       # Space
    # 10-24 键位页：E 格挡/招架、Q 药水、F 互动、R 物品、C 取消/行走、T 特殊模式。
    "guard": dict(vk=0x45),                       # E = 格挡/招架
    "interact": dict(vk=0x46),                    # F = 互动
    "dodge": dict(vk=0x10, scan=0x2A),            # Shift 点按 = 闪躲（与冲刺同键）
    "item": dict(vk=0x52),                        # R = 使用/选择物品
    "potion": dict(vk=0x51),                      # Q = 使用/选择药水
    "cancel": dict(vk=0x43),                      # C = 取消动作
    "walk": dict(vk=0x43),                        # C = 行走切换
    "special_mode": dict(vk=0x54),                # T = 特殊模式
    "attack": dict(vk=0x4C),                      # LMB 由 click 承担，这里留 L 位
    # UI 快捷键（10-24 键位页）；只给专项脚本，不进模型动作表
    "map": dict(vk=0x4D), "skills": dict(vk=0x4B), "inventory": dict(vk=0x49),
    "quests": dict(vk=0x4A), "hint": dict(vk=0x48), "photo": dict(vk=0x50),
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

# 组合键：Shift 按住 + 方向 = 突进（冲刺）；Shift 单点 = 闪躲。
# 模型只能按名字选，拼键仍然由这里决定；每个成员都必须在 ACTIONS 白名单里。
# 方向必须先按：单独先按 Shift 会被判成“闪躲”，不是冲刺。
COMBOS = {
    "sprint_fwd":   ("fwd", "sprint"),
    "sprint_back":  ("back", "sprint"),
    "sprint_left":  ("left", "sprint"),
    "sprint_right": ("right", "sprint"),
}
# 给模型看的动作名：只放战斗/移动/交互，不放地图/背包/拍照等 UI 键，
# 避免它在战斗中把菜单打开。UI 键仍可由专项脚本调用。
MODEL_ACTIONS = tuple(sorted({
    "fwd", "back", "left", "right", "jump", "guard", "dodge", "attack",
    "interact", "item", "potion", "special_mode", "scan",
    "click", "click_right", "look",
} | set(COMBOS)))
DENY_HINT = ("禁止键一律不可用：ESC/ALT/WIN/CTRL/F1-F12/PRTSC/"
             "任务管理器组合键——会切出游戏或打开会污染画面的 UI")


def _send(*inputs):
    return user32.SendInput(len(inputs), ctypes.byref(inputs[0]),
                            ctypes.sizeof(INPUT)) == len(inputs)


def find_game_window(title="StellarBlade"):
    """按标题找游戏主窗口（只调 user32，不碰游戏进程）。"""
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title.lower() in buf.value.lower() and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True

    user32.EnumWindows(proto(_cb), 0)
    return found[0] if found else None


def pause_menu():
    """ESC：打开/关闭暂停菜单。

    刻意不放进 ACTIONS 白名单——模型不能自己按 ESC 切 UI。只允许
    P0 存读档演练的脚本显式调用；每次调用前先截图留证。
    """
    d = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(0x1B, 0, 0, 0, None)))
    u = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(
        0x1B, 0, KEYEVENTF_KEYUP, 0, None)))
    return _send(d) and _send(u)


def focus_game(title="StellarBlade"):
    """把游戏窗口提到前台（SendInput 的按键只会送到前台窗口）。

    返回 (是否成功, hwnd)。Windows 允许前台锁时，用
    AttachThreadInput 临时挂到当前前台线程上再 SetForegroundWindow。
    """
    hwnd = find_game_window(title)
    if not hwnd:
        return False, None
    SW_RESTORE, SW_SHOW = 9, 5
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True, hwnd
    tid_fg = user32.GetWindowThreadProcessId(fg, None)
    tid_cur = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_cur:
        attached = bool(user32.AttachThreadInput(tid_cur, tid_fg, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, SW_SHOW)
    finally:
        if attached:
            user32.AttachThreadInput(tid_cur, tid_fg, False)
    time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd, hwnd


def game_rect(title="StellarBlade"):
    """游戏窗口在屏幕上的矩形 (l, t, r, b)，用于坐标换算/截图裁剪。"""
    hwnd = find_game_window(title)
    if not hwnd:
        return None
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def _key_events(spec):
    """按 spec 生成 (按下, 抬起) 两个事件；带 scan 的键走扫描码注入。"""
    ext = 1 if spec.get("extended") else 0
    scan = spec.get("scan")
    if scan:
        base = KEYEVENTF_SCANCODE
        d = KEYBDINPUT(0, scan, base, 0, None)
        u = KEYBDINPUT(0, scan, base | KEYEVENTF_KEYUP, 0, None)
    else:
        d = KEYBDINPUT(spec["vk"], 0, ext, 0, None)
        u = KEYBDINPUT(spec["vk"], 0, ext | KEYEVENTF_KEYUP, 0, None)
    return (INPUT(type=INPUT_KEYBOARD, u=_U(ki=d)),
            INPUT(type=INPUT_KEYBOARD, u=_U(ki=u)))


def tap_key(name, hold_s=0.08):
    spec = ACTIONS.get(name)
    if spec is None:
        raise ValueError("动作 %r 不在白名单。%s" % (name, DENY_HINT))
    down, up = _key_events(spec)
    ok = _send(down)
    time.sleep(hold_s)
    return ok and _send(up)


def hold_key(name, hold_s=0.6):
    """按住走位（剑星里 W 需要持续按住才走，tap 只能挪半步）。"""
    spec = ACTIONS.get(name)
    if spec is None:
        raise ValueError("动作 %r 不在白名单。%s" % (name, DENY_HINT))
    down, up = _key_events(spec)
    if not _send(down):
        return False
    time.sleep(hold_s)
    return _send(up)


def hold_combo(name, hold_s=0.6):
    """同时按住一个组合键（如 Shift+W 冲刺），返回是否全部送出。

    按下顺序按 COMBOS 里的元组，释放顺序反过来（先松方向再松 Shift），
    和真人手感的顺序一致。任何一个键不在白名单就整体拒绝，不做部分执行。
    """
    members = COMBOS.get(name)
    if members is None:
        raise ValueError("组合 %r 不在白名单。可用：%s"
                         % (name, ", ".join(sorted(COMBOS))))
    downs, ups = [], []
    for m in members:
        spec = ACTIONS.get(m)
        if spec is None:
            raise ValueError("组合成员 %r 不在白名单" % m)
        d, u = _key_events(spec)
        downs.append(d)
        ups.append(u)
    ok = True
    for ev in downs:
        ok = _send(ev) and ok
        time.sleep(0.02)
    time.sleep(hold_s)
    for ev in reversed(ups):
        ok = _send(ev) and ok
        time.sleep(0.02)
    return ok


def look_rel(dx, dy):
    """鼠标相对转向。dx/dy 会被夹到 ±400，防止一次甩过头。"""
    dx = max(-400, min(400, int(dx)))
    dy = max(-400, min(400, int(dy)))
    ev = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, None)))
    return _send(ev)


# 10-24 实测：游戏读 Raw Input，注入 dx=100 对应视角 9.33°（光标会因系统
# 加速多跑，但游戏只吃注入值）。每步不超过 ~9°，避免一次甩过头。
LOOK_DEG_PER_UNIT = 0.0933


def look_yaw(deg, chunk_deg=9.0):
    """按角度转视角（正=右转）。先把光标放回游戏窗口中心，避免贴边丢事件。"""
    r = game_rect()
    if r:
        user32.SetCursorPos((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)
        time.sleep(0.06)
    remaining = float(deg)
    done = 0.0
    while abs(remaining) > 1.0:
        step = max(-chunk_deg, min(chunk_deg, remaining))
        look_rel(int(round(step / LOOK_DEG_PER_UNIT)), 0)
        time.sleep(0.15)
        remaining -= step
        done += step
    return done


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


def lock_on():
    """中键 = 锁定/解锁目标（10-24 键位页实拍）。"""
    d = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MIDDLEDOWN, 0, None)))
    u = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MIDDLEUP, 0, None)))
    return _send(d) and _send(u)


def special():
    """特殊指令 = 左键 + 右键同时按（10-24 键位页实拍）。"""
    d1 = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)))
    d2 = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_RIGHTDOWN, 0, None)))
    u1 = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)))
    u2 = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_RIGHTUP, 0, None)))
    ok = _send(d1) and _send(d2)
    time.sleep(0.05)
    return ok and _send(u1) and _send(u2)


def _tap_vk(vk, scan=None, hold_s=0.08):
    """内部用：发送任意 VK（仅给本文件的专项组合键，不进 ACTIONS）。

    scan 给了就走扫描码（Shift/Alt/Ctrl 这类修饰键游戏只认扫描码）。
    """
    d, u = _key_events({"vk": vk, "scan": scan} if scan else {"vk": vk})
    ok = _send(d)
    time.sleep(hold_s)
    return ok and _send(u)


def burst(n):
    """爆发技能 n = 按住 Alt + 数字（10-24 键位页实拍）。

    Alt 在系统里会激活窗口菜单，所以这里按下-数字-松开整个动作 <0.25s，
    且只允许 1~4；不开放通用 Alt。
    """
    if n not in (1, 2, 3, 4):
        raise ValueError("burst 只支持 1~4")
    alt_d = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(
        0, 0x38, KEYEVENTF_SCANCODE, 0, None)))            # 左 Alt
    alt_u = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(
        0, 0x38, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, None)))
    num = {1: 0x02, 2: 0x03, 3: 0x04, 4: 0x05}[n]           # 主键盘 1~4
    num_d = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(
        0, num, KEYEVENTF_SCANCODE, 0, None)))
    num_u = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(
        0, num, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, None)))
    ok = _send(alt_d)
    time.sleep(0.03)
    ok = _send(num_d) and ok
    time.sleep(0.06)
    _send(num_u)
    _send(alt_u)
    return ok


def ranged_mode():
    """Ctrl = 远程模式开关（10-24 键位页实拍）。"""
    return _tap_vk(0x11, scan=0x1D)


def _key(vk, down):
    flags = 0 if down else KEYEVENTF_KEYUP
    if vk in (0x09, 0x20, 0x31, 0x32, 0x33, 0x34):    # Tab/Space/数字 需扩展码的少，统一不加
        flags |= 0
    return INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(vk, 0, flags, 0, None)))


def scan():
    """Tab = 无人机扫描（只读，标出敌人/人类/宝箱/可攀爬点）。

    2026-10-23 实测（B站剑星 WIKI）：Tab 是扫描键；训练场里的
    Tab+1 是 reset_training，两者别混用。
    """
    ok = _send(_key(0x09, True))
    time.sleep(0.05)
    return _send(_key(0x09, False)) and ok


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


def dump_usmap():
    """Ctrl+小键盘6 = UE4SS DumpUSMAP（只读，生成 *.usmap 映射文件）。

    2026-10-23 实测：unversioned 资产（SkillTable 等）没有 usmap 时
    UAssetGUI 只能导出 base64 原始块；dump 出映射后才能出结构化 JSON。
    """
    return _chord(0x11, 0x66), "CTRL+Numpad6 DumpUSMAP"    # VK_CONTROL, VK_NUMPAD6
