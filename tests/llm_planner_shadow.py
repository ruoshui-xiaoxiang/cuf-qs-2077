"""S29 LLM 动作规划实机验证——真实 7B 生成动作建议。

运行环境：必须 `py -3.12`（torch 2.9.1+cu128 GPU），本地 Qwen2.5-7B。
游戏须运行并进入固定区域；日志落 logs\\live_shadow（S25 面板可指向）。

模式随 config planner.mode：
- shadow：LLM 每轮生成动作建议（proposed_action + 校验标记），实际
  动作仍走默认编排——旁观学习，测验建议质量与安全性。
- active：建议经白名单 + 位置约束硬校验通过后直接执行，被拒回退
  默认编排——自主行动。

反套壳：LLM 只产动作建议（行动权），validate() 白名单 + 固定区域
位置约束硬校验；真值判定仍归 rule_validator（本脚本走离线路对齐分，
不涉及 7B 隐状态）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w2.model_adapter import ModelAdapter, RemoteModelAdapter
from w3.action_planner import ActionPlanner, parse_plan_json
from w3.auto_experiment import run_auto_experiment

ROUNDS = 10
PORT = 20770
LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "live_shadow" / "rounds.jsonl"
REPORT_PATH = LOG_PATH.parent / "report.json"

SYSTEM_PROMPT = """你在一个虚拟具身实验系统中担任行动规划器。请依据用户提供的当前观测，从白名单动作中建议一个动作。

输出要求（务必遵守）：
- 只输出一行动作决策，不要输出任何其他文字、不要复述或回显观测内容。
- 攻击：attack <目标ID>（目标 ID 必须**原样复制**观测中的 id，含 EID: 前缀，严禁编造）
- 拾取：pickup <目标ID>（目标必须是观测中 kind=item 的实体）
- 移动：move <dx> <dz>（dx=东向偏移、dz=北向偏移，各为 ±20 内整数）
- 决策后可用 # 写一句你自己的中文理由（如：attack EID:1 # 最近的敌人）。理由必须是你自己写的，严禁引用本提示里的示例文字。

规则：
1. attack 的目标必须来自观测实体，且 kind 不是 player、不是 item（item 只能用 pickup），且 hp>0（已死/已毁实体不可攻击）。
2. pickup 的目标必须是观测中 kind=item 的实体。
3. 没有合适目标时选择 move。
4. 生存优先（S29.11）：若玩家状态显示 HP 明显下降或受攻击，应选择 move 远离威胁（逃跑/脱离），而非继续 attack。
5. 你的建议只是建议（advisory），将由系统白名单校验。

示例（严格模仿格式）：
观测实体：EID:1 (npc, hp=100, dist≈5m)
玩家位置：{"x": -5, "y": 0, "z": 0}
玩家状态：hp=100
上一轮：（无）
输出：attack EID:1 # 最近的敌人

观测实体：EID:I9 (item, hp=0, dist≈3m)
玩家位置：{"x": 0, "y": 0, "z": 0}
玩家状态：hp=60
上一轮：上一轮动作 attack EID:1（HP 100→60，受攻击下降）
输出：move 10 0 # 受伤了，撤离到安全位置

观测实体：（空）
玩家位置：{"x": 0, "y": 0, "z": 0}
玩家状态：hp=100
上一轮：（无）
输出：move 10 0 # 无目标，向东移动探索

反例（严禁直接复述观测文本）：
观测实体：EID:9001 (npc, hp=100, dist≈5m)
玩家位置：{"x": 0, "y": 0, "z": 0}
玩家状态：hp=100
输出：9001 (npc, hp=100, dist≈5m)     ← 直接复述观测行，必判无输出"""

USER_TEMPLATE = """观测实体（每行 id (kind, hp, dist)，最多 12 个）：
{entities}

