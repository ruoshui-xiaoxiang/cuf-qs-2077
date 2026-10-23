"""D3.② 快速通缉测试——在当前位置切枪开枪+观测。

用户手动走到平民区域后运行此脚本。
直接扫描→切枪(key 3)→开枪→观测 60s。
"""
import ctypes, ctypes.wintypes, json, socket, sys, time
from pathlib import Path

HOST, PORT = '127.0.0.1', 20770
LOG_DIR = Path(__file__).resolve().parents[1] / 'logs' / 'd3_wanted'

SendInput = ctypes.windll.user32.SendInput
FindWindowW = ctypes.windll.user32.FindWindowW
SetForegroundWindow = ctypes.windll.user32.SetForegroundWindow

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
SCANCODE_3 = 0x04

def press_key(sc):
    extra = ctypes.c_ulong(0)
    ii = InputUnion(); ii.ki = KeyBdInput(0, 0, KEYEVENTF_SCANCODE, 0, ctypes.pointer(extra))
    ii.ki.wScan = sc
    SendInput(1, ctypes.pointer(Input(1, ii)), ctypes.sizeof(Input(1, ii)))
def release_key(sc):
    extra = ctypes.c_ulong(0)
    ii = InputUnion(); ii.ki = KeyBdInput(0, 0, KEYEVENTF_SCANCODE|KEYEVENTF_KEYUP, 0, ctypes.pointer(extra))
    ii.ki.wScan = sc
    SendInput(1, ctypes.pointer(Input(1, ii)), ctypes.sizeof(Input(1, ii)))
def click_mouse():
    extra = ctypes.c_ulong(0)
    ii = InputUnion(); ii.mi = MouseInput(0,0,0,0x0002,0,ctypes.pointer(extra))
    SendInput(1, ctypes.pointer(Input(0, ii)), ctypes.sizeof(Input(0, ii)))
    time.sleep(0.05)
    ii2 = InputUnion(); ii2.mi = MouseInput(0,0,0,0x0004,0,ctypes.pointer(extra))
    SendInput(1, ctypes.pointer(Input(0, ii2)), ctypes.sizeof(Input(0, ii2)))

def recv_line(conn, timeout=15.0):
    conn.settimeout(timeout); buf = b''
    while True:
        c = conn.recv(1)
        if not c: raise ConnectionError('closed')
        buf += c
        if c == b'\n': break
    return json.loads(buf.strip(b'\r\n').decode('utf-8')) if buf.strip() else None

def call(conn, cid, fn, args=None, timeout=15.0):
    msg = {'t':'call','id':cid,'fn':fn}
    if args: msg['args'] = args
    conn.sendall((json.dumps(msg)+'\r\n').encode('utf-8'))
    return recv_line(conn, timeout=timeout)

def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    out_path = LOG_DIR / f'wanted_trigger_{ts}.json'

    hwnd = FindWindowW(None, "Cyberpunk 2077 (C) 2020 by CD Projekt RED")
    if hwnd: SetForegroundWindow(hwnd)
    time.sleep(0.5)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT)); srv.listen(1); srv.settimeout(1.0)
    print('等待连接...')
    conn = None
    for _ in range(24):
        try: conn, _ = srv.accept(); break
        except socket.timeout: pass
    if not conn: print('超时'); srv.close(); return 1
    hello = recv_line(conn)
    print(f'CufBridge: {hello.get("mod_version","?")}')
    cid = 10

    # 扫描基线
    print('\n--- 基线扫描 ---')
    cid += 1
    sr = call(conn, cid, 'scan_entities', args={'radius': 100, 'limit': 50}, timeout=15.0)
    ents = sr.get('data',{}).get('entities',[]) if sr and sr.get('ok') else []
    npcs = [e for e in ents if 'NPCPuppet' in e.get('labels',[])]
    non_hostile = [e for e in npcs if 'hostile' not in e.get('labels',[])]
    pre_hostile = sum(1 for e in ents if 'hostile' in e.get('labels',[]))
    print(f'  {len(ents)} 实体, {len(npcs)} NPC, {len(non_hostile)} 非敌对, {pre_hostile} 敌对')

    # 获取玩家位置
    player = next((e for e in ents if 'player' in e.get('labels',[])), None)
    if player:
        pos = player.get('position', {})
        print(f'  玩家位置: x={pos.get("x",0):.1f} y={pos.get("y",0):.1f} z={pos.get("z",0):.1f}')

    # 切枪+开枪
    print('\n--- 切枪(key 3) + 开枪 ---')
    if hwnd: SetForegroundWindow(hwnd)
    time.sleep(0.3)
    press_key(SCANCODE_3); time.sleep(0.1); release_key(SCANCODE_3)
    time.sleep(1.5)
    for i in range(5):
        click_mouse()
        print(f'  射击 #{i+1}')
        time.sleep(0.6)
    attack_time = time.time()

    # 观测 60s
    print('\n--- 观测 60s ---')
    observations = []
    prev_ids = {e.get('entity_id') for e in ents}
    scan_count = 0
    t0 = time.time()
    while time.time() - t0 < 60:
        elapsed = time.time() - attack_time
        scan_count += 1; cid += 1
        try:
            resp = call(conn, cid, 'scan_entities', args={'radius':100,'limit':50}, timeout=15.0)
        except: time.sleep(5); continue
        if not resp or not resp.get('ok'): time.sleep(5); continue

        e_list = resp.get('data',{}).get('entities',[])
        cur_ids = {e.get('entity_id') for e in e_list}
        new_ids = cur_ids - prev_ids
        hostiles = [e for e in e_list if 'hostile' in e.get('labels',[])]
        npc_list = [e for e in e_list if 'NPCPuppet' in e.get('labels',[])]

        obs = {'elapsed_s': round(elapsed,1), 'total': len(e_list),
               'hostiles': len(hostiles), 'npcs': len(npc_list), 'new': len(new_ids)}
        observations.append(obs)
        prev_ids = cur_ids

        new_s = f'+{len(new_ids)}' if new_ids else ''
        print(f'  [{elapsed:.0f}s] total={len(e_list)} hostile={len(hostiles)} npcs={len(npc_list)} {new_s}')
        time.sleep(5)

    conn.close(); srv.close()

    # 保存
    summary = {
        'player_pos': player.get('position') if player else None,
        'pre_hostile': pre_hostile,
        'max_hostile': max((o['hostiles'] for o in observations), default=0),
        'total_new': sum(o['new'] for o in observations),
        'any_new_hostile': any(o['hostiles'] > pre_hostile for o in observations),
    }
    result = {'baseline': {'total': len(ents), 'npcs': len(npcs), 'non_hostile': len(non_hostile)},
              'observations': observations, 'summary': summary,
              '_meta': {'timestamp': ts, 'script': 'd3_quick_trigger'}}
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f'\n=== 总结 ===')
    print(f'  基线: {len(ents)} 实体, {len(non_hostile)} 非敌对 NPC')
    print(f'  基线敌对: {pre_hostile} → 最大: {summary["max_hostile"]}')
    print(f'  新敌对出现: {summary["any_new_hostile"]}')
    print(f'  结果: {out_path}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
