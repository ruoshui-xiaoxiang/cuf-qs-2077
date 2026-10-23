"""D3.② 武器 API 探针——调用 probe_weapon handler 探测 CET 武器/伤害 API。"""
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
    out_path = LOG_DIR / f'probe_weapon_{ts}.json'

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
    if ver != '0.6.18':
        print(f'  警告: 期望 0.6.18，实际 {ver}')

    print('\n调用 probe_weapon...')
    resp = call(conn, 10, 'probe_weapon', timeout=30.0)
    conn.close(); srv.close()

    if not resp or not resp.get('ok'):
        print(f'失败: {resp}')
        return 1

    data = resp.get('data', {})

    # 保存到文件
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'已保存: {out_path}')

    # 显示结果
    print(f'\n=== 玩家 ===')
    print(f'  player_ok: {data.get("player_ok")}')
    print(f'  player_type: {data.get("player_type")}')

    print(f'\n=== 玩家方法探测 ===')
    pm = data.get('player_methods', {})
    for name, info in sorted(pm.items()):
        if info is None:
            status = 'nil'
        elif isinstance(info, dict):
            if info.get('type') == 'function':
                if info.get('result_ok'):
                    status = f'fn → {info.get("result_type", "?")}'
                elif info.get('call_error'):
                    status = f'fn! {info["call_error"][:80]}'
                else:
                    status = f'fn({info.get("type","?")})'
            elif info.get('type'):
                status = f'{info["type"]} = {info.get("value","")[:60]}'
            elif info.get('pcall_error'):
                status = f'ERR {info["pcall_error"][:80]}'
            else:
                status = str(info)[:80]
        else:
            status = str(info)[:80]
        print(f'  {name}: {status}')

    print(f'\n=== 全局系统探测 ===')
    wc = data.get('weapon_candidates', {})
    for name, info in sorted(wc.items()):
        if isinstance(info, dict):
            if info.get('exists'):
                if info.get('result_type'):
                    status = f'OK → {info["result_type"]}'
                elif info.get('call_error'):
                    status = f'CALL_ERR {info["call_error"][:80]}'
                else:
                    status = 'exists'
            elif info.get('pcall_error'):
                status = f'ERR {info["pcall_error"][:80]}'
            else:
                status = str(info)[:80]
        else:
            status = str(info)[:80]
        print(f'  Game.{name}(): {status}')

    print(f'\n=== 武器对象 ===')
    wo = data.get('weapon_obj', {})
    print(f'  source: {wo.get("source", "none")}')
    print(f'  obj_type: {wo.get("obj_type", "none")}')

    if data.get('weapon_methods'):
        print(f'  weapon methods (pairs):')
        for k, v in sorted(data['weapon_methods'].items()):
            print(f'    {k}: {v}')
    if data.get('weapon_pairs_error'):
        print(f'  weapon pairs_error: {data["weapon_pairs_error"][:100]}')

    print(f'\n=== 开火方法探测 ===')
    fc = data.get('fire_candidates', {})
    if not fc:
        print('  (无武器对象，跳过)')
    for name, info in sorted(fc.items()):
        if isinstance(info, dict):
            if info.get('called'):
                status = f'CALLED → {info.get("result","")[:80]}'
            elif info.get('error'):
                status = f'ERR {info["error"][:80]}'
            else:
                status = str(info)[:80]
        else:
            status = str(info)[:80]
        print(f'  weapon:{name}(): {status}')

    print(f'\n=== 伤害路径 ===')
    dc = data.get('damage_candidates', {})
    for name, info in sorted(dc.items()):
        if isinstance(info, dict):
            status = json.dumps(info, ensure_ascii=False)[:120]
        else:
            status = str(info)[:120]
        print(f'  {name}: {status}')

    return 0

if __name__ == '__main__':
    sys.exit(main())
