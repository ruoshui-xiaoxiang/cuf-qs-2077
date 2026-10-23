"""S21 批量正式实验离线自测（RED→GREEN 契约）。

批量契约（S21 锁定）：
- 三公理聚焦剧本族：每公理一个种子化世界模式，机械触发该公理完成
  整条迭代链（反例累计 → 3 预警 → 5 降级 → 边界自动补全），其余
  公理保持 active（迭代特异性）。
  · A_M1  mode="A_M1" ：两个不同标签组合的可伤害 gang 轮换受击，攻击后
    HP 未降（hp_before == hp_after）→ R_M1_2 真反例（闭环观测上下文携带
    hp_before/after；实验组与对照组同口径镜像提取）。场景另含 3 个远距
    离无交互背景平民：对照组频率归纳可学「恒正常」而存活（否则每轮
    错判受击实体、连续低分触发功能死亡），但无法预测交替异常，均值
    仍 <1，P_W2_5 差异信号保留。
  · A_W1  mode="A_W1" ：两个不同标签组合的超半径可见实体（单组合会
    被引擎 _label_variants 闸门锁死于 1 永不计数——决策 #4 约束）。
  · A_W2_5 mode="A_W2_5"：僵尸剧本（S20 家族）。
- 每公理条件 ≥3 种子（锁定决策 #9），产物四件套每运行独立目录 +
  formal_batch_summary.json 总汇。
- 三大交付物：①完整迭代日志（iteration：final_status / warn_round /
  downgrade_round / max_count / 边界条件终态）；②收敛曲线
  （convergence_curve = 逐轮 alignment_score 序列 + qs_history）；
  ③对照组对比数据（S18 回放 + compare_reports 成对序列）。
"""
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tempfile

from w3.formal_batch import run_formal_batch
from mock_game_server import MockGameServer

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "experiment_config.json"
ZOMBIE_COMBOS = [["undead", "hostile"], ["undead", "neutral"],
                 ["undead", "aggressive"]]

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}")
    except AssertionError as e:
        RESULTS.append((name, False, str(e)))
        print(f"[FAIL] {name}: {e}")
    except Exception as e:
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")


# ── 三公理聚焦剧本 mock ──────────────────────────────────────


