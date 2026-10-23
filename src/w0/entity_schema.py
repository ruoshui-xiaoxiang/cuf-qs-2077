ENTITY_FIELDS = ("entity_id", "kind", "labels", "hp", "position")
EVENT_FIELDS = ("event_id", "kind", "source_entity", "target_entity", "game_ts")
POSITION_FIELDS = ("x", "y", "z")
KNOWN_ENTITY_KINDS = ("player", "npc", "device", "vehicle", "item", "unknown")
KNOWN_EVENT_KINDS = ("entity_appeared", "entity_disappeared", "hp_changed",
                     "combat_attack", "intrusion_attempt", "area_alert",
                     "entity_death",
                     # native 通道派生事件（CufNative get_state 轮间差分，
                     # D3 构成② wanted 信号入 W0 词表）
                     "heat_changed", "chase_changed")

# ── 审计 C2：W0 数据链路 Schema 契约（TypedDict，纯类型标注）──
# 运行时校验由 validate_entity / validate_event 执行；TypedDict 作为
# 跨层传输的结构化契约声明（反套壳铁则 #2：数据含义由字段明确定义）。

from typing import List, Optional, TypedDict

from common.utils import is_num


class PositionDict(TypedDict):
    x: float
    y: float
    z: float


class EntityDict(TypedDict):
    entity_id: str
    kind: str
    labels: List[str]
    hp: float
    position: PositionDict
    sampling_status: Optional[str]


class EventDict(TypedDict):
    event_id: str
    kind: str
    source_entity: str
    target_entity: Optional[str]
    game_ts: float
    round_id: Optional[int]
    collection_ts: Optional[float]


class W0RoundDict(TypedDict):
    round_id: int
    collection_ts: float
    entities: List[EntityDict]
    events: List[EventDict]
    stats: dict
    native_state: Optional["NativeStateDict"]


class NativeStateDict(TypedDict):
    """CufNative 通道轮级状态快照（get_state 直读）。
    available=False 时其余字段不保证存在（通道不可用口径）。"""
    available: bool
    heat_stage: int
    chasing_player: bool
    in_combat: bool
    incapacitated: bool
    position: PositionDict


def validate_entity(ent):
    if not isinstance(ent, dict):
        return [f"not a dict: {type(ent).__name__}"]
    errors = []
    for f in ENTITY_FIELDS:
        if f not in ent:
            errors.append(f"missing field: {f}")
    if errors:
        return errors
    if not isinstance(ent["entity_id"], str) or not ent["entity_id"]:
        errors.append("entity_id must be non-empty str")
    if not isinstance(ent["kind"], str):
        errors.append("kind must be str")
    labels = ent["labels"]
    if not isinstance(labels, list) or any(not isinstance(x, str) for x in labels):
        errors.append("labels must be list of str")
    if not is_num(ent["hp"]):
        errors.append("hp must be number")
    pos = ent["position"]
    if not isinstance(pos, dict):
        errors.append("position must be dict")
    else:
        for k in POSITION_FIELDS:
            if not is_num(pos.get(k)):
                errors.append(f"position.{k} must be number")
    return errors


def validate_event(ev):
    if not isinstance(ev, dict):
        return [f"not a dict: {type(ev).__name__}"]
    errors = []
    for f in EVENT_FIELDS:
        if f not in ev:
            errors.append(f"missing field: {f}")
    if errors:
        return errors
    if not isinstance(ev["event_id"], str) or not ev["event_id"]:
        errors.append("event_id must be non-empty str")
    if not isinstance(ev["kind"], str):
        errors.append("kind must be str")
    if not isinstance(ev["source_entity"], str) or not ev["source_entity"]:
        errors.append("source_entity must be non-empty str")
    if ev["target_entity"] is not None and not isinstance(ev["target_entity"], str):
        errors.append("target_entity must be str or null")
    if not is_num(ev["game_ts"]):
        errors.append("game_ts must be number")
    return errors


def is_valid_entity(ent):
    return not validate_entity(ent)


def is_valid_event(ev):
    return not validate_event(ev)
