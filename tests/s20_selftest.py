"""S20 重复实验离线自测（RED→GREEN 契约）。

种子化契约（S20 锁定）：
- 管线本身零随机源（src 全程无 random 引用，纯确定性）；「独立随机
  种子」操作化为种子化世界生成器：random.Random(seed) 驱动逐轮异常
  剧本变异（僵尸出现轮、数量、标签组合），裁决链与种子无关。
- 每条公理条件 ≥3 个独立种子（锁定决策 #9）：每次 repeat 全量注入
  3 条公理，种子随机化世界轨迹，seeds_count ≥ 3 即满足。
- 复现性口径：同种子两次运行 → 轨迹签名（trajectory_signature：
  c_extend_series/qs_history/axiom_final_status/converged_final/
  virtual_lifespan/disintegrated/criterion/对齐分序列/动作序列）
  完全一致；不同种子 → 签名互异。
- 收敛曲线趋势预期（僵尸异常剧本族）：实验组各种子降级后二次收敛
  全真；对照组均值不优于实验组（baseline_mean ≤ exp_mean）；
  默认预算下无解体，虚拟寿命 = 目标轮数。
- 世界时序：动作回执即轮边界（attack/move handler 推进 round_ptr），
  采集扫描服务 schedule[min(round_ptr, len-1)]。
"""
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tempfile

from w3.auto_experiment import run_auto_experiment
from w3.repeat_experiment import (
    repeat_experiment,
    trajectory_signature,
)
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


# ── 种子化世界生成器 + mock ──────────────────────────────────


def world_schedule(seed, rounds):
    """种子驱动的逐轮世界剧本：僵尸在 zombie_start 后出现。"""
    rng = random.Random(seed)
    zombie_start = rng.randint(1, 3)
    schedule = []
    for r in range(rounds):
        ents = [
            {"entity_id": "EID:1", "kind": "player",
             "labels": ["player"], "hp": 100,
             "position": {"x": 0.0, "y": 0.0, "z": 0.0}},
            {"entity_id": "EID:N1", "kind": "npc",
             "labels": ["civilian"], "hp": 100,
             "position": {"x": 4.0, "y": 0.0, "z": 0.0}},
        ]
        if r >= zombie_start:
            n_z = rng.randint(2, 3)
            picks = rng.sample(ZOMBIE_COMBOS, n_z)
            for i, combo in enumerate(picks):
                ents.append({
                    "entity_id": f"EID:Z{r}_{i}", "kind": "npc",
                    "labels": list(combo), "hp": 0,
                    "position": {"x": 6.0 + i * 3.0, "y": 0.0, "z": 0.0},
                })
        schedule.append(ents)
    return schedule, zombie_start


class SeededMock(MockGameServer):
    """种子化剧本 mock：动作回执即轮边界（attack/move 推进 round_ptr）。"""

    def __init__(self, port, seed, rounds):
        super().__init__(port=port, initial_scenario="empty")
        self.seed = seed
        self.schedule, self.zombie_start = world_schedule(seed, rounds + 2)
        self.round_ptr = 0
        self.player_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._events_queue = []
        self._next_evt = 0
        self.handlers["scan_entities"] = self._h_scan
        self.handlers["get_events"] = self._h_get_events
        self.handlers["player_attack"] = self._h_attack
        self.handlers["player_move"] = self._h_move

    def _current_entities(self):
        idx = min(self.round_ptr, len(self.schedule) - 1)
        ents = []
        for e in self.schedule[idx]:
            e = dict(e)
            if e["kind"] == "player":
                e["position"] = dict(self.player_pos)
            ents.append(e)
        return ents

    def _h_scan(self, args):
        entities = self._current_entities()
        return {"count": len(entities), "entities": entities,
                "player": entities[0]}

    def _h_get_events(self, args):
        evts = list(self._events_queue)
        self._events_queue.clear()
        return {"events": evts}

    def _h_attack(self, args):
        eid = args.get("target_id", "")
        self._next_evt += 1
        self._events_queue.append({
            "event_id": f"evt_{self._next_evt}",
            "kind": "hp_changed",
            "source_entity": "EID:1",
            "target_entity": eid,
            "game_ts": int(time.time() * 1000),
        })
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


def seeded_factory(rounds):
    def factory(seed, port):
        return SeededMock(port=port, seed=seed, rounds=rounds)
    return factory


# ── 测试 1：世界生成器——确定性 / 种子独立 / 结构不变量 ────────


def test_world_schedule_properties():
    s1, zs1 = world_schedule(7, 12)
    s2, zs2 = world_schedule(7, 12)
    assert s1 == s2 and zs1 == zs2, "same seed must give identical schedule"
    seen_starts = set()
    for seed in (7, 42, 2077, 11, 22, 33):
        sched, zs = world_schedule(seed, 12)
        seen_starts.add(zs)
        assert 1 <= zs <= 3, zs
        assert len(sched) == 12
        for r, ents in enumerate(sched):
            kinds = [e["kind"] for e in ents]
            assert kinds[0] == "player", ents
            zombies = [e for e in ents if e["hp"] == 0]
            if r < zs:
                assert zombies == [], f"r{r} before zombie_start must be clean"
            else:
                assert len(zombies) >= 2, f"r{r} must carry >=2 zombies"
                combos = {tuple(sorted(e["labels"])) for e in zombies}
                assert len(combos) >= 2, "zombie combos must be distinct"
                assert all(e["kind"] == "npc" for e in zombies)
    assert len(seen_starts) >= 2, \
        f"seeds must vary zombie_start, got {seen_starts}"


