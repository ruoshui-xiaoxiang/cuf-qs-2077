"""gate.py —— 钩子写权限闸门（按"能不能当证据"分级，不按次数一刀切）

裁定依据不是我想出来的，是你自己在 cuf-qs 里落过的两条结论：

1. 2026-08-24 ops_notes：HP 直写能把 100→80，但 attack_debug 里 16 条 OnHit
   全是环境战斗、目标一次没中，无动画/无布娃娃/无 AI 反应 —— 你判定它
   不构成 L3 外部证据，并否掉了这条路（"静默移除"）。
2. config/experiment_config.json planner._note：2026-08-27 控制面分层收口，
   kill 保留为 native 实验性增强动作，但**默认移出 planner 自主白名单**，
   "避免未确认死亡链路混入正式主线"。

所以闸门这么切：

  动作        档                每场配额   证据上限
  ----------  ----------------  ---------  ----------------------------
  move        reach（摆局面）    无限       observable（位移可自证）
  pickup      reach              2          conditional（移出窗口≠真拾取）
  attack      outcome_write      3          conditional（钩子写，非游戏因果）
  kill        forbidden          0          ——（静默移除，你已否决）

配额用完不是"不准动"，而是降回 move —— 让它被迫用可观察的方式推进，
这正是以后接键鼠输入通道时的替身。任何走 attack/pickup 写出来的结果，
归档里都带 evidence_ceiling=conditional，下游不得写成 confirmed。
"""

TIERS = {
    "move":   {"tier": "reach", "quota": None, "ceiling": "observable"},
    "pickup": {"tier": "reach", "quota": 2, "ceiling": "conditional"},
    "attack": {"tier": "outcome_write", "quota": 3, "ceiling": "conditional"},
    "kill":   {"tier": "forbidden", "quota": 0, "ceiling": "none"},
}


class WriteGate:
    def __init__(self, quotas=None):
        self.tiers = {k: dict(v) for k, v in TIERS.items()}
        for action, q in (quotas or {}).items():        # 允许逐场覆盖配额
            if action in self.tiers:
                self.tiers[action]["quota"] = q
        self.used = {a: 0 for a in self.tiers}

    def decide(self, action_type):
        """返回 (ok, labels)；labels 进归档，供下游按证据上限裁决。"""
        spec = self.tiers.get(action_type)
        if spec is None:
            return False, {"tier": "unknown", "evidence_ceiling": "none",
                           "gate_reason": "gate_unknown_action"}
        if spec["tier"] == "forbidden":
            return False, {"tier": "forbidden", "evidence_ceiling": "none",
                           "gate_reason": "gate_forbidden_silent_removal"}
        quota = spec["quota"]
        if quota is not None and self.used[action_type] >= quota:
            return False, {"tier": spec["tier"], "evidence_ceiling": "none",
                           "gate_reason": "gate_quota_exceeded"}
        self.used[action_type] += 1
        return True, {"tier": spec["tier"],
                      "evidence_ceiling": spec["ceiling"],
                      "gate_reason": "allowed"}

    def snapshot(self):
        return {"quota_config": {a: s["quota"] for a, s in self.tiers.items()},
                "used": dict(self.used)}
