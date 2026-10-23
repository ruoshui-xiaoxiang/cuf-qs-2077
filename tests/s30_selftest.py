"""S30 操作层接入 CufNative 事件流离线自测。

契约（本轮锁定）：
- kill 为 W3 白名单动作，走 native 通道（127.0.0.1:20771），半径制，
  无位置约束；证据三段闭环：回执（kill_receipt）→ list_npcs 只读
  复检目标 entityHash 消失 → entity_death（EID:<hash> 口径）；目标
  未消失 → unconfirmed，不伪造死亡（铁律 #1/#2）。
- W0 sampler 直读 get_state：native_state 快照 + 轮间差分派生
  heat_changed / chase_changed 事件；通道失败 → available:False，
  无派生事件，闭环不崩。
- planner 解析 {"action_type":"kill","params":{"radius":N}}，
  白名单外动作拒绝。
"""
import json
import socket
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import w3.action_executor as aex
from w3.action_executor import ActionExecutor
from w3.action_planner import ActionPlanner, parse_plan_json
from w0.data_sampler import DataSampler
from common.native_client import NativeClient, NativeUnavailableError

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


# ── mock CufNative 服务端（临时端口，单行命令 → 单行 JSON）─────


class MockNativeServer:
    """可编程 native 通道：get_state 状态可切换；
    kill_nearby/list_npcs 可模拟击杀生效/未生效两种场景。"""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.state = {"cmd": "get_state", "heatStage": 0,
                      "chasingPlayer": False, "inCombat": False,
                      "isIncapacitated": False,
                      "posX": 1.0, "posY": 2.0, "posZ": 3.0}
        self.npcs = [{"entityHash": 10786193, "hostile": True,
                      "dist": 10.5, "x": 1.0, "y": 2.0, "z": 3.0}]
        self.kill_effective = True   # False = 击杀未生效（目标仍在）
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(timeout=2)

    def _loop(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(2.0)
                line = conn.recv(4096).decode("utf-8", errors="replace").strip()
                resp = self._handle(line)
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except OSError:
                pass
            finally:
                conn.close()

    def _handle(self, line):
        parts = line.split()
        cmd = parts[0] if parts else ""
        if cmd == "get_state":
            return dict(self.state)
        if cmd == "kill_nearby":
            if not self.npcs:
                return {"cmd": "kill_nearby", "npcCount": 0}
            tgt = next((n for n in self.npcs if n["hostile"]), self.npcs[0])
            if self.kill_effective:
                self.npcs = [n for n in self.npcs
                             if n["entityHash"] != tgt["entityHash"]]
            return {"cmd": "kill_nearby", "npcCount": len(self.npcs) + 1,
                    "method": "GetNPCsAroundObject(hostile)",
                    "entityHash": tgt["entityHash"],
                    "killMethod": "RequestSettingStatPoolValue(Health=0)",
                    "isHostile": tgt["hostile"]}
        if cmd == "list_npcs":
            return {"cmd": "list_npcs",
                    "npcs": [dict(n) for n in self.npcs],
                    "npcCount": len(self.npcs)}
        return {"error": "unknown cmd", "got": cmd}


class DummyConnector:
    """kill 路径不经 connector；sampler 只取空的实体/事件流。"""

    def execute_action(self, fn, params=None):
        return {"entities": [], "events": []}


def _fast_evidence():
    """把击杀复检的等待缩到毫秒级（测试不烧真实时间）。"""
    aex.KILL_EVIDENCE_DELAY_S = 0.02
    aex.KILL_EVIDENCE_RETRY_S = 0.01


# ── 测试 1：kill 白名单放行与非法参数拒绝 ──────────────────


def test_kill_whitelist_and_params():
    assert "kill" in ActionExecutor.allowed_actions()
    ex = ActionExecutor(DummyConnector(), native_client=None)
    for bad in ({"radius": 0}, {"radius": -5}, {"radius": 999},
                {"radius": "big"}, {"radius": True}):
        r = ex.execute("kill", bad)
        assert r["status"] == "rejected", (bad, r)
        assert str(r["reject_reason"]).startswith("invalid_params"), (bad, r)
    r = ex.execute("kill", {})  # radius 缺省 → 默认 50，参数校验放行
    assert r["reject_reason"] != "invalid_params:radius must be number", r
    r = ex.execute("steal", {})
    assert r["status"] == "rejected" and r["reject_reason"] == "not_in_whitelist"


# ── 测试 2：击杀生效 → kill_receipt + entity_death 证据 ─────


def test_kill_effective_evidence():
    srv = MockNativeServer()
    try:
        _fast_evidence()
        client = NativeClient(port=srv.port, timeout_s=2.0)
        ex = ActionExecutor(DummyConnector(), kill_timeout_s=2.0,
                            native_client=client)
        h = srv.npcs[0]["entityHash"]
        r = ex.execute("kill", {"radius": 20})
        assert r["status"] == "completed" and r["accepted"], r
        assert r["target_entity"] == f"EID:{h}", r
        kinds = [e["kind"] for e in r["evidence"]]
        assert kinds == ["kill_receipt", "entity_death"], kinds
        receipt = r["evidence"][0]
        assert receipt["kill_method"] == \
            "RequestSettingStatPoolValue(Health=0)", receipt
        death = r["evidence"][1]
        assert death["confirmed_by"] == "list_npcs"
        assert not death.get("unconfirmed"), death
        assert death["target_entity"] == f"EID:{h}", death
    finally:
        srv.stop()


# ── 测试 3：击杀未生效 → unconfirmed，无伪证据 ─────────────


def test_kill_ineffective_unconfirmed():
    srv = MockNativeServer()
    try:
        _fast_evidence()
        srv.kill_effective = False
        client = NativeClient(port=srv.port, timeout_s=2.0)
        ex = ActionExecutor(DummyConnector(), kill_timeout_s=2.0,
                            native_client=client)
        r = ex.execute("kill", {"radius": 20})
        deaths = [e for e in r["evidence"] if e["kind"] == "entity_death"]
        assert len(deaths) == 1, r["evidence"]
        assert deaths[0].get("unconfirmed") is True, deaths
        assert not any(not e.get("unconfirmed")
                       for e in deaths), "不得伪造已确认死亡"
    finally:
        srv.stop()


# ── 测试 4：无目标回执 → no_target_found（不进入复检）────────


def test_kill_no_target():
    srv = MockNativeServer()
    try:
        srv.npcs = []
        client = NativeClient(port=srv.port, timeout_s=2.0)
        ex = ActionExecutor(DummyConnector(), kill_timeout_s=2.0,
                            native_client=client)
        r = ex.execute("kill", {"radius": 20})
        assert r["status"] == "error", r
        assert r["reject_reason"] == "no_target_found", r
        assert r["evidence"] == [], r
    finally:
        srv.stop()


# ── 测试 5：sampler 派生 heat_changed（0→1→0）+ chase_changed ─


def test_sampler_derived_events():
    srv = MockNativeServer()
    try:
        client = NativeClient(port=srv.port, timeout_s=2.0)
        sampler = DataSampler(DummyConnector(), native_client=client)
        r1 = sampler.collect_round(1)
        assert r1["native_state"]["available"] is True
        assert r1["native_state"]["heat_stage"] == 0
        assert not [e for e in r1["events"]
                    if e["kind"] == "heat_changed"], "首轮只记基线"
        srv.state["heatStage"] = 1
        srv.state["chasingPlayer"] = True
        r2 = sampler.collect_round(2)
        heat = [e for e in r2["events"] if e["kind"] == "heat_changed"]
        chase = [e for e in r2["events"] if e["kind"] == "chase_changed"]
        assert len(heat) == 1 and heat[0]["from"] == 0 \
            and heat[0]["to"] == 1, r2["events"]
        assert heat[0]["source"] == "native" \
            and heat[0]["source_entity"] == "player", heat
        assert heat[0]["round_id"] == 2, heat
        assert len(chase) == 1 and chase[0]["from"] is False \
            and chase[0]["to"] is True, chase
        srv.state["heatStage"] = 0
        r3 = sampler.collect_round(3)
        heat3 = [e for e in r3["events"] if e["kind"] == "heat_changed"]
        assert len(heat3) == 1 and heat3[0]["from"] == 1 \
            and heat3[0]["to"] == 0, r3["events"]
    finally:
        srv.stop()


# ── 测试 6：native 不可用 → 不崩、标记跳过、无派生事件 ───────


def test_native_unavailable():
    # executor：kill 直接标 native_unavailable（fail-loud，不伪造）
    ex = ActionExecutor(DummyConnector(), native_client=None)
    r = ex.execute("kill", {"radius": 20})
    assert r["status"] == "error" and r["reject_reason"] == "native_unavailable", r
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()
    client = NativeClient(port=dead_port, timeout_s=0.5)
    ex2 = ActionExecutor(DummyConnector(), kill_timeout_s=1.0,
                         native_client=client)
    r2 = ex2.execute("kill", {"radius": 20})
    assert r2["status"] == "error"
    assert str(r2["reject_reason"]).startswith("native_unavailable"), r2
    # sampler：available:False，无派生事件，轮数据照常产出
    sampler = DataSampler(DummyConnector(), native_client=client)
    rd = sampler.collect_round(1)
    assert rd["native_state"] == {"available": False}, rd["native_state"]
    assert rd["events"] == [], rd["events"]
    # from_config：enabled=false → None（单一事实源）
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as f:
        json.dump({"native": {"enabled": False}}, f)
        cfg_path = f.name
    assert NativeClient.from_config(cfg_path) is None


# ── 测试 7：planner 解析 kill 建议 + 白名单外拒绝 ────────────


def test_planner_kill():
    p = ActionPlanner(mode="active")
    ok = parse_plan_json('{"action_type": "kill", "params": {"radius": 40}, '
                         '"reason": "清除敌对"}')
    assert ok and ok["action_type"] == "kill" \
        and ok["params"] == {"radius": 40}, ok
    v, reason = p.validate(ok, {})
    assert v, reason
    v2, reason2 = p.validate({"action_type": "kill",
                              "params": {"radius": 500}}, {})
    assert not v2 and "out of range" in reason2, (v2, reason2)
    v3, reason3 = p.validate({"action_type": "kill",
                              "params": {"radius": "all"}}, {})
    assert not v3 and "non-numeric" in reason3, (v3, reason3)
    v4, _ = p.validate({"action_type": "kill"}, {})  # 缺省半径放行
    assert v4
    v5, reason5 = p.validate({"action_type": "hack"}, {})
    assert not v5 and "whitelist" in reason5, (v5, reason5)
    # 字段级容错路径：行首决策串
    loose = parse_plan_json("kill # clear hostiles")
    assert loose and loose["action_type"] == "kill", loose


if __name__ == "__main__":
    check("kill_whitelist_and_params", test_kill_whitelist_and_params)
    check("kill_effective_evidence", test_kill_effective_evidence)
    check("kill_ineffective_unconfirmed", test_kill_ineffective_unconfirmed)
    check("kill_no_target", test_kill_no_target)
    check("sampler_derived_events", test_sampler_derived_events)
    check("native_unavailable", test_native_unavailable)
    check("planner_kill", test_planner_kill)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
