"""D3.② 通缉触发实验 v4——键位映射（切枪+开枪）。

通过 Python ctypes + SendInput 模拟硬件级键位输入：
1. 传送到有平民的区域
2. 切出手枪（按 '2'）
3. 开枪（鼠标左键）
4. 扫描观测通缉反应

用法: py tests/d3_wanted_trigger.py
前置: 游戏运行中（窗口模式或无边框窗口）+ CufBridge 已加载
"""
import ctypes
import ctypes.wintypes
import json
import socket
import sys
import time
from pathlib import Path

HOST = '127.0.0.1'
PORT = 20770
LOG_DIR = Path(__file__).resolve().parents[1] / 'logs' / 'd3_wanted'
SCAN_INTERVAL = 5.0
OBSERVE_DURATION = 120

# ===== Windows 输入模拟 =====
SendInput = ctypes.windll.user32.SendInput
FindWindowW = ctypes.windll.user32.FindWindowW
SetForegroundWindow = ctypes.windll.user32.SetForegroundWindow

PUL = ctypes.POINTER(ctypes.c_ulong)

class KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.wintypes.WORD),
                ("wScan", ctypes.wintypes.WORD),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", PUL)]

class MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.wintypes.DWORD),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", PUL)]

class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.wintypes.DWORD),
                ("wParamL", ctypes.wintypes.WORD),
                ("wParamH", ctypes.wintypes.WORD)]

class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyBdInput),
                ("mi", MouseInput),
                ("hi", HardwareInput)]

class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.wintypes.DWORD),
                ("ii", InputUnion)]


# 虚拟键码
VK_1 = 0x31
VK_2 = 0x32
VK_3 = 0x33
VK_4 = 0x34

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
INPUT_KEYBOARD = 1
INPUT_MOUSE = 0

# 扫描码
SCANCODE_1 = 0x02
SCANCODE_2 = 0x03
SCANCODE_3 = 0x04
SCANCODE_Z = 0x2C


def press_key(scancode):
    """模拟按下键（扫描码方式，游戏更容易识别）。"""
    extra = ctypes.c_ulong(0)
    ii_ = InputUnion()
    ii_.ki = KeyBdInput(0, 0, KEYEVENTF_SCANCODE, 0, ctypes.pointer(extra))
    ii_.ki.wScan = scancode
    command = Input(INPUT_KEYBOARD, ii_)
    SendInput(1, ctypes.pointer(command), ctypes.sizeof(command))


def release_key(scancode):
    """模拟释放键。"""
    extra = ctypes.c_ulong(0)
    ii_ = InputUnion()
    ii_.ki = KeyBdInput(0, 0, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, ctypes.pointer(extra))
    ii_.ki.wScan = scancode
    command = Input(INPUT_KEYBOARD, ii_)
    SendInput(1, ctypes.pointer(command), ctypes.sizeof(command))


def click_mouse():
    """模拟鼠标左键点击。"""
    extra = ctypes.c_ulong(0)
    ii_ = InputUnion()
    ii_.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, ctypes.pointer(extra))
    down = Input(INPUT_MOUSE, ii_)

    ii_2 = InputUnion()
    ii_2.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, ctypes.pointer(extra))
    up = Input(INPUT_MOUSE, ii_2)

    SendInput(1, ctypes.pointer(down), ctypes.sizeof(down))
    time.sleep(0.05)
    SendInput(1, ctypes.pointer(up), ctypes.sizeof(up))