class AxiomScenarioMock(MockGameServer):
    """按模式供给异常世界；种子仅做位置/时机的非本质抖动。"""

    def __init__(self, port, seed, rounds, mode):
        super().__init__(port=port, initial_scenario="empty")
        assert mode in ("A_M1", "A_W1", "A_W2_5"), mode
        self.mode = mode
        self.rng = random.Random(seed)
        self.zombie_start = self.rng.randint(1, 3) if mode == "A_W2_5" else None
        self.far_x1 = self.rng.uniform(60.0, 80.0) if mode == "A_W1" else None
        self.far_x2 = self.rng.uniform(81.0, 95.0) if mode == "A_W1" else None
        self.gang_x = self.rng.uniform(6.0, 10.0) if mode == "A_M1" else None
        self.round_ptr = 0
        self.player_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._events_queue = []
        self._next_evt = 0
        self.handlers["scan_entities"] = self._h_scan
        self.handlers["get_events"] = self._h_get_events
        self.handlers["player_attack"] = self._h_attack
        self.handlers["player_move"] = self._h_move

    def _current_entities(self):
        ents = [
            {"entity_id": "EID:1", "kind": "player",
             "labels": ["player"], "hp": 100,
             "position": dict(self.player_pos)},
        ]
        if self.mode == "A_M1":
            # 两个不同标签的可伤害 gang，位置按轮次交替，使 heuristic
            # planner（攻击最近非玩家）轮换攻击二者——制造 >=2 标签组合
            # 的 R_M1_2 反例（攻击可伤害 NPC 但 HP 未降）。
            near_first = (self.round_ptr % 2 == 0)
            g1_x = 6.0 if near_first else 12.0
            g2_x = 12.0 if near_first else 6.0
            ents.append({"entity_id": "EID:G1", "kind": "npc",
                         "labels": ["gang"], "hp": 100,
                         "position": {"x": g1_x, "y": 0.0, "z": 0.0}})
            ents.append({"entity_id": "EID:G2", "kind": "npc",
                         "labels": ["gang", "hostile"], "hp": 100,
                         "position": {"x": g2_x, "y": 0.0, "z": 0.0}})
            # 背景平民（远距离、无交互）：频率归纳可学会「该组合恒
            # 正常」，抬高对照组逐轮 acc 躲过功能死亡线（连续 3 轮
            # score < τ=0.6）；同时保持交替异常率 0.5 使其无法预测
            # 受击 gang——对照组均值仍 <1，P_W2_5 差异信号保留。
            for i, cx in enumerate((30.0, 36.0, 42.0)):
                ents.append({"entity_id": f"EID:C{i}", "kind": "npc",
                             "labels": ["civilian"], "hp": 100,
                             "position": {"x": cx, "y": 0.0, "z": 0.0}})
        else:
            ents.append({"entity_id": "EID:N1", "kind": "npc",
                         "labels": ["civilian"], "hp": 100,
                         "position": {"x": 5.0, "y": 0.0, "z": 0.0}})
            if self.mode == "A_W1":
                ents.append({"entity_id": "EID:F1", "kind": "npc",
                             "labels": ["scavenger"], "hp": 100,
                             "position": {"x": self.far_x1, "y": 0.0, "z": 0.0}})
                ents.append({"entity_id": "EID:F2", "kind": "device",
                             "labels": ["drone"], "hp": 60,
                             "position": {"x": self.far_x2, "y": 0.0, "z": 0.0}})
            elif self.mode == "A_W2_5" and self.round_ptr >= self.zombie_start:
                n_z = 2 if self.round_ptr % 2 == 0 else 3
                picks = self.rng.sample(ZOMBIE_COMBOS, n_z)
                for i, combo in enumerate(picks):
                    ents.append({
                        "entity_id": f"EID:Z{self.round_ptr}_{i}", "kind": "npc",
                        "labels": list(combo), "hp": 0,
                        "position": {"x": 6.0 + i * 3.0, "y": 0.0, "z": 0.0},
                    })
        return ents

    def _h_scan(self, args):
        entities = self._current_entities()
        return {"count": len(entities), "entities": entities,
                "player": entities[0]}

    def _h_get_events(self, args):
        evts = list(self._events_queue)
        self._events_queue.clear()
        return {"events": evts}

    def _queue_combat_attack(self, target_id):
        self._next_evt += 1
        self._events_queue.append({
            "event_id": f"evt_{self._next_evt}",
            "kind": "combat_attack",
            "source_entity": "EID:1",
            "target_entity": target_id,
            "game_ts": int(time.time() * 1000),
        })

    def _h_attack(self, args):
        eid = args.get("target_id", "")
        self._next_evt += 1
        hp_evt = {
            "event_id": f"evt_{self._next_evt}",
            "kind": "hp_changed",
            "source_entity": "EID:1",
            "target_entity": eid,
            "game_ts": int(time.time() * 1000),
        }
        if self.mode == "A_M1":
            # R_M1_2 反例：攻击可伤害 NPC 但 HP 未降（before == after）。
            hp_evt["hp_before"] = 100.0
            hp_evt["hp_after"] = 100.0
        self._events_queue.append(hp_evt)
        if self.mode == "A_M1":
            # 攻击事件 target 对齐实际攻击目标（不再固定 EID:G1 制造
            # target mismatch——R_M1_1 语义已修正，不再靠旁观 mismatch 充数）。
            self._queue_combat_attack(eid)
        self.round_ptr += 1
        return {"action_id": f"act_{self._next_evt}", "accepted": True,
                "reason": "damage_dispatched"}

    def _h_move(self, args):
        self.player_pos = {"x": float(args.get("x", 0)),
                           "y": float(args.get("y", 0)),
                           "z": float(args.get("z", 0))}
        self.round_ptr += 1
        return {"action_id": "act_move", "accepted": True,
                "reason": "teleport_dispatched"}


def scenario_factory(mode, rounds):
    def factory(seed, port):
        return AxiomScenarioMock(port=port, seed=seed, rounds=rounds,
                                 mode=mode)
    return factory


