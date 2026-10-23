import json
import logging
import time
from pathlib import Path

from w0.entity_schema import validate_entity, validate_event

log = logging.getLogger("data_sampler")

FAIL_STREAK_THRESHOLD = 3

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "experiment_config.json"


class DataSampler:
    def __init__(self, connector, fail_streak_threshold=FAIL_STREAK_THRESHOLD,
                 native_client=None):
        self.connector = connector
        self.fail_streak_threshold = int(fail_streak_threshold)
        self._fail_streak = {}
        self._last_valid = {}
        # CufNative 通道（D3 构成②）：直读通缉/战斗信号并轮间差分派生事件；
        # None = 通道未启用，轮数据不带 native_state 段（历史口径不变）。
        self.native_client = native_client
        self._last_native_heat = None
        self._last_native_chase = None

    @classmethod
    def from_config(cls, connector, config_path=None):
        """按配置构造：native 段 enabled 时注入 NativeClient（单一事实源）。"""
        from common.native_client import NativeClient
        path = config_path or DEFAULT_CONFIG_PATH
        native_client = None
        try:
            cfg = json.loads(Path(path).read_text(encoding="utf-8"))
            if cfg.get("native", {}).get("enabled"):
                native_client = NativeClient.from_config(path)
        except (OSError, ValueError) as e:
            log.warning("native config read failed, sampler without native: %s", e)
        return cls(connector, native_client=native_client)

    def collect_round(self, round_id):
        collection_ts = time.time()
        scan_resp = self.connector.execute_action("scan_entities")
        events_resp = self.connector.execute_action("get_events")
        raw_entities = scan_resp.get("entities") if isinstance(scan_resp, dict) else scan_resp
        raw_events = events_resp.get("events") if isinstance(events_resp, dict) else events_resp
        if not isinstance(raw_entities, list):
            raw_entities = []
        if not isinstance(raw_events, list):
            raw_events = []
        entities_out = []
        events_out = []
        stats = {
            "raw_entities": len(raw_entities),
            "raw_events": len(raw_events),
            "invalid_entities": 0,
            "invalid_events": 0,
            "failed_entities": 0,
            "anomalous_entities": 0,
            "ok_entities": 0,
        }
        for raw in raw_entities:
            errors = validate_entity(raw)
            if errors:
                stats["invalid_entities"] += 1
                record = self._record_failure(raw)
                if record is None:
                    continue
                entities_out.append(record)
                if record["sampling_status"] == "anomalous":
                    stats["anomalous_entities"] += 1
                else:
                    stats["failed_entities"] += 1
                log.warning("entity validation failed: %s (%s)", errors,
                            raw.get("entity_id") if isinstance(raw, dict) else None)
                continue
            ent_id = raw["entity_id"]
            self._fail_streak.pop(ent_id, None)
            self._last_valid[ent_id] = dict(raw)
            record = dict(raw)
            record["sampling_status"] = "ok"
            stats["ok_entities"] += 1
            entities_out.append(record)
        for raw in raw_events:
            errors = validate_event(raw)
            if errors:
                stats["invalid_events"] += 1
                log.warning("event validation failed: %s", errors)
                continue
            ev = dict(raw)
            ev["round_id"] = round_id
            ev["collection_ts"] = collection_ts
            events_out.append(ev)
        # ── native 通道：通缉/战斗信号快照 + 轮间差分派生事件 ──
        native_state, derived = self._poll_native(round_id, collection_ts)
        events_out.extend(derived)
        return {
            "round_id": round_id,
            "collection_ts": collection_ts,
            "entities": entities_out,
            "events": events_out,
            "stats": stats,
            "native_state": native_state,
        }

    def _poll_native(self, round_id, collection_ts):
        """读 CufNative get_state：结构化快照 + 轮间差分派生事件。
        通道失败 → available:False 快照、无派生事件、不静默伪造（铁律 #2）；
        首次成功轮只记基线不产事件（无可比较的前值）。
        """
        if self.native_client is None:
            return None, []
        try:
            st = self.native_client.get_state()
        except Exception as e:
            log.warning("native get_state failed: %s", e)
            return {"available": False}, []
        heat = st.get("heatStage")
        chase = st.get("chasingPlayer")
        heat = int(heat) if isinstance(heat, int) and not isinstance(heat, bool) else None
        chase = bool(chase)
        snap = {
            "available": True,
            "heat_stage": heat,
            "chasing_player": chase,
            "in_combat": bool(st.get("inCombat")),
            "incapacitated": bool(st.get("isIncapacitated")),
            "position": {"x": st.get("posX"), "y": st.get("posY"),
                         "z": st.get("posZ")},
        }
        derived = []
        seq = 0
        if heat is not None and self._last_native_heat is not None \
                and heat != self._last_native_heat:
            derived.append(self._native_event(
                "heat_changed", round_id, collection_ts, seq,
                from_value=self._last_native_heat, to_value=heat))
            seq += 1
        if self._last_native_chase is not None \
                and chase != self._last_native_chase:
            derived.append(self._native_event(
                "chase_changed", round_id, collection_ts, seq,
                from_value=self._last_native_chase, to_value=chase))
        self._last_native_heat = heat if heat is not None else self._last_native_heat
        self._last_native_chase = chase
        return snap, derived

    @staticmethod
    def _native_event(kind, round_id, collection_ts, seq, from_value, to_value):
        return {
            "event_id": f"native-{round_id}-{seq}-{kind}",
            "kind": kind,
            "source_entity": "player",
            "target_entity": None,
            "game_ts": collection_ts,
            "round_id": round_id,
            "collection_ts": collection_ts,
            "from": from_value,
            "to": to_value,
            "source": "native",
        }

    def _record_failure(self, raw):
        ent_id = raw.get("entity_id") if isinstance(raw, dict) else None
        if not isinstance(ent_id, str) or not ent_id:
            return None
        streak = self._fail_streak.get(ent_id, 0) + 1
        self._fail_streak[ent_id] = streak
        status = "anomalous" if streak >= self.fail_streak_threshold else "failed"
        base = self._last_valid.get(ent_id)
        if base is not None:
            record = dict(base)
            # 安全检查 B3：兜底记录显式标注来源（区分真实数据与回填）
            record["source"] = "last_valid_snapshot"
        else:
            record = {"entity_id": ent_id, "kind": "unknown", "labels": [],
                      "hp": 0.0, "position": {"x": 0.0, "y": 0.0, "z": 0.0}}
            record["source"] = "placeholder"
        record["sampling_status"] = status
        return record

    def reset_tracking(self):
        self._fail_streak.clear()
        self._last_valid.clear()
        self._last_native_heat = None
        self._last_native_chase = None
