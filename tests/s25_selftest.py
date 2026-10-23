"""S25 实时监控面板离线自测。

契约（S25 锁定）：
- 零第三方依赖（标准库 http.server），只读 JSONL/JSON 归档，不参与裁决。
- / 返回 monitor_ui.html；/api/config 返回数据源路径；
  /api/rounds 返回逐轮轻量视图（alignment/c_h/axioms/事件）；
  /api/report 返回批次末报告；/api/stats 返回 S22 统计（缺失 404）。
- 实时性：JSONL 追加新轮后 /api/rounds 立即反映（轮询语义）。
- 损坏 JSONL 行跳过（S6/D3 容错一致）。
"""
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w3.monitor import MonitorHandler, summarize_rounds

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


def make_round(round_id, align=0.9, extend=0):
    return {
        "round_id": round_id,
        "w0": {"entities": [{"entity_id": "EID:1", "kind": "player"}],
               "events": [{"event_id": f"e{round_id}", "kind": "hp_changed"}],
               "stats": {"raw_entities": 1, "raw_events": 1}},
        "w2": {"axioms": [
            {"id": "A_M1", "status": "active", "hypothesis": "攻击 NPC 会降低其 HP",
             "anchor": "P_M1", "boundary_conditions": []},
            {"id": "A_W2_5", "status": "warned",
             "hypothesis": "HP 归零的实体会死亡并消失",
             "anchor": "P_W2_5",
             "boundary_conditions": [{"kind": "npc"}]},
        ]},
        "w3": {"action": "attack:EID:1", "action_status": "completed",
               "action_channel": "lua",
               "action_control_tier": "conditional",
               "action_receipt_reason": "status_effect_applied",
               "action_outcome_semantics": "damage_observed",
               "action_confirmation_status": "confirmed",
               "alignment_score": align,
               "hypotheses": [{
                   "hypothesis_id": f"H{round_id}", "axiom_id": "A_M1",
                   "type": "boundary_refine",
                   "description": "建议为 A_M1 补充边界条件",
                   "is_counterexample": False,
               }],
               "downgrade_events": ([{"axiom_id": "A_W2_5", "count": 5,
                                      "threshold": 5, "from_status": "warned",
                                      "to_status": "deprecated"}]
                                    if round_id == 2 else []),
               "warn_events": []},
        "c_h": {"struct": 3, "adapt": 2, "extend": extend,
                "within_budget": True},
    }


class MonitorServer:
    def __init__(self, target_dir, port):
        self.log_path = Path(target_dir) / "rounds.jsonl"
        self.report_path = Path(target_dir) / "report.json"
        self.stats_path = Path(target_dir) / "stats" / "stats_summary.json"
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)
        # S29.19：handler 按请求动态解析数据源（注入目标字符串）
        MonitorHandler.target = str(target_dir)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), MonitorHandler)
        self.t = threading.Thread(target=self.httpd.serve_forever,
                                  daemon=True)
        self.t.start()
        self.base = f"http://127.0.0.1:{port}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=3) as r:
            return r.status, r.read()


def test_html_and_config():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "rounds.jsonl").write_text("", encoding="utf-8")
        srv = MonitorServer(d, 20950)
        try:
            st, body = srv.get("/")
            assert st == 200, st
            assert body.startswith(b"<!DOCTYPE html>"), body[:40]
            assert "CUF-QS 实验实时监控" in body.decode("utf-8")
            st, cfg = srv.get("/api/config")
            assert st == 200
            assert json.loads(cfg)["log_path"].endswith("rounds.jsonl")
        finally:
            srv.stop()


def test_rounds_parse_and_live_update():
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "rounds.jsonl"
        log.write_text(json.dumps(make_round(0)) + "\n", encoding="utf-8")
        srv = MonitorServer(d, 20951)
        try:
            st, body = srv.get("/api/rounds")
            assert st == 200
            data = json.loads(body)
            assert data["count"] == 1, data
            r0 = data["rounds"][0]
            assert r0["alignment_score"] == 0.9, r0
            assert r0["c_h"]["struct"] == 3, r0
            assert r0["axioms"]["A_W2_5"]["status"] == "warned", r0
            assert r0["action_control_tier"] == "conditional", r0
            assert r0["action_outcome_semantics"] == "damage_observed", r0
            assert r0["action_confirmation_status"] == "confirmed", r0
            assert r0["entities_count"] == 1 and r0["events_count"] == 1, r0

            # 实时性：JSONL 追加新轮后立即反映
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(make_round(1)) + "\n")
                f.write(json.dumps(make_round(2, extend=1)) + "\n")
            st, body = srv.get("/api/rounds")
            data = json.loads(body)
            assert data["count"] == 3, data["count"]
            r2 = data["rounds"][2]
            assert r2["downgrade_events"][0]["from_status"] == "warned", r2
            assert r2["downgrade_events"][0]["to_status"] == "deprecated", r2

            # 损坏行跳过
            with open(log, "a", encoding="utf-8") as f:
                f.write("{corrupt json\n")
            st, body = srv.get("/api/rounds")
            assert json.loads(body)["count"] == 3, "corrupt line must be skipped"
        finally:
            srv.stop()


