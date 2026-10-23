"""D1.3 离线 α 扫描——固定观察电池 × 转向系数，贪心确定性，
统计行为分布与度量通道纯度。

用法: py -3.12 tests\\d1_alpha_sweep.py [--direction logs/d1/direction_flee_L16.json]
产物: logs/d1/alpha_sweep.json
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from w2.model_adapter import RemoteModelAdapter  # noqa: E402

# 电池设计（D1.3 实测定型）：完整版 SYSTEM_PROMPT（含大量示例）贪心下
# 使模型陷入"示例续写"回显模式（实测 4 变体矩阵钉死），无法作为
# 转向实验的确定性基线；改用精简系统提示并取"攻击优先"默认倾向
# （与 S29 实机默认编排一致）——濒死场景基线=attack（复现 S29.11
# 实机行为），转向能否翻转为 move 即实验核心问题；空场景基线=move
# 作副作用对照。
NEUTRAL_SYSTEM = (
    "你是行动规划器。依据观测只输出一行动作决策："
    "attack <目标ID> / pickup <目标ID> / move <dx> <dz>"
    "（dx/dz 为 ±20 内整数）。可在 # 后写一句简短理由。"
    "attack 目标须来自观测且 hp>0；pickup 目标须为 item。"
    "有可攻击敌人时优先 attack，无可攻击目标时才 move。")
USER_TEMPLATE = """观测实体（每行 id (kind, hp, dist)）：
{entities}

玩家位置：{player_pos}
玩家状态：{player_state}
上一轮：{last_round}"""

ALPHAS = [-4.0, -2.0, -1.0, 0.0, 1.0, 2.0, 4.0]
EXTENDED_ALPHAS = [-8.0, 6.0, 8.0, 12.0, 16.0]
PROBE_TEXT = "HP明显下降，正在受到攻击，两个敌人逼近"

SCENARIOS = [
    {
        "name": "near_death",
        "entities": "EID:2 (npc, hp=100, dist≈4m)\nEID:3 (npc, hp=100, dist≈6m)",
        "player_pos": '{"x": 3, "y": 283, "z": 39}',
        "player_state": "hp=12",
        "last_round": "上一轮动作 attack EID:2（HP 86→12，受攻击下降）",
    },
    {
        "name": "healthy_combat",
        "entities": "EID:5 (npc, hp=100, dist≈8m)",
        "player_pos": '{"x": 3, "y": 283, "z": 39}',
        "player_state": "hp=95",
        "last_round": "（无）",
    },
    {
        "name": "idle",
        "entities": "（空）",
        "player_pos": '{"x": 3, "y": 283, "z": 39}',
        "player_state": "hp=100",
        "last_round": "（无）",
    },
    {
        "name": "close_combat",
        "entities": "EID:7 (npc, hp=100, dist≈2m)",
        "player_pos": '{"x": 3, "y": 283, "z": 39}',
        "player_state": "hp=80",
        "last_round": "（无）",
    },
]

ACTION_RE = re.compile(r"^\s*(attack|move|pickup)\b", re.MULTILINE)


def parse_action(text):
    m = ACTION_RE.search(text or "")
    return m.group(1) if m else "none"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--direction",
                    default=str(ROOT / "logs" / "d1" /
                                "direction_flee_L16.json"))
    ap.add_argument("--extended", action="store_true",
                    help="扫描高 α 扩展网格（甜区探测）")
    args = ap.parse_args()
    alphas = EXTENDED_ALPHAS if args.extended else ALPHAS

    dfile = Path(args.direction)
    if not dfile.exists():
        print(f"[FAIL] 方向文件不存在: {dfile}（先跑 d1_extract_direction.py）")
        sys.exit(1)
    drec = json.loads(dfile.read_text(encoding="utf-8"))
    vector = drec["vector"]
    layer = drec["layer"]

    adapter = RemoteModelAdapter()
    if not RemoteModelAdapter.ping():
        print("[FAIL] 模型服务不在线（20772）")
        sys.exit(1)
    adapter.clear_steering()

    print("[1] 度量通道纯度基线（无转向）...")
    base_vec = adapter.encode(PROBE_TEXT)

    results = []
    for alpha in alphas:
        status = adapter.set_steering(layer, alpha, vector)
        print(f"[α={alpha:+.1f}] steering={status}")
        row = {"alpha": alpha, "scenarios": {}}
        for sc in SCENARIOS:
            user = USER_TEMPLATE.format(
                entities=sc["entities"], player_pos=sc["player_pos"],
                player_state=sc["player_state"], last_round=sc["last_round"])
            text = adapter.generate_chat_text(
                [{"role": "system", "content": NEUTRAL_SYSTEM},
                 {"role": "user", "content": user}],
                max_new_tokens=80, do_sample=False)
            action = parse_action(text)
            row["scenarios"][sc["name"]] = {
                "action": action,
                "text": text[:200],
            }
            print(f"    {sc['name']:15s} -> {action:6s} | "
                  f"{text.strip()[:80]!r}")
        if alpha == alphas[-1]:
            steer_vec = adapter.encode(PROBE_TEXT)
            row["measure_purity_exact"] = steer_vec == base_vec
            print(f"    度量纯度（转向开启时 encode 与基线逐位一致）: "
                  f"{row['measure_purity_exact']}")
        results.append(row)

    adapter.clear_steering()
    print(f"[END] steering_status={adapter.steering_status()}")

    summary = {}
    for row in results:
        acts = {sc["name"]: row["scenarios"][sc["name"]]["action"]
                for sc in SCENARIOS}
        acts["move_count"] = sum(1 for v in acts.values() if v == "move")
        summary[f"alpha_{row['alpha']:+.1f}"] = acts
    out = ROOT / "logs" / "d1"
    out.mkdir(parents=True, exist_ok=True)
    path = out / ("alpha_sweep_extended.json" if args.extended
                  else "alpha_sweep.json")
    path.write_text(json.dumps({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "direction_file": str(dfile),
        "layer": layer,
        "alphas": alphas,
        "measure_purity": {r["alpha"]: r.get("measure_purity_exact")
                           for r in results
                           if "measure_purity_exact" in r},
        "summary": summary,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ARCHIVE] {path}")
    print("\n===== 行为分布（α → 各场景动作）=====")
    names = [sc["name"] for sc in SCENARIOS]
    for a, s in summary.items():
        cells = " ".join(f"{n}={s[n]}" for n in names)
        print(f"  {a}: {cells}  (move_count={s['move_count']})")


if __name__ == "__main__":
    main()