def single_condition(axiom, mode, seed=7, port=20850, rounds=10):
    """单公理单种子运行；文件存在性检查须在 tempdir 存活期内完成。"""
    with tempfile.TemporaryDirectory() as d:
        out_dir = Path(d) / "batch"
        summary = run_formal_batch(
            conditions=[{"axiom": axiom, "seeds": [seed],
                         "mock_factory": scenario_factory(mode, rounds)}],
            rounds=rounds, out_dir=str(out_dir),
            config_path=str(CONFIG_PATH), base_port=port,
            wait_timeout_s=5.0)
        saved = json.loads((out_dir / "formal_batch_summary.json")
                           .read_text(encoding="utf-8"))
        run_dir = out_dir / axiom / f"seed_{seed}"
        missing = [name for name in ("rounds.jsonl", "report.json",
                                     "baseline_report.json", "comparison.json")
                   if not (run_dir / name).exists()]
        return summary, saved, missing


# ── 测试 1：A_M1 聚焦剧本——完整迭代 + 特异性 ─────────────────


def test_axiom_a_m1_full_iteration():
    summary, saved, missing = single_condition("A_M1", "A_M1", port=20850)
    run = summary["conditions"][0]["runs"][0]
    it = run["iteration"]
    assert it["axiom"] == "A_M1"
    assert it["final_status"] == "deprecated", it
    assert it["warn_round"] is not None, it
    assert it["downgrade_round"] is not None, it
    assert it["downgrade_round"] >= it["warn_round"], it
    assert it["max_count"] >= 5, it
    assert len(it["boundary_conditions_final"]) >= 1, \
        f"warn round must auto-complete boundary: {it}"
    # 迭代特异性：其余公理保持 active
    assert run["other_axiom_status"] == {"A_W1": "active",
                                         "A_W2_5": "active"}, run
    # 收敛曲线交付：逐轮对齐分 + QS 历史
    assert len(run["convergence_curve"]) == 10, run["convergence_curve"]
    assert all(isinstance(s, float) for s in run["convergence_curve"])
    assert run["converged_final"] is True, run
    assert run["virtual_lifespan"] == 10, run
    # 对照组对比交付
    cmp = run["comparison"]
    assert cmp["compared_rounds"] >= 5, cmp["compared_rounds"]
    assert len(cmp["exp_series"]) == len(cmp["baseline_series"])
    # 落盘四件套
    assert missing == [], f"missing files: {missing}"
    assert saved["axiom_coverage"] == {"A_M1": 1}, saved["axiom_coverage"]


# ── 测试 2：A_W1 聚焦剧本——双组合超半径实体 ───────────────────


def test_axiom_a_w1_full_iteration():
    summary, saved, missing = single_condition("A_W1", "A_W1", port=20851)
    assert missing == [], f"missing files: {missing}"
    run = summary["conditions"][0]["runs"][0]
    it = run["iteration"]
    assert it["axiom"] == "A_W1"
    assert it["final_status"] == "deprecated", it
    assert it["warn_round"] is not None, it
    assert it["downgrade_round"] is not None, it
    assert it["max_count"] >= 5, it
    assert len(it["boundary_conditions_final"]) >= 1, it
    assert run["other_axiom_status"] == {"A_M1": "active",
                                         "A_W2_5": "active"}, run
    assert run["converged_final"] is True, run
    assert run["virtual_lifespan"] == 10, run
    assert len(run["convergence_curve"]) == 10
    assert run["comparison"]["compared_rounds"] >= 5


# ── 测试 3：A_W2_5 聚焦剧本——僵尸家族 ─────────────────────────


def test_axiom_a_w2_5_full_iteration():
    summary, saved, missing = single_condition("A_W2_5", "A_W2_5", port=20852)
    assert missing == [], f"missing files: {missing}"
    run = summary["conditions"][0]["runs"][0]
    it = run["iteration"]
    assert it["axiom"] == "A_W2_5"
    assert it["final_status"] == "deprecated", it
    assert it["warn_round"] is not None, it
    assert it["downgrade_round"] is not None, it
    assert it["max_count"] >= 5, it
    assert len(it["boundary_conditions_final"]) >= 1, it
    assert run["other_axiom_status"] == {"A_M1": "active",
                                         "A_W1": "active"}, run
    assert run["converged_final"] is True, run
    assert run["virtual_lifespan"] == 10, run


# ── 测试 4：3 公理 × 3 种子矩阵——覆盖度与全量产物 ─────────────


