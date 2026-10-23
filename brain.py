"""brain.py —— DeepSeek 决策脑（qs-play 阶段 0）

只做一件事：把观测喂给 deepseek-flash，拿回一个动作建议。
动作合法与否不归它管——白名单、位置约束由复用自 cuf-qs-2077 的
ActionPlanner.validate 硬校验，写权限配额由 gate.py 管。

零第三方依赖：urllib + 复用 parse_plan_json。图片通道（视觉）留接口，
阶段 0 不用画面（mock 世界没有画面）。
"""
import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"          # /models 实测只有 deepseek-flash（可看图）与 deepseek-v4-pro
# 元数据（2026-10-23 实读）：flash = DeepSeek-V4.1-Flash，context 1M，max_output 393216，
# input_modalities [text, image]，effort 支持 low/high/max（默认 high）。
# pro 是 input_modalities [text] —— 想看图只能用 flash。
# 玩游戏的回路要的是快和便宜，不是深度推理，所以默认 effort=low。
EFFORT = "low"
TIMEOUT_S = 60
MAX_OUTPUT_TOKENS = 1200

SYSTEM = """你是一个在玩《赛博朋克 2077》的自主智能体。每轮你会收到当前观测，
必须只输出一个 JSON 动作对象，不要输出解释文字。

动作 JSON 形如：
{"action_type": "attack"|"move"|"pickup",
 "target_entity_id": "实体ID 或 null",
 "params": {"dx": 数值, "dz": 数值} 或 {"radius_m": 数值},
 "reason": "一句话说明你为什么这么选"}

硬规则（违反会被系统直接拒绝）：
- attack / pickup 的 target_entity_id 必须是本轮观测列表里真实存在的实体 ID；
- move 用 2D 步进 dx/dz，单步绝对值不超过 {move_max}；
- 不允许攻击 kind=player 的实体；
- 你的动作由游戏侧钩子执行，不产生手柄/键鼠层面的真实操作。

思考要求：先想这一轮要达成什么，再选最小够用的动作。{coach}"""

# 引导句开关：A/B 用。开着＝教它"无变化就换策略"；关掉＝看它自己会不会发现。
COACH_ON = "若观测与上一轮完全相同，说明你的上一个动作没有产生任何可见后果——此时必须改变策略，不要重复同一个动作。"
COACH_OFF = "按你对当前局面的判断自行决定。"

# 视觉通道（阶段 1：真截图驱动）。帧差是系统实测的客观量，写进 prompt 当事实，
# 模型不许自行宣称"已经生效"——它只能填 claim，由 harness 拿实测去对。
VISION_SYSTEM = """你在通过屏幕截图实时操控一个第三人称动作游戏（《剑星》Stellar Blade）。

本轮目标：{goal}

可用的操作（只能从这里选，其它一律无效）：
  hold_key(t)  按住某个键走/闪，t ∈ {keys}，持续 0.6 秒
  tap_key(t)   点一下某个键，t ∈ {keys}
  look(dx,dy)  转视角，dx/dy 为 -400..400
  click()      鼠标左键一次
  wait()       什么都不做，观察

系统实测（这是事实，不是你猜的）：
  本帧与上一帧的粗网格变化占比 cells={cells}（0..1），平均灰度差 mean={mean}，
  上一拍实测 cells={prev_cells}。
  cells=0 表示画面完全没动，也就是你的上一拍**没有产生任何可见后果**。

只输出一个 JSON：
{{"intent": {{"type": "hold_key|tap_key|look|click|wait",
             "arg": 键名或 [dx,dy] 或 null}},
  "claim": "你认为这一拍会造成什么可见变化（一句话）",
  "reason": "你为什么这么选（一句话）",
  "goal_progress": 0.0}}

纪律：不许宣称已经成功；claim 只是你的预期，系统会用帧差核对。
若连续几拍 cells 都接近 0，说明你在原地空转——换一种操作，不要重复同一动作。"""


def build_history_block(history):
    if not history:
        return "这是第一轮，没有历史。请看图给出第一拍。"
    lines = ["最近几拍（模型自报 claim / 系统实测帧差）："]
    for h in history[-4:]:
        lines.append("- r%s %s｜claim=%s｜实测cells=%s mean=%s" % (
            h.get("round_id"), h.get("intent"), h.get("claim"),
            h.get("measured_cells"), h.get("measured_mean")))
    return "\n".join(lines)


def _first_json(text):
    """从模型输出里抠出第一个完整 JSON 对象（视觉通道用，不依赖 planner 的文本解析器）。"""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except (json.JSONDecodeError, ValueError):
                        break
        start = text.find("{", start + 1)
    return None


