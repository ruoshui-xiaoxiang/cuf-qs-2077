"""W2 LLM 辅助模块——仅三件事：假设自然语言化、降级语义总结、语义矛盾参考排查。

反套壳铁则 #1（裁决权下沉）：
    本模块全部输出都明确标记"仅建议/无裁决权"，任何最终状态变更
    （active/warned/deprecated、降级触发、收敛判定、QS 对齐分）
    必须由独立硬编码模块（AxiomEngine / DowngradeManager /
    QSKernel / ICCAuditor）完成。本模块不写回任何核心状态。

反套壳铁则 #2（数据流解耦）：
    三接口一律输出结构化 dict（含 advisory/disclaimer/meta 字段）；
    自然语言字符串仅作为人类可读字段，不作为任何决策路径输入。

反套壳铁则 #3（可单测验证）：
    默认 use_model=False（离线模式），不加载模型、不联网，
    完全可脱离 7B 与网络独立运行，单测覆盖全部核心行为。
    from_config 未显式设置 llm.use_model=True 时强制 fallback 离线，
    禁止静默加载模型或联网。

S14 验收口径（project_rules.md）：
    1. hypothesis_nl(hypothesis_struct) → dict  假设自然语言化
    2. summarize_downgrade(downgrade_event)  → dict  降级语义总结
    3. check_semantic_contradiction(axioms)  → dict  语义矛盾参考排查
    全部输出带明确"无裁决权"标记，语义排查明确以 ICCAuditor 审计结果为最终依据。
"""

import json
import logging
import os
import time

log = logging.getLogger("llm_assistant")


DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "experiment_config.json"
)


# 裁决类短语——输出任何位置都不得出现（或必须被剥离）
_VERDICT_PHRASES = [
    "触发降级", "触发预警", "裁决", "判定为", "判定通过", "判定不通过",
    "确认降级", "执行降级", "否决", "通过公理", "公理有效", "公理无效",
    "公理通过", "公理未通过", "系统判定",
]


_DISCLAIMER_COMMON = (
    "【仅建议·无裁决权】本内容由 LLM 辅助模块生成，仅供科研工作者阅读参考，"
    "不得直接用于公理状态变更、降级触发、QS 收敛判定、ICC 审计等核心决策。"
    "所有裁决以 AxiomEngine / DowngradeManager / QSKernel / ICCAuditor "
    "四个硬编码独立模块的结构化输出为准。"
)

_DISCLAIMER_ICC_AUDITOR = (
    "【仅参考】语义矛盾排查为启发式建议，可能存在误报/漏报。正式一致性与"
    "矛盾判定请以 ICCAuditor 的结构化审计报告为最终依据。"
)


def _strip_verdict(text):
    """把输入中带的裁决词替换为非裁决描述（上游数据可能包含裁决字眼，
    但辅助模块不能把它们直接搬出来——反套壳铁则 #1 严格性。）"""
    if text is None:
        return ""
    s = str(text)
    s = s.replace("系统判定触发降级预警", "上游记录显示反例计数已达到预警阈值")
    s = s.replace("触发降级", "反例计数达到降级阈值")
    s = s.replace("触发预警", "反例计数达到预警阈值")
    s = s.replace("系统判定", "上游结构化记录显示")
    for w in _VERDICT_PHRASES:
        if w in s:
            s = s.replace(w, "（参考说明）")
    return s