def test_batch_matrix_coverage_and_files():
    seeds = [7, 42, 2077]
    conditions = [
        {"axiom": "A_M1", "seeds": seeds,
         "mock_factory": scenario_factory("A_M1", 10)},
        {"axiom": "A_W1", "seeds": seeds,
         "mock_factory": scenario_factory("A_W1", 10)},
        {"axiom": "A_W2_5", "seeds": seeds,
         "mock_factory": scenario_factory("A_W2_5", 10)},
    ]
    with tempfile.TemporaryDirectory() as d:
        out_dir = Path(d) / "batch"
        summary = run_formal_batch(
            conditions=conditions, rounds=10, out_dir=str(out_dir),
            config_path=str(CONFIG_PATH), base_port=20853,
            wait_timeout_s=5.0)

        # —— 锁定决策 #9：每公理 ≥3 种子 ——
        cov = summary["axiom_coverage"]
        assert cov == {"A_M1": 3, "A_W1": 3, "A_W2_5": 3}, cov

        # —— 9 次运行全部完成目标公理迭代 ——
        total_runs = sum(len(c["runs"]) for c in summary["conditions"])
        assert total_runs == 9, total_runs
        for cond in summary["conditions"]:
            for run in cond["runs"]:
                assert run["iteration"]["final_status"] == "deprecated", run
                assert run["converged_final"] is True, run
                assert run["virtual_lifespan"] == 10, run
                assert len(run["convergence_curve"]) == 10

        # —— 总汇落盘 + 目录结构 ——
        saved = json.loads((out_dir / "formal_batch_summary.json")
                           .read_text(encoding="utf-8"))
        assert saved["mode"] == "formal_batch"
        assert saved["rounds_target"] == 10
        for axiom in ("A_M1", "A_W1", "A_W2_5"):
            for seed in seeds:
                run_dir = out_dir / axiom / f"seed_{seed}"
                for name in ("rounds.jsonl", "report.json",
                             "baseline_report.json", "comparison.json"):
                    assert (run_dir / name).exists(), \
                        f"missing {axiom}/seed_{seed}/{name}"
                rep = json.loads((run_dir / "report.json")
                                 .read_text(encoding="utf-8"))
                assert rep["meta"]["seed"] == seed, rep["meta"]


# ── 测试 5：对照组对比数据——成对序列完整性 ─────────────────────


def test_comparison_data_completeness():
    seeds = [7, 42, 2077]
    conditions = [
        {"axiom": "A_M1", "seeds": seeds,
         "mock_factory": scenario_factory("A_M1", 10)},
        {"axiom": "A_W1", "seeds": seeds,
         "mock_factory": scenario_factory("A_W1", 10)},
        {"axiom": "A_W2_5", "seeds": seeds,
         "mock_factory": scenario_factory("A_W2_5", 10)},
    ]
    with tempfile.TemporaryDirectory() as d:
        out_dir = Path(d) / "batch"
        summary = run_formal_batch(
            conditions=conditions, rounds=10, out_dir=str(out_dir),
            config_path=str(CONFIG_PATH), base_port=20862,
            wait_timeout_s=5.0)
        for cond in summary["conditions"]:
            for run in cond["runs"]:
                cmp = run["comparison"]
                assert len(cmp["exp_series"]) == len(cmp["baseline_series"])
                assert cmp["compared_rounds"] >= 5, cmp
                assert cmp["exp_mean"] >= 0.99, cmp
                assert cmp["baseline_mean"] <= cmp["exp_mean"], cmp
                # S22 统计检验数据底座：序列同域 [-1,1]（浮点容差 1e-9，
                # 余弦内积数值误差可略超边界 ~2e-16）
                assert all(-1.0 - 1e-9 <= s <= 1.0 + 1e-9
                           for s in cmp["exp_series"]), cmp["exp_series"]
                assert all(-1.0 - 1e-9 <= s <= 1.0 + 1e-9
                           for s in cmp["baseline_series"]), cmp["baseline_series"]


if __name__ == "__main__":
    check("axiom_a_m1_full_iteration", test_axiom_a_m1_full_iteration)
    check("axiom_a_w1_full_iteration", test_axiom_a_w1_full_iteration)
    check("axiom_a_w2_5_full_iteration", test_axiom_a_w2_5_full_iteration)
    check("batch_matrix_coverage_and_files", test_batch_matrix_coverage_and_files)
    check("comparison_data_completeness", test_comparison_data_completeness)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
