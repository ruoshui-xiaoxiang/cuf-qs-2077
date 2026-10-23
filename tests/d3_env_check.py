"""环境检查：进程 + mod 版本 + CufBridge 连接 + 实体扫描。"""
import subprocess, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.d3_auto import (
    MOD_SRC, MOD_DST, GAME_WINDOW_TITLE,
    wait_for_bridge, recv_line, send_call
)

def sha(path):
    r = subprocess.run(
        ['powershell', '-Command', f'(Get-FileHash "{path}" -Algorithm SHA256).Hash'],
        capture_output=True, encoding='utf-8', errors='replace', timeout=10)
    return r.stdout.strip()

def main():
    print('=== 1. 进程状态 ===')
    r = subprocess.run(
        ['powershell', '-Command',
         'Get-Process -Name Cyberpunk2077 -ErrorAction SilentlyContinue | '
         'Select-Object Id,MainWindowTitle | Format-Table -AutoSize'],
        capture_output=True, encoding='utf-8', errors='replace', timeout=10)
    out = r.stdout.strip()
    print(out if out else '  游戏未运行!')

    print('\n=== 2. Mod 文件 ===')
    src_hash = sha(str(MOD_SRC))
    dst_hash = sha(str(MOD_DST))
    print(f'  源:   {MOD_SRC}')
    print(f'        SHA={src_hash[:32]}...')
    print(f'  目标: {MOD_DST}')
    print(f'        SHA={dst_hash[:32]}...')
    print(f'  同步: {"YES" if src_hash == dst_hash else "NO ← 需重新部署!"}')

    # 读版本号
    for line in MOD_SRC.read_text(encoding='utf-8').splitlines():
        if 'MOD_VERSION' in line and 'local' in line:
            print(f'  版本: {line.strip()}')
            break

    print('\n=== 3. CufBridge 连接 ===')
    conn, srv = wait_for_bridge(timeout=15)
    if not conn:
        print('  未连接（游戏可能未加载存档）')
        return 1

    # 扫描
    print('\n=== 4. 实体扫描 ===')
    send_call(conn, 10, 'scan_entities', {'radius': 100, 'limit': 50})
    resp = recv_line(conn, timeout=15)
    ents = resp.get('data', {}).get('entities', []) if resp and resp.get('ok') else []

    player = next((e for e in ents if 'player' in e.get('labels', [])), None)
    npcs = [e for e in ents if 'NPCPuppet' in e.get('labels', [])]
    hostile = [e for e in ents if 'hostile' in e.get('labels', [])]
    items = [e for e in ents if e.get('kind') == 'item']

    print(f'  总实体: {len(ents)}')
    print(f'  NPC: {len(npcs)} (敌对: {len(hostile)})')
    print(f'  物品: {len(items)}')
    if player:
        pos = player.get('position', {})
        print(f'  玩家位置: ({pos.get("x",0):.1f}, {pos.get("y",0):.1f}, {pos.get("z",0):.1f})')
        print(f'  玩家HP: {player.get("hp", "?")}')

    print(f'\n=== 5. 实体明细 ===')
    for e in ents[:25]:
        labels = e.get('labels', [])
        cls = labels[0] if labels else '?'
        h = 'HOSTILE' if 'hostile' in labels else 'neutral'
        kind = e.get('kind', '?')
        eid = e.get('entity_id', '?')
        hp = e.get('hp', '?')
        print(f'  {eid} kind={kind} class={cls} hp={hp} {h}')

    conn.close()
    srv.close()

    print('\n=== 6. 实验日志 ===')
    log_dir = ROOT / 'logs' / 'd3_wanted'
    if log_dir.exists():
        files = sorted(log_dir.glob('*.json'), key=lambda f: f.stat().st_mtime, reverse=True)
        print(f'  最近实验数据 ({len(files)} 个文件):')
        for f in files[:5]:
            size = f.stat().st_size
            print(f'    {f.name} ({size} bytes)')

    print('\n[OK] 环境检查完成')
    return 0

if __name__ == '__main__':
    sys.exit(main())
