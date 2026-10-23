"""S28 遗留：候选公理激活阈值敏感性标定（S29.5 后续方向 1）。

对候选池三个公理（A_M2/A_S1/A_I1，各自 activation_trigger 定义
event_kinds/min_count/window_rounds）做 min_count × window_rounds
网格扫描，在不同事件流模式下统计激活时序/正确性，给出推荐配置。

事件流模式（按候选触发事件种类注入）：
- 持续证据（steady）：每轮 2 事件 × 6 轮（总 12）——应激活
- 低频分散（sparse）：每轮 1 事件 × 6 轮（总 6）——窗口越长越晚激活
- 噪声+真实（noise_signal）：前 2 轮 0、后 4 轮 2（总 8）——延迟激活
- 单轮突发（burst）：r0 5 事件、后 5 轮 0（总 5）——单轮达标即激活

正确性判据：
- 真阳性：事件总量 ≥ min_count → 应在 round ≤ rounds-1 激活
- 真阴性：事件总量 < min_count → 不应激活
- 激活延迟：激活轮（越小越好）

推荐 = 全部场景真阳/真阴正确 + 总延迟最小（窗口短优先）。

运行：py tests/activation_calib.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w1.rule_validator import RuleValidator
from w2.axiom_activator import ActivationEngine

SCENARIOS = {
    "steady":       [2, 2, 2, 2, 2, 2],
    "sparse":       [1, 1, 1, 1, 1, 1],
    "noise_signal": [0, 0, 2, 2, 2, 2],
    "burst":        [5, 0, 0, 0, 0, 0],
}

MIN_COUNTS = [1, 2, 3, 5]
WINDOWS = [3, 5, 8]


class StubEngine:
    """记录 add_axiom 调用的桩（不跑真实公理引擎）。"""

    def __init__(self):
        self.added = []

    def add_axiom(self, axiom_id, hypothesis, anchor, boundary_conditions=None):
        self.added.append(axiom_id)


def simulate(cand, events_per_round, min_count, window):
    rv = RuleValidator()
    eng = StubEngine()
    kinds = set(cand["activation_trigger"].get("event_kinds", []))
    ae = ActivationEngine(
        rv, eng, enabled=True,
        min_count_overrides={cand["axiom_id"]: min_count},
        window_overrides={cand["axiom_id"]: window})
    activated_round = None
    for r, n in enumerate(events_per_round):
        ae.feed({"events": [{"kind": next(iter(kinds))}] * n}, r)
        if ae.end_round():
            activated_round = r
    return activated_round, bool(eng.added)


def calibrate(aid, cand):
    rows = []
    for min_count in MIN_COUNTS:
        for window in WINDOWS:
            entry = {"min_count": min_count, "window": window}
            delay_sum = 0
            correct = True
            for name, ev in SCENARIOS.items():
                total = sum(ev)
                act_round, activated = simulate(cand, ev, min_count, window)
                ok = (activated == (total >= min_count))
                if not ok:
                    correct = False
                entry[f"{name}_round"] = act_round
                entry[f"{name}_ok"] = ok
                if act_round is not None:
                    delay_sum += act_round
            entry["correct_all"] = correct
            entry["delay_sum"] = delay_sum
            rows.append(entry)

    print(f"\n=== {aid}（触发事件："
          f"{cand['activation_trigger'].get('event_kinds')}）===")
    print(f"{'min':<4}{'win':<5}{'steady':<9}{'sparse':<9}"
          f"{'noise':<10}{'burst':<8}{'全对':<6}{'延迟和'}")
    best = None
    for r in rows:
        print(f"{r['min_count']:<4}{r['window']:<5}"
              f"{str(r['steady_round']):<9}{str(r['sparse_round']):<9}"
              f"{str(r['noise_signal_round']):<10}{str(r['burst_round']):<8}"
              f"{str(r['correct_all']):<6}{r['delay_sum']}")
        if r["correct_all"] and (best is None
                                 or r["delay_sum"] < best["delay_sum"]):
            best = r

    if best:
        print(f"推荐：min_count={best['min_count']}, "
              f"window={best['window']} (delay_sum={best['delay_sum']})")
    else:
        print("无全对组合")
    tr = cand["activation_trigger"]
    print(f"当前配置：min_count={tr['min_count']}, "
          f"window_rounds={tr['window_rounds']}")
    return best


def main():
    rv = RuleValidator()
    for cand in rv.get_candidates():
        calibrate(cand["axiom_id"], cand)


if __name__ == "__main__":
    main()