# ── 测试 2：单种子全流程——文件布局 + 种子落档 + 口径产物 ──────


def test_single_seed_run_files_and_seed_recorded():
    with tempfile.TemporaryDirectory() as d:
        out_dir = Path(d) / "repeat"
        summary = repeat_experiment(
            seeds=[7], rounds=10, mock_factory=seeded_factory(10),
            config_path=str(CONFIG_PATH), out_dir=str(out_dir),
            base_port=20830, wait_timeout_s=5.0)

        assert summary["seeds"] == [7], summary["seeds"]
        assert summary["rounds_target"] == 10
        assert len(summary["runs"]) == 1
        run = summary["runs"][0]
        assert run["seed"] == 7
        assert run["interruption"] is False, run
        assert run["exp_converged_final"] is True, run
        assert run["virtual_lifespan"] == 10, run
        assert run["exp_disintegrated"] is False, run

        # —— 文件布局：每种子独立目录四件套 + 总汇 ——
        seed_dir = out_dir / "seed_7"
        for name in ("rounds.jsonl", "report.json",
                     "baseline_report.json", "comparison.json"):
            assert (seed_dir / name).exists(), f"missing {name}"
        assert (out_dir / "repeat_summary.json").exists()

        # —— 种子落档于报告 meta ——
        saved = json.loads((seed_dir / "report.json").read_text(encoding="utf-8"))
        assert saved["meta"]["seed"] == 7, saved["meta"]
        assert len(saved["axiom_final_status"]) == 3, \
            "each run must inject all 3 axioms (锁定决策 #9 每公理条件)"

        # —— 对照组口径产物（S18 契约）——
        cmp = json.loads((seed_dir / "comparison.json").read_text(encoding="utf-8"))
        assert cmp["compared_rounds"] >= 5, cmp["compared_rounds"]
        assert len(cmp["exp_series"]) == len(cmp["baseline_series"])
        assert cmp["baseline_mean"] <= cmp["exp_mean"], cmp

        # —— 归档行数 == 完成轮数 ——
        lines = [ln for ln in (seed_dir / "rounds.jsonl")
                 .read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 10, len(lines)


# ── 测试 3：三种子——复现性（同种子等签名）+ 独立性（异种子异签名）


def test_three_seeds_reproducibility_and_independence():
    seeds = [7, 42, 2077]
    with tempfile.TemporaryDirectory() as d:
        out1 = Path(d) / "run1"
        out2 = Path(d) / "run2"
        s1 = repeat_experiment(
            seeds=seeds, rounds=10, mock_factory=seeded_factory(10),
            config_path=str(CONFIG_PATH), out_dir=str(out1),
            base_port=20831, wait_timeout_s=5.0)
        s2 = repeat_experiment(
            seeds=seeds, rounds=10, mock_factory=seeded_factory(10),
            config_path=str(CONFIG_PATH), out_dir=str(out2),
            base_port=20831, wait_timeout_s=5.0)

        sigs1 = {r["seed"]: r["trajectory"] for r in s1["runs"]}
        sigs2 = {r["seed"]: r["trajectory"] for r in s2["runs"]}
        for seed in seeds:
            assert sigs1[seed] == sigs2[seed], \
                f"seed {seed} must reproduce identical trajectory"

        keys = list(seeds)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                assert sigs1[keys[i]] != sigs1[keys[j]], \
                    f"seeds {keys[i]} and {keys[j]} must differ"


# ── 测试 4：收敛趋势预期 + ≥3 种子统计口径 ─────────────────────


def test_trend_expectations_and_seed_count():
    seeds = [11, 22, 33]
    with tempfile.TemporaryDirectory() as d:
        out_dir = Path(d) / "repeat"
        summary = repeat_experiment(
            seeds=seeds, rounds=10, mock_factory=seeded_factory(10),
            config_path=str(CONFIG_PATH), out_dir=str(out_dir),
            base_port=20840, wait_timeout_s=5.0)

        trend = summary["trend"]
        # 锁定决策 #9：每公理条件 ≥3 个独立种子
        assert trend["seeds_count"] >= 3, trend
        # 趋势预期：实验组各种子降级后二次收敛全真
        assert trend["exp_converged_all"] is True, trend
        assert trend["exp_converged_count"] == 3, trend
        # 默认预算下无解体，虚拟寿命 = 目标轮数
        assert all(v == 10 for v in trend["virtual_lifespans"].values()), trend
        assert all(c is None for c in trend["disintegrations"].values()), trend
        # 对照组均值不优于实验组（本剧本族）
        assert trend["baseline_mean_le_exp_all"] is True, trend
        # 总汇落盘可复现
        saved = json.loads((out_dir / "repeat_summary.json")
                           .read_text(encoding="utf-8"))
        assert saved["trend"]["exp_converged_all"] is True
        assert sorted(int(k) for k in saved["trend"]["virtual_lifespans"]) == seeds


if __name__ == "__main__":
    check("world_schedule_properties", test_world_schedule_properties)
    check("single_seed_run_files_and_seed_recorded",
          test_single_seed_run_files_and_seed_recorded)
    check("three_seeds_reproducibility_and_independence",
          test_three_seeds_reproducibility_and_independence)
    check("trend_expectations_and_seed_count",
          test_trend_expectations_and_seed_count)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