def activate_game_window():
    """激活 Cyberpunk 2077 窗口。"""
    titles = [
        "Cyberpunk 2077 (C) 2020 by CD Projekt RED",
        "Cyberpunk 2077",
    ]
    for title in titles:
        hwnd = FindWindowW(None, title)
        if hwnd:
            SetForegroundWindow(hwnd)
            print(f'  游戏窗口已激活 (hwnd={hwnd})')
            return True
    # 回退：按进程名查找
    import subprocess
    result = subprocess.run(
        ['powershell', '-Command',
         '(Get-Process -Name Cyberpunk2077 -ErrorAction SilentlyContinue).MainWindowHandle'],
        capture_output=True, text=True, timeout=5)
    try:
        hwnd = int(result.stdout.strip())
        if hwnd:
            SetForegroundWindow(hwnd)
            print(f'  游戏窗口已激活 (hwnd={hwnd}, via process)')
            return True
    except (ValueError, TypeError):
        pass
    print('  未找到游戏窗口！')
    return False


# ===== TCP 通信 =====
def recv_line(conn, timeout=15.0):
    conn.settimeout(timeout)
    buf = b''
    while True:
        chunk = conn.recv(1)
        if not chunk:
            raise ConnectionError('closed')
        buf += chunk
        if chunk == b'\n':
            break
    line = buf.strip(b'\r\n')
    return json.loads(line.decode('utf-8')) if line else None


