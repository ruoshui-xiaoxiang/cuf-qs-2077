"""D3.② 快速扫描多区域找平民 NPC。"""
import ctypes, ctypes.wintypes, json, socket, sys, time

HOST, PORT = '127.0.0.1', 20770
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

PUL = ctypes.POINTER(ctypes.c_ulong)
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SCANCODE_3 = 0x04


def press_key(sc):
    extra = ctypes.c_ulong(0)
    ii = InputUnion()
    ii.ki = KeyBdInput(0, 0, KEYEVENTF_SCANCODE, 0, ctypes.pointer(extra))
    ii.ki.wScan = sc
    SendInput(1, ctypes.pointer(Input(1, ii)), ctypes.sizeof(Input(1, ii)))

def release_key(sc):
    extra = ctypes.c_ulong(0)
    ii = InputUnion()
    ii.ki = KeyBdInput(0, 0, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, ctypes.pointer(extra))
    ii.ki.wScan = sc
    SendInput(1, ctypes.pointer(Input(1, ii)), ctypes.sizeof(Input(1, ii)))

def click_mouse():
    extra = ctypes.c_ulong(0)
    ii = InputUnion()
    ii.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, ctypes.pointer(extra))
    SendInput(1, ctypes.pointer(Input(0, ii)), ctypes.sizeof(Input(0, ii)))
    time.sleep(0.05)
    ii2 = InputUnion()
    ii2.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, ctypes.pointer(extra))
    SendInput(1, ctypes.pointer(Input(0, ii2)), ctypes.sizeof(Input(0, ii2)))


def recv_line(conn, timeout=15.0):
    conn.settimeout(timeout)
    buf = b''
    while True:
        chunk = conn.recv(1)
        if not chunk: raise ConnectionError('closed')
        buf += chunk
        if chunk == b'\n': break
    line = buf.strip(b'\r\n')
    return json.loads(line.decode('utf-8')) if line else None

def call(conn, cid, fn, args=None, timeout=15.0):
    msg = {'t': 'call', 'id': cid, 'fn': fn}
    if args: msg['args'] = args
    conn.sendall((json.dumps(msg) + '\r\n').encode('utf-8'))
    return recv_line(conn, timeout=timeout)


# 候选区域——市区平民密集点
LOCATIONS = [
    {'x': -1970.0, 'y': -570.0, 'z': 15.0, 'name': 'Megabuilding_H1'},
    {'x': -1620.0, 'y': -1110.0, 'z': 10.0, 'name': 'Charter_Hill'},
    {'x': -350.0, 'y': -55.0, 'z': 15.0, 'name': 'CityCenter_Plaza'},
    {'x': -130.0, 'y': 880.0, 'z': 25.0, 'name': 'Northside_Market'},
    {'x': 380.0, 'y': -900.0, 'z': 12.0, 'name': 'Pacifica_Entrance'},
    {'x': -1050.0, 'y': -370.0, 'z': 15.0, 'name': 'Heywood_Plaza'},
]


def main():
    hwnd = FindWindowW(None, "Cyberpunk 2077 (C) 2020 by CD Projekt RED")
    if not hwnd:
        print('游戏窗口未找到'); return 1
    SetForegroundWindow(hwnd)
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

    for loc in LOCATIONS:
        print(f'\n=== {loc["name"]} ({loc["x"]},{loc["y"]},{loc["z"]}) ===')
        cid += 1
        r = call(conn, cid, 'player_move',
                 args={'x': loc['x'], 'y': loc['y'], 'z': loc['z']})
        reason = r.get('data', {}).get('reason', '?') if r else 'fail'
        print(f'  传送: {reason}')
        time.sleep(12)  # 多等一会儿加载

        cid += 1
        sr = call(conn, cid, 'scan_entities',
                  args={'radius': 100, 'limit': 50}, timeout=15.0)
        if not sr or not sr.get('ok'):
            print('  扫描失败'); continue
        ents = sr.get('data', {}).get('entities', [])
        npcs = [e for e in ents if 'NPCPuppet' in e.get('labels', [])]
        non_hostile = [e for e in npcs if 'hostile' not in e.get('labels', [])]
        print(f'  {len(ents)} 实体, {len(npcs)} NPC, {len(non_hostile)} 非敌对')
        for e in non_hostile[:5]:
            print(f'    {e["entity_id"]} hp={e.get("hp","?")}')

        if len(non_hostile) >= 2:
            print(f'\n  >>> 找到平民区！尝试开枪... <<<')
            SetForegroundWindow(hwnd)
            time.sleep(0.3)
            press_key(SCANCODE_3); time.sleep(0.1); release_key(SCANCODE_3)
            time.sleep(1.5)
            for i in range(5):
                click_mouse()
                print(f'    射击 #{i+1}')
                time.sleep(0.6)

            # 观测 30s
            print(f'  观测 30s...')
            time.sleep(3)
            for t in range(6):
                cid += 1
                sr2 = call(conn, cid, 'scan_entities',
                           args={'radius': 100, 'limit': 50}, timeout=15.0)
                if sr2 and sr2.get('ok'):
                    e2 = sr2.get('data', {}).get('entities', [])
                    h = sum(1 for e in e2 if 'hostile' in e.get('labels', []))
                    n = sum(1 for e in e2 if 'NPCPuppet' in e.get('labels', []))
                    print(f'  [{(t+1)*5}s] total={len(e2)} hostile={h} npcs={n}')
                time.sleep(5)
            break

    conn.close(); srv.close()
    print('\n完成')
    return 0

if __name__ == '__main__':
    sys.exit(main())