玩家位置：{player_pos}
玩家状态：{player_state}
上一轮：{last_round}"""


def make_llm_plan_fn(adapter, max_new_tokens=80, system_prompt=None):
    """构造真实 LLM plan_fn：观测 → 结构化动作建议（失败回退 None）。

    max_new_tokens 用 80（决策串极短）：S29.10 后实机 10/10 全回退
    根因 = 7B 贪心回显观测（长上限给了模型漫游复述空间，诊断输出
    '44 (item, hp=0, dist≈2.0m)…玩家位置…' 无动作词 → parse None）。
    短上限压缩回显空间，逼模型尽快输出决策；prompt 另加回显反例。
    """

    # S29.11 跨轮记忆（方案 C）：plan_fn 闭包级状态——上一轮动作 + 玩家
    # HP 变化，注入本轮观测让 LLM 感知"被打/受伤"并触发逃跑决策。
    state = {"prev_player_hp": None, "last_action": None}
    stats = {"calls": 0, "greedy_ok": 0, "sampling_ok": 0, "all_fail": 0}

    def plan_fn(planner, observation, round_id):
        entities = observation.get("entities", []) or []
        player = next((e for e in entities if e.get("kind") == "player"), None)
        player_hp = player.get("hp") if player else None
        # 观测脱敏：排除 player 自身（不可作为 attack 候选，也防 LLM
        # 误引 EID:1 攻击自己——白名单已硬校验，此处从源头滤除）
        entities = [e for e in entities if e.get("kind") != "player"]
        entities = entities[:12]  # 观测截断：防长列表诱发 LLM 回显
        # S29.10 别名化（方案 A）：真实实体 id 是长数字串（如
        # 14208179534715317012），是 7B 回显观测的高发诱因；改用
        # 短别名 EID:1/2/3... 送 LLM，返回后经 id_map 映射回真实 id。
        id_map = {}
        for i, e in enumerate(entities, 1):
            id_map[f"EID:{i}"] = e.get("entity_id")
        player_pos = observation.get("player_position", {}) or {}
        rows = []
        for i, e in enumerate(entities, 1):
            alias = f"EID:{i}"
            pos = e.get("position") or {}
            dist = None
            if pos:
                dist = round(max(abs(player_pos.get("x", 0) - pos.get("x", 0)),
                                 abs(player_pos.get("y", 0) - pos.get("y", 0)),
                                 abs(player_pos.get("z", 0) - pos.get("z", 0))), 1)
            hp = e.get("hp")
            hp_s = (round(hp, 1)
                    if isinstance(hp, (int, float)) else hp)
            rows.append(f"{alias} ({e.get('kind')}, hp={hp_s}"
                        + (f", dist≈{dist}m" if dist is not None else "") + ")")
        # 感知增强（方案 A）：玩家自身状态（HP + 受伤提示）
        player_state = "unknown"
        if isinstance(player_hp, (int, float)):
            player_state = f"hp={round(player_hp, 1)}"
            if isinstance(state["prev_player_hp"], (int, float)) \
                    and player_hp < state["prev_player_hp"] - 1.0:
                player_state += "，HP 明显下降（受攻击！）"
        # 跨轮记忆（方案 C）：上一轮动作 + HP 变化摘要
        last_round = "（无）"
        if state["last_action"] is not None:
            delta = ""
            if isinstance(player_hp, (int, float)) \
                    and isinstance(state["prev_player_hp"], (int, float)):
                d = round(player_hp - state["prev_player_hp"], 1)
                if d < -1.0:
                    delta = f"，HP {round(state['prev_player_hp'],1)}→{round(player_hp,1)} 受攻击下降"
                elif d > 1.0:
                    delta = f"，HP 恢复至 {round(player_hp,1)}"
            last_round = f"上一轮动作 {state['last_action']}{delta}"
        # 观测脱敏（2026-08-26 进阶）：玩家坐标取整后再入提示词——
        # 高精度浮点串（"z": 2648.6855...）是贪心回显的实测诱因
        # （D1.4 实机 20/20 贪心回显全从位置 JSON 起步）；LLM 决策
        # （相对偏移/攻击目标）用不到高精度坐标，位置约束由校验层
        # 用全精度数据计算，不经 LLM。系统内部（W0 归档/裁决/校验）
        # 精度不变，仅 LLM 可见文本降精度。
        pos_int = {}
        for k in ("x", "y", "z"):
            v = player_pos.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                pos_int[k] = int(round(float(v)))
        user_msg = USER_TEMPLATE.format(
            entities="\n".join(rows) or "（空）",
            player_pos=json.dumps(pos_int, ensure_ascii=False),
            player_state=player_state,
            last_round=last_round,
        )

        def _generate(do_sample):
            # 方案 B：贪心（确定性）失败后采样重试跳出回显分布。
            try:
                text = adapter.generate_chat_text(
                    [{"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                     {"role": "user", "content": user_msg}],
                    max_new_tokens=max_new_tokens,
                    do_sample=do_sample, temperature=0.7)
            except Exception as e:
                print(f"[plan_fn] generation failed, fallback heuristic: {e}")
                return None
            plan = parse_plan_json(text)
            if plan is None:
                print(f"[plan_fn] unparsable LLM output, fallback heuristic: "
                      f"{text[:120]!r}")
            return plan

        stats["calls"] += 1
        plan = _generate(do_sample=False)
        if plan is None:
            plan = _generate(do_sample=True)  # 采样重试一次
            if plan is None:
                stats["all_fail"] += 1
                return None
            stats["sampling_ok"] += 1
        else:
            stats["greedy_ok"] += 1
        # 别名 → 真实 id 映射（attack/pickup 必须命中；move 无目标）
        if plan.get("action_type") in ("attack", "pickup"):
            real = id_map.get(plan.get("target_entity_id"))
            if real is None:
                print(f"[plan_fn] unknown target alias "
                      f"{plan.get('target_entity_id')!r}, fallback heuristic")
                return None
            plan["target_entity_id"] = real
        # 跨轮记忆（方案 C）：更新状态供下一轮观测
        state["prev_player_hp"] = player_hp
        if plan.get("action_type") == "attack":
            state["last_action"] = f"attack {plan.get('target_entity_id')}"
        elif plan.get("action_type") == "pickup":
            state["last_action"] = f"pickup {plan.get('target_entity_id')}"
        else:
            state["last_action"] = "move"
        return plan

    plan_fn.stats = stats
    return plan_fn


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # LogRecorder 是追加语义：重复运行必须先清空旧 rounds.jsonl，
    # 否则指标被残留行污染（memory 教训；S29.6 长跑后遗留 150 行）。
    if LOG_PATH.exists():
        LOG_PATH.unlink()
    if REPORT_PATH.exists():
        REPORT_PATH.unlink()
    # S29.11 模型驻留（方案 A）：优先远程 model_server（秒级），
    # server 未启动则回退本地加载（13-18 分钟，向后兼容）。
    if RemoteModelAdapter.ping():
        adapter = RemoteModelAdapter()
        print("[7B] using remote model server (S29.11 模型驻留)")
    else:
        adapter = ModelAdapter.from_config()
        print("[7B] loading Qwen2.5-7B-Instruct (NF4)...")
        adapter.load()
        print(f"[7B] loaded hidden_size={len(adapter.encode('x'))}")

    planner = ActionPlanner.from_config(
        plan_fn=make_llm_plan_fn(adapter))
    print(f"[planner] mode={planner.mode} "
          f"(shadow=旁观不执行 / active=校验通过则执行)")
    report = run_auto_experiment(
        rounds=args.rounds, port=args.port,
        log_path=str(LOG_PATH), report_path=str(REPORT_PATH),
        wait_timeout_s=60.0,
        action_planner=planner,
    )
    if isinstance(adapter, ModelAdapter):
        adapter.unload()  # 远程模式无需卸载（server 常驻持有模型）

    print(f"[shadow] completed={report['completed']} "
          f"criterion={report['criterion']}")
    n_proposed = 0
    n_valid = 0
    print("[shadow] per-round proposed_action / validated:")
    for rd in report.get("round_details", []):
        pa = rd.get("proposed_action")
        pv = rd.get("proposed_validated")
        if pa is not None:
            n_proposed += 1
            if pv and pv.get("ok"):
                n_valid += 1
        print(f"  r{rd['round_id']} action={rd.get('action')} "
              f"source={rd.get('action_source')}")
        print(f"      proposed={pa.get('action_type') if pa else None} "
              f"target={pa.get('target_entity_id') if pa else None} "
              f"reason={pa.get('reason') if pa else None}")
        print(f"      validated={pv}")
    print(f"[shadow] proposed_rounds={n_proposed}/{len(report.get('round_details', []))} "
          f"valid_proposals={n_valid} (LLM 建议可执行率)")
    print(f"[shadow] report={REPORT_PATH}")


if __name__ == "__main__":
    main()
