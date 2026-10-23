"""D3.② 自动化调试流程——游戏重启 + 菜单导航 + 通缉实验一体化。

完整流程：
  1. kill_game()        杀游戏进程
  2. deploy_and_start() 部署 mod + 启动游戏
  3. wait_for_game()    等待游戏窗口出现
  4. [ComputerUse]      菜单导航加载存档（外部调用）
  5. wait_for_bridge()  等待 CufBridge TCP 连接
  6. run_experiment()   扫描→切枪→开枪→观测→报告

用法:
  # 完整流程（分步，菜单导航由 AI 用 ComputerUse 完成）
  py -c "from tests.d3_auto import *; kill_game(); deploy_and_start()"
  # → ComputerUse 加载存档 →
  py -c "from tests.d3_auto import *; run_full_experiment()"

  # 快速实验（游戏已加载，直接跑）
  py tests/d3_auto.py
"""
import ctypes
import ctypes.wintypes
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

# ===== 路径配置 =====
ROOT = Path(__file__).resolve().parents[1]
GAME_EXE = r'E:\steam\steamapps\common\Cyberpunk 2077\bin\x64\Cyberpunk2077.exe'
GAME_DIR = r'E:\steam\steamapps\common\Cyberpunk 2077\bin\x64'
MOD_SRC = ROOT / 'game_mods' / 'CufBridge' / 'init.lua'
MOD_DST = Path(GAME_DIR) / 'plugins' / 'cyber_engine_tweaks' / 'mods' / 'CufBridge' / 'init.lua'
LOG_DIR = ROOT / 'logs' / 'd3_wanted'
GAME_WINDOW_TITLE = "Cyberpunk 2077 (C) 2020 by CD Projekt RED"

HOST = '127.0.0.1'
PORT = 20770

# ===== Windows API =====
user32 = ctypes.windll.user32
FindWindowW = user32.FindWindowW
SetForegroundWindow = user32.SetForegroundWindow
SendInput = user32.SendInput

class KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.wintypes.WORD), ("wScan", ctypes.wintypes.WORD),
                ("dwFlags", ctypes.wintypes.DWORD), ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
class MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.wintypes.DWORD),
                ("dwFlags", ctypes.wintypes.DWORD), ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.wintypes.DWORD), ("wParamL", ctypes.wintypes.WORD), ("wParamH", ctypes.wintypes.WORD)]
class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]
class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.wintypes.DWORD), ("ii", InputUnion)]

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
SCANCODE_3 = 0x04  # key '3' = 手枪


# ===== 输入模拟 =====
def press_key(scancode):
    extra = ctypes.c_ulong(0)
    ii = InputUnion()
    ii.ki = KeyBdInput(0, 0, KEYEVENTF_SCANCODE, 0, ctypes.pointer(extra))
    ii.ki.wScan = scancode
    SendInput(1, ctypes.pointer(Input(1, ii)), ctypes.sizeof(Input(1, ii)))

def release_key(scancode):
    extra = ctypes.c_ulong(0)
    ii = InputUnion()
    ii.ki = KeyBdInput(0, 0, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, ctypes.pointer(extra))
    ii.ki.wScan = scancode
    SendInput(1, ctypes.pointer(Input(1, ii)), ctypes.sizeof(Input(1, ii)))

def click_mouse():
    extra = ctypes.c_ulong(0)
    ii = InputUnion()
    ii.mi = MouseInput(0, 0, 0, 0x0002, 0, ctypes.pointer(extra))
    SendInput(1, ctypes.pointer(Input(0, ii)), ctypes.sizeof(Input(0, ii)))
    time.sleep(0.05)
    ii2 = InputUnion()
    ii2.mi = MouseInput(0, 0, 0, 0x0004, 0, ctypes.pointer(extra))
    SendInput(1, ctypes.pointer(Input(0, ii2)), ctypes.sizeof(Input(0, ii2)))

def activate_game_window():
    hwnd = FindWindowW(None, GAME_WINDOW_TITLE)
    if hwnd:
        SetForegroundWindow(hwnd)
        return hwnd
    return None