def test_report_and_stats():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "rounds.jsonl").write_text("", encoding="utf-8")
        rep = {"completed": 3, "rounds_target": 3, "virtual_lifespan": 3,
               "criterion": "over_budget", "disintegrated": True,
               "system_dead": False}
        (Path(d) / "report.json").write_text(json.dumps(rep), encoding="utf-8")
        stats = {"mode": "s22_stats", "figures": {}}
        (Path(d) / "stats").mkdir(parents=True, exist_ok=True)
        (Path(d) / "stats" / "stats_summary.json").write_text(
            json.dumps(stats), encoding="utf-8")
        srv = MonitorServer(d, 20952)
        try:
            st, body = srv.get("/api/report")
            assert st == 200
            assert json.loads(body)["criterion"] == "over_budget"
            st, body = srv.get("/api/stats")
            assert st == 200
            assert json.loads(body)["mode"] == "s22_stats"
            # report 缺失 → 404
            (Path(d) / "report.json").unlink()
            try:
                srv.get("/api/report")
                raise AssertionError("must return 404")
            except urllib.error.HTTPError as e:
                assert e.code == 404, e.code
        finally:
            srv.stop()


def test_directory_and_file_targets():
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "rounds.jsonl"
        log.write_text(json.dumps(make_round(0)) + "\n", encoding="utf-8")
        # --dir 形态
        srv1 = MonitorServer(d, 20953)
        try:
            st, body = srv1.get("/api/rounds")
            assert json.loads(body)["count"] == 1
        finally:
            srv1.stop()
        # --log 单文件形态（resolve_sources 路径推导）
        from w3.monitor import resolve_sources
        lp, rp, sp = resolve_sources(log)
        assert lp == log and rp == log.parent / "report.json"
        assert sp == log.parent / "stats" / "stats_summary.json"


def test_summarize_rounds_contract():
    """summarize_rounds 输出 schema 契约（前端消费面）。"""
    rounds = [make_round(0), make_round(1)]
    out = summarize_rounds(rounds)
    assert len(out) == 2
    for r in out:
        for key in ("round_id", "alignment_score", "action", "action_status",
                    "action_channel", "action_control_tier",
                    "action_receipt_reason", "action_outcome_semantics",
                    "action_confirmation_status", "c_h", "axioms",
                    "hypotheses", "downgrade_events", "warn_events",
                    "entities_count", "events_count", "stats", "empty_scan",
                    "model_state_unavailable"):
            assert key in r, f"missing {key}"
        assert set(r["c_h"].keys()) == {"struct", "adapt", "extend",
                                        "within_budget"}
        assert r["action_channel"] == "lua"
        assert r["action_control_tier"] == "conditional"
        assert r["action_receipt_reason"] == "status_effect_applied"
        assert r["action_outcome_semantics"] == "damage_observed"
        assert r["action_confirmation_status"] == "confirmed"
        # 公理内容契约：假设文本 + 锚点 + 边界列表
        for a in r["axioms"].values():
            for key in ("status", "boundary_count", "hypothesis", "anchor",
                        "boundary_conditions"):
                assert key in a, f"axiom missing {key}"
        assert r["axioms"]["A_W2_5"]["hypothesis"] == "HP 归零的实体会死亡并消失"
        assert r["axioms"]["A_W2_5"]["anchor"] == "P_W2_5"
        assert r["axioms"]["A_W2_5"]["boundary_conditions"] == [{"kind": "npc"}]
        # 假设生成流契约
        assert len(r["hypotheses"]) == 1
        h = r["hypotheses"][0]
        for key in ("hypothesis_id", "axiom_id", "type", "description",
                    "is_counterexample"):
            assert key in h, f"hypothesis missing {key}"
        assert h["type"] == "boundary_refine"
    assert out[0]["empty_scan"] is False


def test_directory_appears_after_startup():
    """S29.19：目标目录后于面板启动出现 → 动态解析自动跟踪。

    根因场景复现（S29.18 实机面板空白）：面板先启、实机循环后创建
    日志目录——旧实现启动时解析一次并钉死路径，此后永远空白；
    动态解析须在文件出现/追加时实时反映。
    """
    with tempfile.TemporaryDirectory() as d:
        future = Path(d) / "later_run"
        MonitorHandler.target = str(future)
        httpd = ThreadingHTTPServer(("127.0.0.1", 20960), MonitorHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:20960"
        try:
            with urllib.request.urlopen(base + "/api/rounds", timeout=3) as r:
                assert json.loads(r.read())["count"] == 0
            future.mkdir(parents=True)
            (future / "rounds.jsonl").write_text(
                json.dumps(make_round(0)) + "\n", encoding="utf-8")
            with urllib.request.urlopen(base + "/api/rounds", timeout=3) as r:
                assert json.loads(r.read())["count"] == 1
            with (future / "rounds.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(make_round(1)) + "\n")
            with urllib.request.urlopen(base + "/api/rounds", timeout=3) as r:
                assert json.loads(r.read())["count"] == 2
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    check("html_and_config", test_html_and_config)
    check("rounds_parse_and_live_update", test_rounds_parse_and_live_update)
    check("report_and_stats", test_report_and_stats)
    check("directory_and_file_targets", test_directory_and_file_targets)
    check("directory_appears_after_startup", test_directory_appears_after_startup)
    check("summarize_rounds_contract", test_summarize_rounds_contract)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
