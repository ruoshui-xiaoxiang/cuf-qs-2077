"""D3.② 快速传送+扫描诊断。

传送 → 等 20s → 大半径扫描 → 打印全部实体。
用于确认坐标是否有效、NPC 加载需要多久。
"""
import json
import socket
import sys
import time

HOST = '127.0.0.1'
PORT = 20770


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
    if args: msg['args'] = args
    conn.sendall((json.dumps(msg) + '\r\n').encode('utf-8'))
    return recv_line(conn, timeout=timeout)


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    srv.settimeout(1.0)
    print('等待连接...')

    conn = None
    for _ in range(24):
        try:
            conn, addr = srv.accept()
            break
        except socket.timeout:
            pass
    if not conn:
        print('超时'); srv.close(); return 1

    hello = recv_line(conn)
    print(f'CufBridge: {hello.get("mod_version", "?")}')
    cid = 10

    # 传送到 Kabuki（Watson 商业区）
    TARGETS = [
        {'x': -175.0, 'y': 65.0, 'z': 27.0, 'name': 'Kabuki'},
        {'x': -477.0, 'y': -125.0, 'z': 8.0, 'name': 'Wellspring'},
    ]

    for loc in TARGETS:
        print(f'\n=== 传送到 {loc["name"]} ===')
        cid += 1
        r = call(conn, cid, 'player_move',
                 args={'x': loc['x'], 'y': loc['y'], 'z': loc['z']})
        print(f'  传送: {r.get("data", {}).get("reason", "?") if r else "fail"}')

        # 分阶段扫描：5s / 10s / 20s
        for wait in [5, 10, 15]:
            time.sleep(5)
            total_wait = wait
            cid += 1
            sr = call(conn, cid, 'scan_entities',
                      args={'radius': 100, 'limit': 50}, timeout=15.0)
            if not sr or not sr.get('ok'):
                print(f'  [{total_wait}s] 扫描失败')
                continue
            data = sr.get('data', {})
            ents = data.get('entities', [])
            print(f'  [{total_wait}s] {len(ents)} 实体 (radius={data.get("radius")})')
            for e in ents[:20]:
                labels = e.get('labels', [])
                cls = labels[0] if labels else '?'
                hostile = 'hostile' in labels
                print(f'    {e["entity_id"]} class={cls} kind={e.get("kind","?")} '
                      f'hp={e.get("hp",0)} hostile={hostile}')

    conn.close()
    srv.close()
    print('\n完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