def call(conn, cid, fn, args=None, timeout=15.0):
    msg = {'t': 'call', 'id': cid, 'fn': fn}
    if args:
        msg['args'] = args
    conn.sendall((json.dumps(msg) + '\r\n').encode('utf-8'))
    return recv_line(conn, timeout=timeout)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    out_path = LOG_DIR / f'wanted_trigger_{ts}.json'

    print('=== D3.② 通缉触发实验 v4（键位映射） ===')

    # 激活游戏窗口
    if not activate_game_window():
        print('请确认游戏窗口标题')
        return 1

    time.sleep(1)

    # 连接 CufBridge
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    srv.settimeout(1.0)
    print(f'监听 {HOST}:{PORT}...')

    conn = None
    for _ in range(24):
        try:
            conn, _ = srv.accept()
            break
        except socket.timeout:
            pass
    if not conn:
        print('超时'); srv.close(); return 1

    hello = recv_line(conn)
    print(f'CufBridge: {hello.get("mod_version", "?")}')
    cid = 10

    # ===== 传送到 Wellspring =====
    print('\n--- 传送到 Wellspring ---')
    cid += 1
    r = call(conn, cid, 'player_move',
             args={'x': -477.0, 'y': -125.0, 'z': 8.0})
    print(f'  {r.get("data", {}).get("reason", "?") if r else "fail"}')
    print('  等待 10s 加载...')
    time.sleep(10)

    # ===== 基线扫描 =====
    print('\n--- 基线扫描 ---')
    cid += 1
    sr = call(conn, cid, 'scan_entities',
              args={'radius': 100, 'limit': 50}, timeout=15.0)
    entities = sr.get('data', {}).get('entities', []) if sr else []
    pre_hostile = sum(1 for e in entities if 'hostile' in e.get('labels', []))
    npcs = [e for e in entities if 'NPCPuppet' in e.get('labels', [])]
    print(f'  {len(entities)} 实体, {len(npcs)} NPC, {pre_hostile} 敌对')

    # ===== 切枪+开枪 =====
    print('\n--- 切枪+开枪 ---')
    print('  按 3 切出手枪...')
    activate_game_window()
    time.sleep(0.3)
    press_key(SCANCODE_3)
    time.sleep(0.1)
    release_key(SCANCODE_3)
    time.sleep(1.5)  # 等待切枪动画

    print('  开枪（3 次鼠标左键）...')
    for i in range(3):
        click_mouse()
        print(f'    射击 #{i+1}')
        time.sleep(0.8)

    attack_time = time.time()

    # 等 2s 让游戏反应
    time.sleep(2)

    # ===== 观测 =====
    print(f'\n--- 观测 {OBSERVE_DURATION}s ---')
    observations = []
    prev_ids = set()
    # 获取攻击后首次扫描作为新基线
    cid += 1
    sr2 = call(conn, cid, 'scan_entities',
               args={'radius': 100, 'limit': 50}, timeout=15.0)
    if sr2 and sr2.get('ok'):
        ents = sr2.get('data', {}).get('entities', [])
        prev_ids = {e.get('entity_id') for e in ents}
        post_hostile = sum(1 for e in ents if 'hostile' in e.get('labels', []))
        print(f'  攻击后: {len(ents)} 实体, {post_hostile} 敌对')

    scan_count = 0
    t0 = time.time()
    while time.time() - t0 < OBSERVE_DURATION:
        elapsed = time.time() - attack_time
        scan_count += 1
        cid += 1

        try:
            resp = call(conn, cid, 'scan_entities',
                        args={'radius': 100, 'limit': 50}, timeout=15.0)
        except Exception as e:
            print(f'  [{elapsed:.0f}s] 异常: {e}')
            time.sleep(SCAN_INTERVAL)
            continue

        if not resp or not resp.get('ok'):
            time.sleep(SCAN_INTERVAL)
            continue

        ents = resp.get('data', {}).get('entities', [])
        cur_ids = {e.get('entity_id') for e in ents}
        new_ids = cur_ids - prev_ids
        gone_ids = prev_ids - cur_ids

        hostiles = [e for e in ents if 'hostile' in e.get('labels', [])]
        npc_list = [e for e in ents if 'NPCPuppet' in e.get('labels', [])]

        obs = {
            'elapsed_s': round(elapsed, 1),
            'total': len(ents),
            'hostiles': len(hostiles),
            'npcs': len(npc_list),
            'new': len(new_ids),
            'gone': len(gone_ids),
            'new_details': [],
            'hostile_details': [],
        }

        for e in ents:
            eid = e.get('entity_id', '')
            labels = e.get('labels', [])
            cls = labels[0] if labels else '?'
            if eid in new_ids:
                obs['new_details'].append({
                    'eid': eid, 'class': cls,
                    'hostile': 'hostile' in labels,
                    'hp': e.get('hp', 0),
                })
            if 'hostile' in labels:
                obs['hostile_details'].append({'eid': eid, 'class': cls})

        observations.append(obs)
        prev_ids = cur_ids

        new_s = f'+{len(new_ids)}' if new_ids else ''
        gone_s = f'-{len(gone_ids)}' if gone_ids else ''
        chg = ' '.join(filter(None, [new_s, gone_s]))
        print(f'  [{elapsed:.0f}s] #{scan_count}: total={len(ents)} '
              f'hostile={len(hostiles)} npcs={len(npc_list)} {chg}')

        for nd in obs['new_details']:
            tag = 'HOSTILE' if nd['hostile'] else 'neutral'
            print(f'    NEW [{tag}] {nd["eid"]} {nd["class"]} hp={nd["hp"]}')

        time.sleep(SCAN_INTERVAL)

    conn.close()
    srv.close()

    # ===== 保存 =====
    summary = {
        'total_scans': scan_count,
        'duration_s': round(time.time() - attack_time, 1),
        'pre_hostile': pre_hostile,
        'max_hostile': max((o['hostiles'] for o in observations), default=0),
        'total_new': sum(o['new'] for o in observations),
        'any_new_hostile': any(o['hostiles'] > pre_hostile for o in observations),
    }
    result = {
        'method': 'key_simulation',
        'baseline': {'total': len(entities), 'npcs': len(npcs), 'hostile': pre_hostile},
        'observations': observations,
        'summary': summary,
        '_meta': {'timestamp': ts, 'script': 'd3_wanted_trigger_v4'},
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f'\n=== 总结 ===')
    print(f'  基线: {len(entities)} 实体, {len(npcs)} NPC, {pre_hostile} 敌对')
    print(f'  最大敌对: {summary["max_hostile"]}')
    print(f'  出现新敌对: {summary["any_new_hostile"]}')
    print(f'  新实体: {summary["total_new"]}')
    print(f'  结果: {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