# ===== 进程管理 =====
def kill_game():
    """杀掉游戏进程。"""
    r = subprocess.run(
        ['powershell', '-Command',
         'Get-Process -Name Cyberpunk2077 -ErrorAction SilentlyContinue | Stop-Process -Force'],
        capture_output=True, encoding='utf-8', errors='replace', timeout=10)
    time.sleep(3)
    print('[OK] 游戏进程已终止')

def deploy_mod():
    """部署 mod 到游戏目录。"""
    subprocess.run(
        ['powershell', '-Command',
         f'Copy-Item -Path "{MOD_SRC}" -Destination "{MOD_DST}" -Force'],
        capture_output=True, encoding='utf-8', errors='replace', timeout=10)
    print(f'[OK] mod 已部署: {MOD_DST.name}')

def start_game():
    """启动游戏。"""
    subprocess.Popen([GAME_EXE], cwd=GAME_DIR)
    print('[OK] 游戏启动中...')

def deploy_and_start():
    """部署 mod + 启动游戏。"""
    deploy_mod()
    start_game()

def wait_for_game(timeout=120):
    """等待游戏窗口出现。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        hwnd = FindWindowW(None, GAME_WINDOW_TITLE)
        if hwnd:
            print(f'[OK] 游戏窗口已出现 ({time.time()-t0:.0f}s)')
            return hwnd
        time.sleep(2)
    print(f'[FAIL] 等待超时 ({timeout}s)')
    return None


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

def send_call(conn, call_id, fn, args=None):
    msg = {'t': 'call', 'id': call_id, 'fn': fn}
    if args:
        msg['args'] = args
    payload = (json.dumps(msg, ensure_ascii=False) + '\r\n').encode('utf-8')
    conn.sendall(payload)

def wait_for_bridge(timeout=180):
    """等待 CufBridge TCP 连接（游戏加载后 mod 自动连上来）。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    srv.settimeout(2.0)
    print(f'监听 {HOST}:{PORT}...')

    conn = None
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            conn, addr = srv.accept()
            break
        except socket.timeout:
            pass

    if not conn:
        srv.close()
        print(f'[FAIL] CufBridge 连接超时 ({timeout}s)')
        return None, srv

    hello = recv_line(conn)
    ver = hello.get('mod_version', '?')
    print(f'[OK] CufBridge {ver} 已连接')
    return conn, srv


# ===== 实验逻辑 =====
def scan(conn, cid, radius=100, limit=50, timeout=15.0):
    """扫描实体。"""
    send_call(conn, cid, 'scan_entities', {'radius': radius, 'limit': limit})
    resp = recv_line(conn, timeout=timeout)
    if resp and resp.get('ok'):
        return resp.get('data', {}).get('entities', [])
    return []

def fire_gun(shots=5, interval=0.6):
    """切枪(key 3) + 开枪。"""
    hwnd = activate_game_window()
    if not hwnd:
        print('[WARN] 游戏窗口未找到，SendInput 可能失效')
    time.sleep(0.3)

    # 切枪
    press_key(SCANCODE_3)
    time.sleep(0.1)
    release_key(SCANCODE_3)
    time.sleep(1.5)

    # 开枪
    for i in range(shots):
        click_mouse()
        time.sleep(interval)