class Brain:
    def __init__(self, api_key=None, model=MODEL, move_max=20.0, coach=True):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 不在环境里（值不外泄，只查存在性）")
        self.model = model
        self.move_max = move_max
        self.coach = bool(coach)
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "cached_tokens": 0}
        self.last_finish = None
        self.last_reasoning_len = 0

    # ── 观测 → 提示词 ────────────────────────────────────────
    def build_user(self, observation, history, world_changed):
        ents = []
        for e in observation.get("entities", []) or []:
            pos = e.get("position", {}) or {}
            ents.append({
                "entity_id": e.get("entity_id"),
                "kind": e.get("kind"),
                "labels": e.get("labels"),
                "hp": e.get("hp"),
                "pos": [round(float(pos.get("x", 0)), 1),
                        round(float(pos.get("y", 0)), 1),
                        round(float(pos.get("z", 0)), 1)],
            })
        lines = ["# 本轮观测",
                 "player_position: %s" % json.dumps(
                     observation.get("player_position", {}), ensure_ascii=False),
                 "world_changed_vs_last_round: %s" % ("yes" if world_changed else "NO"),
                 "# 实体列表",
                 json.dumps(ents, ensure_ascii=False)]
        if history:
            lines.append("# 你最近的动作与系统裁决（accepted/rejected，及原因）")
            for h in history[-5:]:
                lines.append("- r%s %s target=%s -> %s %s" % (
                    h.get("round_id"), h.get("action_type"),
                    h.get("target_entity_id") or "-",
                    h.get("decision"), h.get("note") or ""))
        lines.append("现在只输出一个 JSON 动作对象。")
        return "\n".join(lines)

    # ── 视觉通道：截图 + 客观帧差 → 一个操作意图 ───────────────
    def ask_image(self, image_data_url, goal, history, frame_stats,
                  allowed_keys):
        """返回 (intent dict, err)。intent 里模型必须自报 claim（它认为这一拍
        会造成什么），harness 用**帧差**去对，不对它的话负责。"""
        sys_txt = VISION_SYSTEM.format(
            goal=goal, keys="/".join(sorted(allowed_keys)),
            cells=frame_stats["cells_changed"],
            mean=frame_stats["mean_delta"],
            prev_cells=frame_stats.get("prev_cells", "None"))
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_txt},
                {"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": image_data_url}},
                    {"type": "text", "text": build_history_block(history)},
                ]},
            ],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "effort": EFFORT,
            "stream": False,
        }
        req = urllib.request.Request(
            API_URL, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return None, "http_%s" % e.code
        except Exception as e:                      # noqa: BLE001
            return None, "%s:%s" % (type(e).__name__, str(e)[:60])

        u = body.get("usage") or {}
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
        self.usage["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
        pd = u.get("prompt_tokens_details") or {}
        self.usage["cached_tokens"] += int(pd.get("cached_tokens", 0) or 0)

        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        self.last_finish = choice.get("finish_reason")
        self.last_reasoning_len = len(msg.get("reasoning_content") or "")
        obj = _first_json(text)
        if obj is None:
            return None, "unparsed[finish=%s]:%s" % (
                self.last_finish, text[:90].replace("\n", " ") or "empty")
        return obj, None


    def ask(self, observation, history, world_changed, parse_plan_json):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM.replace(
                    "{move_max}", str(self.move_max)).replace(
                    "{coach}", COACH_ON if self.coach else COACH_OFF)},
                {"role": "user", "content": self.build_user(
                    observation, history, world_changed)},
            ],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "effort": EFFORT,
            "stream": False,
        }
        req = urllib.request.Request(
            API_URL, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return None, "http_%s" % e.code
        except Exception as e:                      # noqa: BLE001
            return None, "%s:%s" % (type(e).__name__, str(e)[:60])

        u = body.get("usage") or {}
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
        self.usage["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
        pd = u.get("prompt_tokens_details") or {}
        self.usage["cached_tokens"] += int(pd.get("cached_tokens", 0) or 0)

        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        self.last_finish = choice.get("finish_reason")
        self.last_reasoning_len = len(msg.get("reasoning_content") or "")
        # V4.1 会把思考写进 reasoning_content；正文被截断或留空时，
        # 从思考尾部再试一次解析（只当兜底，不作常规路径）。
        if not text.strip():
            rc = msg.get("reasoning_content") or ""
            text = rc[-400:] if rc else ""
        plan = parse_plan_json(text)
        if plan is None:
            return None, "unparsed[finish=%s rc_len=%d]:%s" % (
                self.last_finish, self.last_reasoning_len,
                (text[:80].replace("\n", " ") or "empty"))
        return plan, None
