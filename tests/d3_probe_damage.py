"""D3.② DamageSystem 方法面探针。"""
import json, socket, sys, time
from pathlib import Path

HOST, PORT = '127.0.0.1', 20770
LOG_DIR = Path(__file__).resolve().parents[1] / 'logs' / 'd3_wanted'

def recv_line(conn, timeout=15.0):
    conn.settimeout(timeout); buf = b''
    while True:
        c = conn.recv(1)
        if not c: raise ConnectionError('closed')
        buf += c
        if c == b'\n': break
    return json.loads(buf.strip(b'\r\n').decode('utf-8')) if buf.strip() else None

def call(conn, cid, fn, args=None, timeout=30.0):
    msg = {'t': 'call', 'id': cid, 'fn': fn}
    if args: msg['args'] = args
    conn.sendall((json.dumps(msg) + '\r\n').encode('utf-8'))
    return recv_line(conn, timeout=timeout)

def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    out_path = LOG_DIR / f'probe_damage_{ts}.json'

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
    ver = hello.get('mod_version', '?')
    print(f'CufBridge: {ver}')

    # probe_damage_system
    print('\n[1] probe_damage_system...')
    resp = call(conn, 10, 'probe_damage_system', timeout=30.0)
    if resp and resp.get('ok'):
        data = resp['data']
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'  已保存: {out_path}')

        print(f'\n=== DamageSystem ===')
        print(f'  exists: {data.get("ds_exists")}')
        print(f'  type: {data.get("ds_type")}')
        if data.get('ds_pairs_error'):
            print(f'  pairs_error: {data["ds_pairs_error"][:120]}')

        print(f'\n  方法面 (pairs):')
        methods = data.get('ds_methods', {})
        if methods:
            for k, v in sorted(methods.items()):
                print(f'    {k}: {v}')
        else:
            print(f'    (无)')

        print(f'\n  伤害方法猜测:')
        for name, info in sorted(data.get('damage_attempts', {}).items()):
            if isinstance(info, dict):
                if info.get('exists'):
                    status = f'EXISTS ({info.get("type","?")})'
                elif info.get('pcall_error'):
                    status = f'ERR'
                else:
                    status = str(info)[:80]
            else:
                status = str(info)[:80]
            print(f'    ds:{name}() → {status}')

        print(f'\n=== 玩家伤害方法 ===')
        for name, info in sorted(data.get('player_damage_methods', {}).items()):
            if isinstance(info, dict):
                if info.get('exists'):
                    if info.get('result_type'):
                        status = f'fn → {info["result_type"]}'
                    elif info.get('call_error'):
                        status = f'fn! {info["call_error"][:60]}'
                    elif info.get('type'):
                        status = f'{info["type"]}'
                    else:
                        status = 'exists'
                elif info.get('pcall_error'):
                    status = f'ERR'
                else:
                    status = str(info)[:80]
            else:
                status = str(info)[:80]
            print(f'    player:{name}() → {status}')
    else:
        print(f'  失败: {resp}')

    # probe_weapon (也跑一下看 0.6.19 结果)
    print('\n[2] probe_weapon...')
    resp2 = call(conn, 11, 'probe_weapon', timeout=30.0)
    if resp2 and resp2.get('ok'):
        d2 = resp2['data']
        wo = d2.get('weapon_obj', {})
        if isinstance(wo, dict):
            print(f'  weapon source: {wo.get("source", "none")}')
            print(f'  weapon type: {wo.get("obj_type", "none")}')
        else:
            print(f'  weapon_obj: {wo}')

    conn.close(); srv.close()
    return 0

if __name__ == '__main__':
    sys.exit(main())