def observe_wanted(conn, cid_start, duration=60, interval=5.0):
    """观测通缉反应。返回观测列表和摘要。"""
    observations = []
    prev_ids = set()
    t0 = time.time()
    cid = cid_start

    while time.time() - t0 < duration:
        elapsed = time.time() - t0
        cid += 1
        try:
            ents = scan(conn, cid)
        except Exception:
            time.sleep(interval)
            continue

        cur_ids = {e.get('entity_id') for e in ents}
        new_ids = cur_ids - prev_ids
        hostiles = [e for e in ents if 'hostile' in e.get('labels', [])]
        npcs = [e for e in ents if 'NPCPuppet' in e.get('labels', [])]

        obs = {
            'elapsed_s': round(elapsed, 1),
            'total': len(ents),
            'hostiles': len(hostiles),
            'npcs': len(npcs),
            'new': len(new_ids),
            'hostile_details': [
                {'eid': e.get('entity_id'), 'class': e.get('labels', ['?'])[0]}
                for e in hostiles
            ],
        }
        observations.append(obs)
        prev_ids = cur_ids

        new_s = f'+{len(new_ids)}' if new_ids else ''
        print(f'  [{elapsed:.0f}s] total={len(ents)} hostile={len(hostiles)} '
              f'npcs={len(npcs)} {new_s}')
        for hd in obs['hostile_details']:
            print(f'    HOSTILE: {hd["eid"]} {hd["class"]}')

        time.sleep(interval)

    max_h = max((o['hostiles'] for o in observations), default=0)
    summary = {
        'scans': len(observations),
        'max_hostile': max_h,
        'any_hostile': max_h > 0,
        'first_hostile_at': next(
            (o['elapsed_s'] for o in observations if o['hostiles'] > 0), None),
    }
    return observations, summary


def run_experiment(conn, cid_start=10, fire_shots=5, observe_duration=60):
    """完整实验：基线扫描→开枪→观测。"""
    cid = cid_start

    # 基线扫描
    print('\n--- 基线扫描 ---')
    cid += 1
    baseline = scan(conn, cid)
    npcs = [e for e in baseline if 'NPCPuppet' in e.get('labels', [])]
    non_hostile = [e for e in npcs if 'hostile' not in e.get('labels', [])]
    pre_hostile = sum(1 for e in baseline if 'hostile' in e.get('labels', []))
    player = next((e for e in baseline if 'player' in e.get('labels', [])), None)

    print(f'  {len(baseline)} 实体, {len(npcs)} NPC, '
          f'{len(non_hostile)} 非敌对, {pre_hostile} 敌对')
    if player:
        pos = player.get('position', {})
        print(f'  位置: ({pos.get("x",0):.1f}, {pos.get("y",0):.1f}, {pos.get("z",0):.1f})')

    if len(non_hostile) == 0:
        print('  [WARN] 附近无非敌对 NPC，实验可能无效')

    # 开枪
    print(f'\n--- 开枪 ({fire_shots} 发) ---')
    fire_gun(shots=fire_shots)
    print('  射击完成')
    attack_time = time.time()

    # 等几秒让游戏反应
    time.sleep(3)

    # 观测
    print(f'\n--- 观测 {observe_duration}s ---')
    observations, summary = observe_wanted(conn, cid, duration=observe_duration)

    # 保存结果
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    result = {
        'baseline': {
            'total': len(baseline),
            'npcs': len(npcs),
            'non_hostile': len(non_hostile),
            'pre_hostile': pre_hostile,
            'player_pos': player.get('position') if player else None,
        },
        'fire': {'shots': fire_shots, 'time': ts},
        'observations': observations,
        'summary': summary,
    }
    out_path = LOG_DIR / f'auto_experiment_{ts}.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 打印总结
    print(f'\n{"="*50}')
    print(f'基线: {len(non_hostile)} 非敌对 NPC, {pre_hostile} 敌对')
    print(f'最大敌对: {summary["max_hostile"]}')
    if summary['first_hostile_at'] is not None:
        print(f'首个敌对出现: {summary["first_hostile_at"]:.0f}s')
    else:
        print('未检测到敌对')
    print(f'结果: {out_path}')
    print(f'{"="*50}')

    return result


# ===== 完整流程入口 =====
def run_full_experiment(observe_duration=60):
    """游戏已加载状态下直接跑实验。"""
    conn, srv = wait_for_bridge(timeout=30)
    if not conn:
        return None
    try:
        result = run_experiment(conn, observe_duration=observe_duration)
        return result
    finally:
        conn.close()
        srv.close()


if __name__ == '__main__':
    # 快速实验模式
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run_full_experiment(observe_duration=dur)
