"""D1.2 对比方向提取（逃跑概念 × 中性对照）——驻留模型远程计算。

用法: py -3.12 tests\\d1_extract_direction.py [--layer 16]
产物: logs/d1/direction_flee_L{layer}.json
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w2.model_adapter import RemoteModelAdapter  # noqa: E402

POS_TEXTS = [
    "敌人太多火力太猛，我立刻转身逃离，尽快逃走。",
    "HP明显下降，危险！我赶紧撤离战场，快速逃跑。",
    "被敌人包围了，只能脱离逃跑，退到安全的地方。",
    "持续受攻击濒临死亡，生存优先，我马上逃跑。",
    "对方太强打不过，我转身就跑，逃离危险区域。",
    "正在被攻击！停止攻击，转身逃跑，先远离敌人。",
    "火力压制我撑不住了，选择撤离，远离威胁。",
    "HP快归零了，只能放弃战斗，逃命要紧。",
    "寡不敌众，撤退是唯一选择，跑向安全地点。",
    "受伤超过承受范围，停战逃跑，脱离敌人火力。",
]

NEG_TEXTS = [
    "敌人不多威胁不大，我停下脚步观察四周。",
    "HP充足，安全。我静静观察战场，收集信息。",
    "周围平静没有状况，我留在原地检查装备。",
    "HP充足状态良好，我稍作休整整理物品。",
    "对方不强暂时不用动手，我安静观察对方动向。",
    "没有受到攻击。我留在原地扫描周围环境。",
    "区域内没有明确威胁，我选择观望停留。",
    "HP很高，无需担心。我观察环境变化。",
    "敌人在远处不构成威胁，我站在原地休息。",
    "战场安静平稳，我留在原地计划下一步。",
]


def cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(x * x for x in b))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", type=int, default=16)
    args = ap.parse_args()

    adapter = RemoteModelAdapter()
    if not RemoteModelAdapter.ping():
        print("[FAIL] 模型服务不在线（20772）")
        sys.exit(1)
    if adapter.steering_status() is not None:
        adapter.clear_steering()

    print(f"[1] 提取逃跑方向 @ layer {args.layer}（pos {len(POS_TEXTS)} × "
          f"neg {len(NEG_TEXTS)}）...")
    t0 = time.time()
    direction, meta = adapter.extract_direction(
        args.layer, POS_TEXTS, NEG_TEXTS)
    print(f"    耗时 {time.time() - t0:.1f}s  meta={meta}")

    pos_proj = [cos(direction, adapter.encode(t)) for t in POS_TEXTS]
    neg_proj = [cos(direction, adapter.encode(t)) for t in NEG_TEXTS]
    pos_proj = [x for x in pos_proj if x is not None]
    neg_proj = [x for x in neg_proj if x is not None]
    sep = (sum(pos_proj) / len(pos_proj)
           if pos_proj else 0) - (sum(neg_proj) / len(neg_proj)
                                  if neg_proj else 0)
    print(f"[2] 分离度（协议层投影）: pos_mean={sum(pos_proj) / len(pos_proj):+.4f} "
          f"neg_mean={sum(neg_proj) / len(neg_proj):+.4f} sep={sep:+.4f}")

    out = ROOT / "logs" / "d1"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"direction_flee_L{args.layer}.json"
    path.write_text(json.dumps({
        "concept": "flee_survival",
        "layer": args.layer,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meta": meta,
        "pos_texts": POS_TEXTS,
        "neg_texts": NEG_TEXTS,
        "projection_sep_final_layer": sep,
        "vector": direction,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"[3] 方向已归档 {path}")
    if abs(sep) < 1e-4:
        print("    [WARN] 分离度≈0：该层方向在协议层无区分度，"
              "建议换层重试（如 --layer 12/20）")


if __name__ == "__main__":
    main()
