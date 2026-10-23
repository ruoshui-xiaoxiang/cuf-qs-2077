"""Create pet_personality.json and pet_dialogue.py for the desktop pet system."""
import json
import os

BASE = r"e:\QODERCN\ai_agent_monitor"

# ══════════════════════════════════════════════════════
# 1. config/pet_personality.json
# ══════════════════════════════════════════════════════
personality = {
    "personality": "snarky",
    "language": "zh",
    "dialogue_interval_s": 3.0,
    "max_visible_s": 8.0,
    "events": {
        "trust_drop":       {"weight": 1.0, "cooldown_s": 10},
        "trust_recover":    {"weight": 0.5, "cooldown_s": 15},
        "repetition":       {"weight": 1.2, "cooldown_s": 5},
        "low_diversity":    {"weight": 1.0, "cooldown_s": 8},
        "circuit_break":    {"weight": 2.0, "cooldown_s": 30},
        "rate_limit":       {"weight": 1.5, "cooldown_s": 20},
        "idle":             {"weight": 0.3, "cooldown_s": 60},
        "startup":          {"weight": 1.0, "cooldown_s": 0},
        "healthy_streak":   {"weight": 0.2, "cooldown_s": 30},
    },
}

cfg_path = os.path.join(BASE, "config", "pet_personality.json")
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(personality, f, ensure_ascii=False, indent=2)
print(f"[OK] {cfg_path}")


