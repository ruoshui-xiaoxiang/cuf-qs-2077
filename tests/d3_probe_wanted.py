"""D3.② 通缉/警察状态 API 考古探针（服务器模式）。

Python 侧作为 TCP 服务器监听 20770，等待 CufBridge（游戏侧）连接，
然后发送 probe_wanted 命令，探测 Cyberpunk 2077 通过 CET 暴露的
通缉等级、犯罪状态、警察敌对等接口。

协议：换行分隔 JSON（与 GameConnector 一致）。

纯只读，不修改任何游戏状态。

用法: py tests/d3_probe_wanted.py
前置: 游戏运行中 + CufBridge 0.6.17 已加载（CufBridge 会自动重连）
"""
import json
import socket
import sys
import time
from pathlib import Path

HOST = '127.0.0.1'
PORT = 20770
LOG_DIR = Path(__file__).resolve().parents[1] / 'logs' / 'd3_wanted'


def recv_line(conn, timeout=15.0):
    """读取一行（以 \\n 结尾），返回解析后的 JSON。"""
    conn.settimeout(timeout)
    buf = b''
    while True:
        chunk = conn.recv(1)
        if not chunk:
            raise ConnectionError('connection closed')
        buf += chunk
        if chunk == b'\n':
            break
    line = buf.strip(b'\r\n')
    if not line:
        return None
    return json.loads(line.decode('utf-8'))


def send_call(conn, call_id, fn, args=None):
    """发送 call 命令。"""
    msg = {'t': 'call', 'id': call_id, 'fn': fn}
    if args is not None:
        msg['args'] = args
    payload = (json.dumps(msg, ensure_ascii=False) + '\r\n').encode('utf-8')
    conn.sendall(payload)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    out_path = LOG_DIR / f'probe_wanted_{ts}.json'

    print('=== D3.② 通缉 API 考古探针（服务器模式） ===')

    # 启动 TCP 服务器
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    srv.settimeout(1.0)
    print(f'监听 {HOST}:{PORT}，等待 CufBridge 连接...')

    conn = None
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            conn, addr = srv.accept()
            print(f'  连接来自: {addr}')
            break
        except socket.timeout:
            pass

    if conn is None:
        print('  超时 120s，无连接。请确认游戏运行中 + CufBridge 0.6.17 已加载。')
        srv.close()
        return 1

    mod_version = '?'

    # 0. 读取 hello 消息
    print('\n[0/2] 等待 hello...')
    try:
        hello = recv_line(conn, timeout=10.0)
        if hello:
            print(f'  hello: {hello}')
            mod_version = hello.get('mod_version', '?')
            print(f'  CufBridge 版本: {mod_version}')
        else:
            print('  空消息，继续...')
    except Exception as e:
        print(f'  读取 hello 异常: {e}（继续尝试）')

    # 1. 版本已在 hello 中获取
    print(f'\n[1/2] CufBridge 版本: {mod_version}（来自 hello）')

    # 2. 调用 probe_wanted
    print('\n[2/2] 调用 probe_wanted（纯只读考古）...')
    result = {}
    try:
        send_call(conn, 2, 'probe_wanted')
        resp = recv_line(conn, timeout=30.0)
        if resp and not resp.get('ok'):
            print(f'  probe_wanted 失败: {resp.get("error", "?")}')
            result = {'error': resp.get('error'), 'raw': resp}
        elif resp:
            result = resp.get('data', resp)
            print('  probe_wanted 成功!')
    except Exception as e:
        print(f'  probe_wanted 异常: {e}')
        result = {'error': str(e)}

    conn.close()
    srv.close()

    # 3. 先保存完整结果（避免显示代码崩溃丢失数据）
    result['_meta'] = {
        'timestamp': ts,
        'mod_version': mod_version,
        'script': 'd3_probe_wanted.py',
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'\n完整结果已保存: {out_path}')

    # 4. 解析结果摘要
    print('\n=== 结果摘要 ===')

    def _safe_items(obj):
        """安全遍历——可能是 dict 或 list。"""
        if isinstance(obj, dict):
            return obj.items()
        if isinstance(obj, list):
            return enumerate(obj)
        return []

    # 系统可用性
    systems = result.get('systems', [])
    available = [s for s in systems if s.get('available')]
    print(f'\n可用游戏系统: {len(available)}/{len(systems)}')
    for s in available:
        method_count = len(s.get('methods', {})) if isinstance(s.get('methods'), (dict, list)) else 0
        pairs_err = s.get('pairs_error', '')
        extra = f' [pairs_error: {pairs_err[:60]}]' if pairs_err else ''
        print(f'  + {s["name"]} ({method_count} methods){extra}')
    unavailable = [s['name'] for s in systems if not s.get('available')]
    if unavailable:
        print(f'  不可用: {", ".join(unavailable)}')

    # 通缉 API 猜测
    wanted = result.get('wanted_apis', [])
    print(f'\n通缉 API 猜测: {len(wanted)} 条')
    for w in wanted:
        status = 'OK' if w.get('ok') else 'NO'
        print(f'  [{status}] {w["desc"]}')
        if w.get('ok'):
            sub = w.get('sub_methods', {})
            if isinstance(sub, dict):
                for m, t in sub.items():
                    ret = w.get(f'{m}_ret', {})
                    if ret:
                        print(f'    -> {m}() = {ret.get("value", "?")} ({ret.get("type", "?")})')
                    else:
                        print(f'    -> {m}: {t}')

    # 玩家通缉方法
    pwm = result.get('player_wanted_methods', [])
    found = [m for m in pwm if m.get('found')]
    print(f'\n玩家实体通缉方法: {len(found)}/{len(pwm)} 找到')
    for m in found:
        if m.get('is_property'):
            print(f'  + {m["method"]} (property, {m.get("prop_type", "?")})')
        elif m.get('ret_type'):
            print(f'  + {m["method"]}() -> {m.get("ret_value", "?")} ({m["ret_type"]})')
        elif m.get('call_err'):
            print(f'  ~ {m["method"]}() -> error: {m["call_err"][:80]}')

    # 警察扫描
    ps = result.get('police_scan', {})
    print(f'\n警察扫描: police={ps.get("police_count", 0)}, hostile={ps.get("hostile_count", 0)}')
    for s in ps.get('samples', []):
        print(f'  {s.get("eid", "?")} class={s.get("class", "?")} hostile={s.get("hostile")} hp={s.get("hp")}')

    # 态度系统
    att = result.get('attitude_system', {})
    print(f'\n态度系统: available={att.get("available")}')
    if att.get('available'):
        methods = att.get('methods', {})
        for k, v in list(_safe_items(methods))[:10]:
            print(f'  {k} ({v})')
        if att.get('pairs_error'):
            print(f'  [pairs_error: {att["pairs_error"][:80]}]')
    ptp = result.get('attitude_player_to_police', [])
    if ptp:
        for p in ptp:
            print(f'  玩家->{p["group"]}: ok={p["ok"]} value={p.get("value")}')

    # 黑板探测
    bb = result.get('blackboard_probe', {})
    print(f'\n黑板系统: bb_available={bb.get("bb_system_available")}, player_bb_ok={bb.get("player_bb_ok")}')
    if bb.get('methods'):
        for k, v in list(_safe_items(bb['methods']))[:10]:
            print(f'  {k} ({v})')
    if bb.get('pairs_error'):
        print(f'  [pairs_error: {bb["pairs_error"][:80]}]')

    # 玩家信息
    pi = result.get('player_info', {})
    if pi:
        print(f'\n玩家信息: available={pi.get("available")}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