class LLMAssistant:
    """LLM 辅助器——离线模式（默认）与实机模式（use_model=True）双路。

    离线模式：按规则模板生成自然语言文本，保证可单测、无外部依赖、无随机。
    实机模式：（预留接口，S14 不强制落地，若 use_model=True 但模型不可用，
    自动 fallback 离线并写入 meta.fallback_note。）
    """

    MODE_OFFLINE = "offline"
    MODE_LIVE = "live"

    def __init__(self, use_model=False, model_adapter=None, config=None):
        self.use_model = bool(use_model)
        self.model_adapter = model_adapter
        self._config = config or {}

        # 锁定到模式：若模型不可用，fallback 离线
        if self.use_model and self.model_adapter is None:
            self.mode = self.MODE_OFFLINE
            self._fallback_note = "use_model=True 但 model_adapter 缺失，自动 fallback 离线"
        else:
            self.mode = self.MODE_LIVE if self.use_model else self.MODE_OFFLINE
            self._fallback_note = ""

    # ── 构造器：from_config ─────────────────────────────────

    @classmethod
    def from_config(cls, config):
        """从 experiment_config.json（dict 或 path）构造。

        核心约束（反套壳铁则 #3 静默联网防护）：
        - 若 config 中没有显式的 llm.use_model=True，强制 use_model=False；
        - 绝不因缺省去加载模型或发起网络请求。
        """
        if isinstance(config, (str, os.PathLike)):
            with open(config, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        elif isinstance(config, dict):
            cfg = config
        else:
            raise TypeError(f"from_config: config must be dict or path, got {type(config).__name__}")

        llm_cfg = cfg.get("llm", {}) or {}
        use_model = bool(llm_cfg.get("use_model", False))
        # 显式 True 才走实机，其他一律离线（防字段缺失静默加载模型）
        if not use_model:
            return cls(use_model=False, config=cfg)

        # use_model=True：尝试导入 model_adapter（S13），不加载模型本身；
        # 缺失则 fallback 离线。
        try:
            from w2.model_adapter import ModelAdapter  # noqa: F401
            # S14 默认不自动构造模型（节省显存），留待上层决定何时加载。
            return cls(use_model=True, model_adapter=None, config=cfg)
        except Exception:
            return cls(use_model=False, config=cfg)

    # ── 工具：统一 meta / disclaimer ───────────────────────

    def attach_model_adapter(self, adapter):
        """上层接入已加载的模型适配器（审计 A1：live 分支入口）。

        仅在 use_model=True 时置 live 模式；adapter 须实现 loaded 属性
        与 generate_text(prompt) -> str（ModelAdapter 已满足）。
        """
        self.model_adapter = adapter
        if self.use_model and adapter is not None:
            self.mode = self.MODE_LIVE
            self._fallback_note = ""
        return self.mode

    def _live_generate(self, prompt):
        """live 生成：adapter 就绪则调用模型，失败回退（返回 None）。

        裁决权铁则不变：即使 live 成功，输出仍须经 _strip_verdict
        剥离裁决词并打 advisory 标记；失败绝不抛错，静默回退离线模板
        并写入 meta.fallback_note（可追溯）。
        """
        if not (self.use_model and self.model_adapter is not None
                and getattr(self.model_adapter, "loaded", False)):
            return None
        try:
            return self.model_adapter.generate_text(prompt)
        except Exception as e:
            log.warning("LLM live generation failed, fallback offline: %s", e)
            self._fallback_note = f"live generation failed, fallback offline: {e}"
            return None

    def _base_meta(self):
        meta = {
            "mode": self.mode,
            "generated_ts": time.time(),
            "generator": "LLMAssistant",
            "advisory": True,
        }
        if self._fallback_note:
            meta["fallback_note"] = self._fallback_note
        return meta

    @staticmethod
    def _common_disclaimer():
        return _DISCLAIMER_COMMON

    # ── 接口 1：假设自然语言化 ──────────────────────────────

    def hypothesis_nl(self, hypothesis_struct):
        """将结构化假设转写为自然语言可读形式。

        Args:
            hypothesis_struct (dict): 必需字段：axiom_id / anchor / predicate；
                可选：predicted_condition / boundary_conditions / 其他。

        Returns:
            dict {text, advisory=True, disclaimer, meta}
        """
        if not isinstance(hypothesis_struct, dict):
            raise TypeError(
                f"hypothesis_nl: hypothesis_struct must be dict, got {type(hypothesis_struct).__name__}")

        req = ["axiom_id", "anchor", "predicate"]
        miss = [k for k in req if k not in hypothesis_struct or hypothesis_struct[k] in (None, "")]
        if miss:
            raise ValueError(
                f"hypothesis_nl: missing required fields: {miss}")

        axiom_id = hypothesis_struct["axiom_id"]
        anchor = hypothesis_struct["anchor"]
        predicate = _strip_verdict(hypothesis_struct["predicate"])
        predicted = hypothesis_struct.get("predicted_condition")
        boundaries = hypothesis_struct.get("boundary_conditions") or []

        lines = []
        lines.append(f"【假设 · {axiom_id}（锚点 {anchor}）】")
        lines.append(f"  主张：{predicate}")
        if predicted:
            lines.append(f"  预期观测：{_strip_verdict(predicted)}")
        if boundaries:
            lines.append("  适用边界：")
            for i, bc in enumerate(boundaries, 1):
                if isinstance(bc, dict):
                    pieces = []
                    for k, v in bc.items():
                        pieces.append(f"{k}={v}")
                    lines.append(f"    B{i}. " + "; ".join(pieces))
                else:
                    lines.append(f"    B{i}. {_strip_verdict(str(bc))}")

        text = "\n".join(lines)

        # 审计 A1：live 分支（adapter 就绪时由 7B 生成自然语言）
        live_text = self._live_generate(
            "你是实验日志的自然语言转写助手。将以下结构化实验假设转写为"
            "简洁的人类可读描述，仅作阅读说明，不得包含任何判定、结论"
            "或决策建议。\n假设结构："
            + json.dumps(hypothesis_struct, ensure_ascii=False)
            + "\n转写：")
        if live_text:
            text = _strip_verdict(live_text)

        # 最终再扫一遍全文，避免边界条件里带裁决词
        text = _strip_verdict(text)

        meta = self._base_meta()
        meta.update({
            "kind": "hypothesis_nl",
            "axiom_id": axiom_id,
            "anchor": anchor,
            "boundary_count": len(boundaries),
            "model_generated": live_text is not None,
        })

        return {
            "text": text,
            "advisory": True,
            "disclaimer": self._common_disclaimer(),
            "meta": meta,
        }

    # ── 接口 2：降级事件语义总结 ────────────────────────────

    def summarize_downgrade(self, downgrade_event):
        """对降级/预警结构化事件生成自然语言总结与要点。

        Args:
            downgrade_event (dict): 必需字段：axiom_id / from_status / to_status。
                可选：reason / counterexamples_used / boundary_added / round_id。

        Returns:
            dict {summary, key_points, advisory=True, disclaimer, meta}
        """
        if not isinstance(downgrade_event, dict):
            raise TypeError(
                f"summarize_downgrade: event must be dict, got {type(downgrade_event).__name__}")

        req = ["axiom_id", "from_status", "to_status"]
        miss = [k for k in req if k not in downgrade_event or downgrade_event[k] in (None, "")]
        if miss:
            raise ValueError(
                f"summarize_downgrade: missing required fields: {miss}")

        axiom_id = downgrade_event["axiom_id"]
        from_s = downgrade_event["from_status"]
        to_s = downgrade_event["to_status"]
        reason_raw = downgrade_event.get("reason", "") or ""
        ce_used = downgrade_event.get("counterexamples_used") or []
        bc_added = downgrade_event.get("boundary_added") or []
        round_id = downgrade_event.get("round_id")

        # 清洗上游可能带的裁决性描述
        reason_clean = _strip_verdict(str(reason_raw))
        if not reason_clean:
            reason_clean = f"从 {from_s} 切换到 {to_s}"

        kp = []
        kp.append(f"公理 {axiom_id} 的记录显示状态从 {from_s} 调整为 {to_s}。")
        kp.append(f"附带说明：{reason_clean}。")
        if ce_used:
            samples = []
            for e in ce_used[:3]:
                if isinstance(e, dict):
                    parts = []
                    if e.get("labels"):
                        parts.append(f"标签{e['labels']}")
                    if e.get("round_id") is not None:
                        parts.append(f"轮次{e['round_id']}")
                    samples.append("+".join(parts) if parts else "样本")
                else:
                    samples.append(str(e))
            tail = f" 等共 {len(ce_used)} 条" if len(ce_used) > 3 else ""
            kp.append(f"本次参考样本：{', '.join(samples)}{tail}。")
        if bc_added:
            items = [_strip_verdict(str(b)) for b in bc_added]
            kp.append(f"新纳入边界参考：{'; '.join(items)}。")
        if round_id is not None:
            kp.append(f"发生轮次：round_id={round_id}。")

        summary = (
            f"关于公理 {axiom_id}：记录显示状态由 {from_s} 过渡到 {to_s}；"
            f"依据：{reason_clean}；参考样本 {len(ce_used)} 条，"
            f"新增边界参考 {len(bc_added)} 项。本总结仅作阅读理解。"
        )
        # 审计 A1：live 分支（adapter 就绪时由 7B 生成自然语言）
        live_text = self._live_generate(
            "你是实验日志的自然语言总结助手。将以下降级事件结构化记录"
            "总结为简洁的人类可读说明，仅作阅读参考，不得包含任何判定"
            "或决策建议。\n事件记录："
            + json.dumps({
                "axiom_id": axiom_id, "from_status": from_s,
                "to_status": to_s, "reason": reason_raw,
                "counterexamples_used": len(ce_used),
                "boundary_added": len(bc_added), "round_id": round_id},
                ensure_ascii=False)
            + "\n总结：")
        if live_text:
            summary = _strip_verdict(live_text)
        summary = _strip_verdict(summary)

        # 最终对每个要点再洗一遍
        kp = [_strip_verdict(p) for p in kp]

        meta = self._base_meta()
        meta.update({
            "kind": "summarize_downgrade",
            "axiom_id": axiom_id,
            "from_status": from_s,
            "to_status": to_s,
            "counterexample_count": len(ce_used),
            "boundary_added_count": len(bc_added),
            "model_generated": live_text is not None,
        })
        if round_id is not None:
            meta["round_id"] = round_id

        return {
            "summary": summary,
            "key_points": kp,
            "advisory": True,
            "disclaimer": self._common_disclaimer(),
            "meta": meta,
        }

    # ── 接口 3：语义矛盾参考排查 ────────────────────────────

    def check_semantic_contradiction(self, axioms):
        """启发式排查公理假设文本之间的弱语义冲突——【仅参考】。

        规则（完全硬编码启发式，不调用 LLM，离线可测）：
        - 每条公理需要 axiom_id / hypothesis，缺则抛错。
        - 关键词冲突对（可扩展）：
            {"不攻击", "反击", "攻击"}
            {"不持有武器", "持有武器"}
            {"无敌对", "敌对"}
          若两条不同公理的 hypothesis 命中同一冲突组的对立两边，
          产出一条 suggestion（severity=medium/high）。
        - 单条公理若 hypothesis 长度 < 4 或含歧义占位符 → low 建议。

        最终判定必须以 ICCAuditor.audit() 的结构化输出为准。

        Args:
            axioms (list[dict]): 每个元素至少 axiom_id / hypothesis。

        Returns:
            dict {suggestions, found_any, advisory=True, disclaimer, meta}
        """
        if not isinstance(axioms, list):
            raise TypeError(
                f"check_semantic_contradiction: axioms must be list, got {type(axioms).__name__}")

        clean_axioms = []
        for a in axioms:
            if not isinstance(a, dict):
                raise ValueError(
                    f"check_semantic_contradiction: axiom item must be dict")
            if "axiom_id" not in a or a["axiom_id"] in (None, ""):
                raise ValueError(
                    "check_semantic_contradiction: axiom missing required field 'axiom_id'")
            hypothesis = a.get("hypothesis") or ""
            clean_axioms.append((a["axiom_id"], _strip_verdict(str(hypothesis))))

        # 冲突组：组内关键词彼此视为弱冲突（只给建议，不做裁决）
        conflict_groups = [
            (("不攻击",), ("攻击", "反击"), "high"),
            (("不持有武器",), ("持有武器", "配枪"), "high"),
            (("无敌对", "非敌对"), ("敌对", "敌视"), "medium"),
            (("不受伤害",), ("受到伤害", "受伤"), "medium"),
            (("不移动",), ("移动", "瞬移", "巡逻"), "low"),
        ]

        suggestions = []

        def has_any(text, kws):
            return any(k in text for k in kws)

        n = len(clean_axioms)
        for i in range(n):
            ai, hi = clean_axioms[i]
            # 单条质量建议
            if len(hi) < 4 or "TODO" in hi or "xxx" in hi.lower():
                suggestions.append({
                    "severity": "low",
                    "axiom_ids": [ai],
                    "reason": f"公理 {ai} 的 hypothesis 文本疑似占位或过短（{len(hi)} 字符），建议补全。",
                    "suggested_action": "人工审核 hypothesis 文本并确认；确认后无需任何自动变更。",
                })

            for j in range(i + 1, n):
                aj, hj = clean_axioms[j]
                for side_a, side_b, sev in conflict_groups:
                    a_hit = has_any(hi, side_a) and not has_any(hi, side_b)
                    b_hit = has_any(hj, side_b) and not has_any(hj, side_a)
                    # 两边交叉命中 → 建议
                    if (a_hit and b_hit) or (
                        has_any(hi, side_b) and not has_any(hi, side_a)
                        and has_any(hj, side_a) and not has_any(hj, side_b)
                    ):
                        suggestions.append({
                            "severity": sev,
                            "axiom_ids": [ai, aj],
                            "reason": (
                                f"公理 {ai}（“{hi}”）与 {aj}（“{hj}”）在关键词对 "
                                f"({list(side_a)} vs {list(side_b)}) 上构成潜在语义方向相反。"
                            ),
                            "suggested_action": (
                                "人工审核两条公理的 hypothesis 与边界条件是否相容；"
                                "任何修改须通过 axiom_engine.update_axiom_boundary 结构化记录，不接受本建议直接改。"
                            ),
                        })
                        break  # 同一对只报一次

        found_any = len(suggestions) > 0

        meta = self._base_meta()
        meta.update({
            "kind": "semantic_contradiction_check",
            "count_axioms": len(axioms),
            "count_suggestions": len(suggestions),
        })

        disclaimer = self._common_disclaimer() + " " + _DISCLAIMER_ICC_AUDITOR

        return {
            "suggestions": suggestions,
            "found_any": found_any,
            "advisory": True,
            "disclaimer": disclaimer,
            "meta": meta,
        }