# ══════════════════════════════════════════════════════
# 2. src/ui/pet_dialogue.py
# ══════════════════════════════════════════════════════
dialogue_code = r'''"""UI: Pet dialogue system — the personality layer of the desktop pet.

Generates context-aware dialogue lines based on monitoring events.
Uses combinatorial templates (subject x action x object x tone)
to produce ~160+ unique lines from ~8 event types.

Zero dependencies — pure stdlib + internal imports.
"""

import json
import logging
import os
import random
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════
#  Dialogue templates by event type
#  Each event has HUMAN and CYBER variants
#  Combinatorial: pick one from each list and combine
# ══════════════════════════════════════════════════

# ── Trust dropped ──
_TRUST_DROP_HUMAN = [
    "{subj}有点不放心那个 AI 了",
    "{subj}觉得那个 AI 刚才说的不太对",
    "{subj}对 AI 的信任降了一点",
    "{subj}开始怀疑 AI 了",
    "{subj}刚才那句话有问题",
    "{subj}不太相信它了",
    "{subj}感觉 AI 在糊弄",
    "{subj}注意到 AI 不太靠谱",
]

_TRUST_DROP_CYBER = [
    "TRUST↓ {subj}检测到可信度衰减",
    "WARNING: {subj}标记异常输出",
    "{subj}::trust_delta(-) // 置信度下降",
    "ALERT: {subj}信号质量降低",
    "{subj} 判定: 输出不可靠",
]

# ── Trust recovered ──
_TRUST_RECOVER_HUMAN = [
    "{subj}觉得 AI 又靠谱了",
    "{subj}对 AI 的信任回来了一点",
    "{subj}松了口气，这次没问题",
    "{subj}觉得 AI 状态恢复了",
]

_TRUST_RECOVER_CYBER = [
    "TRUST↑ {subj}信号质量回升",
    "{subj}::trust_delta(+) // 置信恢复",
    "OK: {subj}重新校准信任",
]

# ── Repetition detected ──
_REPETITION_HUMAN = [
    "{subj}觉得 AI 在说车轱辘话",
    "{subj}发现 AI 翻来覆去就那几个词",
    "{subj}怀疑 AI 卡带了",
    "{subj}：它是不是原地打转了？",
    "{subj}感觉 AI 在复读",
    "{subj}：这话它刚才说过了吧",
    "{subj}发现 AI 开始循环了",
    "{subj}：怎么又是这句",
]

_REPETITION_CYBER = [
    "LOOP_DETECTED: {subj}捕获重复模式",
    "{subj}::repeat_ratio > threshold // 循环退化",
    "DEGEN: {subj}检测到生成退化",
    "{subj} 标记: 重复率异常",
]

# ── Low diversity ──
_LOW_DIV_HUMAN = [
    "{subj}觉得 AI 词汇量好少",
    "{subj}发现 AI 就会这几个词",
    "{subj}：它的表达也太单一了",
    "{subj}感觉 AI 在凑字数",
    "{subj}：翻来覆去就这些",
    "{subj}发现 AI 不会换说法",
]

_LOW_DIV_CYBER = [
    "DIVERSITY↓ {subj}词表利用率过低",
    "{subj}::unique_ratio < threshold // 低多样性",
    "WARN: {subj}检测到表达贫乏",
    "{subj} 标记: 词汇集中度异常",
]

# ── Circuit break ──
_CIRCUIT_HUMAN = [
    "{subj}不想帮它传话了",
    "{subj}今天不伺候了",
    "{subj}决定暂停一下",
    "{subj}：够了，先停停吧",
    "{subj}已经忍不了了",
    "{subj}拉闸了",
]

_CIRCUIT_CYBER = [
    "CIRCUIT_BREAK: {subj}已断开连接",
    "{subj}::trust < threshold // 熔断触发",
    "HALT: {subj}执行保护性中断",
    "{subj} 状态: 已熔断",
]

# ── Rate limit ──
_RATE_LIMIT_HUMAN = [
    "{subj}要慢一点让它冷静冷静",
    "{subj}在控制节奏了",
    "{subj}：别急，慢慢来",
    "{subj}给它降降速",
]

_RATE_LIMIT_CYBER = [
    "RATE_LIMIT: {subj}启用限速",
    "{subj}::throttle(active) // 流量控制",
    "SLOW: {subj}降低请求频率",
]

# ── Idle ──
_IDLE_HUMAN = [
    "{subj}好无聊",
    "{subj}在等你用 AI 呢",
    "{subj}：怎么没人说话了",
    "{subj}打了个哈欠",
    "{subj}在发呆",
    "{subj}：安静得有点过分",
    "{subj}开始数羊了",
    "{subj}：要不要来一轮？",
]

_IDLE_CYBER = [
    "IDLE: {subj}等待信号输入",
    "{subj}::state=idle // 静默监听",
    "STANDBY: {subj}低功耗模式",
    "{subj} 状态: 空闲",
]

# ── Startup ──
_STARTUP_HUMAN = [
    "{subj}上线了",
    "{subj}来了，准备好看着你的 AI",
    "{subj}：今天也要盯着你哦",
    "{subj}启动完成，开始监控",
    "{subj}：让我看看你的 AI 靠不靠谱",
]

_STARTUP_CYBER = [
    "BOOT: {subj}系统初始化完成",
    "{subj}::online // 监控就绪",
    "INIT: {subj}全链路激活",
    "{subj} 状态: 运行中",
]

# ── Healthy streak ──
_HEALTHY_HUMAN = [
    "{subj}觉得 AI 今天表现不错",
    "{subj}：连续好几次都没问题",
    "{subj}对 AI 有点改观了",
    "{subj}：它今天挺靠谱的",
]

_HEALTHY_CYBER = [
    "STABLE: {subj}连续健康输出",
    "{subj}::streak > threshold // 稳定运行",
    "NOMINAL: {subj}全指标正常",
]

# ── Time-of-day modifiers (human mode) ──
_TIME_MODIFIERS = {
    "late_night": ["都这么晚了", "凌晨了还在", "夜深了"],
    "night": ["晚上好", "夜间模式"],
    "morning": ["早上好", "新的一天"],
    "afternoon": ["下午了"],
}

# ── Subject pool ──
_SUBJECTS = ["我", "本宠", "系统"]

# ── Event → template mapping ──
_TEMPLATE_MAP = {
    "trust_drop":     (_TRUST_DROP_HUMAN, _TRUST_DROP_CYBER),
    "trust_recover":  (_TRUST_RECOVER_HUMAN, _TRUST_RECOVER_CYBER),
    "repetition":     (_REPETITION_HUMAN, _REPETITION_CYBER),
    "low_diversity":  (_LOW_DIV_HUMAN, _LOW_DIV_CYBER),
    "circuit_break":  (_CIRCUIT_HUMAN, _CIRCUIT_CYBER),
    "rate_limit":     (_RATE_LIMIT_HUMAN, _RATE_LIMIT_CYBER),
    "idle":           (_IDLE_HUMAN, _IDLE_CYBER),
    "startup":        (_STARTUP_HUMAN, _STARTUP_CYBER),
    "healthy_streak": (_HEALTHY_HUMAN, _HEALTHY_CYBER),
}


class PetDialogue:
    """Generates context-aware pet dialogue from monitoring events.

    Features:
    - Combinatorial templates: subject x action x object x tone
    - Context awareness: trust level, time of day, repeat count
    - Deduplication: same line won't repeat within N calls
    - Dual mode: human (plain Chinese) and cyber (technical flair)
    - Cooldown: each event type has configurable cooldown
    """

    def __init__(self, config_path: Optional[str] = None):
        self._config = self._load_config(config_path)
        self._personality = self._config.get("personality", "snarky")
        self._history: deque = deque(maxlen=50)
        self._cooldowns: Dict[str, float] = {}
        self._event_counts: Dict[str, int] = {}
        self._total_events = 0
        self._start_time = time.time()

    def _load_config(self, path: Optional[str]) -> dict:
        if path is None:
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))),
                "config", "pet_personality.json"
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def on_event(self, event_type: str, mode: str = "human",
                 trust: float = 0.8,
                 extra: Optional[Dict] = None) -> Optional[str]:
        """Generate a dialogue line for a monitoring event.

        Args:
            event_type: One of the supported event types.
            mode: "human" or "cyber" — determines dialogue style.
            trust: Current trust score (0.0-1.0).
            extra: Additional context (repeat_ratio, etc.).

        Returns:
            A dialogue string, or None if on cooldown.
        """
        if event_type not in _TEMPLATE_MAP:
            log.warning("Unknown pet event type: %s", event_type)
            return None

        # Check cooldown
        ev_cfg = self._config.get("events", {}).get(event_type, {})
        cooldown = ev_cfg.get("cooldown_s", 5)
        now = time.time()
        last = self._cooldowns.get(event_type, 0)
        if now - last < cooldown:
            return None
        self._cooldowns[event_type] = now

        # Track counts
        self._event_counts[event_type] = \
            self._event_counts.get(event_type, 0) + 1
        self._total_events += 1

        # Select template set based on mode
        human_tpl, cyber_tpl = _TEMPLATE_MAP[event_type]
        templates = cyber_tpl if mode == "cyber" else human_tpl

        # Pick random template
        template = random.choice(templates)

        # Select subject
        subj = self._pick_subject(trust, mode)

        # Format
        line = template.format(subj=subj)

        # Add time modifier occasionally (human mode only)
        if mode == "human" and random.random() < 0.2:
            time_mod = self._get_time_modifier()
            if time_mod:
                line = f"{time_mod}，{line}"

        # Add escalation if repeated event
        count = self._event_counts.get(event_type, 0)
        if count >= 5 and mode == "human":
            line = f"（第{count}次了）{line}"

        # Dedup check
        if line in self._history:
            # Try again with different template
            template = random.choice(templates)
            subj = self._pick_subject(trust, mode)
            line = template.format(subj=subj)
            if line in self._history:
                return None  # Give up if still duplicate

        self._history.append(line)
        return line

    def _pick_subject(self, trust: float, mode: str) -> str:
        """Pick a subject based on trust level and personality."""
        if trust < 0.3:
            return "我"  # More personal when angry
        if trust > 0.9 and mode == "human":
            return random.choice(["我", "本宠"])
        return random.choice(_SUBJECTS)

    def _get_time_modifier(self) -> Optional[str]:
        """Get a time-of-day modifier string."""
        hour = time.localtime().tm_hour
        if hour >= 0 and hour < 5:
            pool = _TIME_MODIFIERS.get("late_night", [])
        elif hour >= 5 and hour < 12:
            pool = _TIME_MODIFIERS.get("morning", [])
        elif hour >= 12 and hour < 18:
            pool = _TIME_MODIFIERS.get("afternoon", [])
        else:
            pool = _TIME_MODIFIERS.get("night", [])
        return random.choice(pool) if pool else None

    @property
    def stats(self) -> Dict:
        """Return dialogue system statistics."""
        return {
            "total_events": self._total_events,
            "event_counts": dict(self._event_counts),
            "unique_lines": len(set(self._history)),
            "uptime_s": round(time.time() - self._start_time, 1),
        }
'''

dlg_path = os.path.join(BASE, "src", "ui", "pet_dialogue.py")
with open(dlg_path, "w", encoding="utf-8") as f:
    f.write(dialogue_code)
print(f"[OK] {dlg_path}")
print(f"  Lines: {len(dialogue_code.splitlines())}")
print("Done.")
