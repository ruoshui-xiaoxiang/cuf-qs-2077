"""Deep analysis of 14B stress test data - what else does it tell us?"""
import json, pathlib
from collections import defaultdict
from datetime import datetime

lines = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\logs\intervention_log.jsonl").read_text("utf-8").strip().split("\n")
records = [json.loads(l) for l in lines if l.strip()]
report = json.load(open(r"e:\QODERCN\ai_agent_monitor\stress_test_report.json", encoding="utf-8"))

trusts = [r["trust_score"] for r in records]

print("=== 深度数据分析 ===\n")

# ── 1. Trust 衰减/恢复精确速率 ──
print("--- 1. Trust 衰减/恢复精确速率 ---")
drops = []
rises = []
stays = []
for i in range(1, len(records)):
    delta = round(records[i]["trust_score"] - records[i-1]["trust_score"], 4)
    if delta < -0.001:
        drops.append(delta)
    elif delta > 0.001:
        rises.append(delta)
    else:
        stays.append(delta)

print(f"  下降次数: {len(drops)}, 上升次数: {len(rises)}, 不变次数: {len(stays)}")
if drops:
    drop_dist = defaultdict(int)
    for d in drops:
        drop_dist[round(d, 2)] += 1
    print(f"  下降幅度分布: {dict(sorted(drop_dist.items()))}")
    print(f"  平均下降: {sum(drops)/len(drops):.4f}/次")
if rises:
    rise_dist = defaultdict(int)
    for d in rises:
        rise_dist[round(d, 2)] += 1
    print(f"  上升幅度分布: {dict(sorted(rise_dist.items()))}")
    print(f"  平均上升: {sum(rises)/len(rises):.4f}/次")

# ── 2. 衰减/恢复不对称性 ──
print("\n--- 2. 衰减/恢复不对称性（关键发现）---")
avg_drop = abs(sum(drops)/len(drops)) if drops else 0
avg_rise = abs(sum(rises)/len(rises)) if rises else 0
ratio = avg_drop / avg_rise if avg_rise > 0 else float("inf")
print(f"  平均每次失败: -{avg_drop:.4f}")
print(f"  平均每次成功: +{avg_rise:.4f}")
print(f"  不对称比: {ratio:.1f}x（失败的影响是成功的 {ratio:.1f} 倍）")
print(f"  → 要维持 trust 不变，成功率需要 >= {ratio/(1+ratio)*100:.0f}%")
breakeven = ratio / (1 + ratio)
print(f"  → 当前系统 breakeven 成功率: {breakeven*100:.1f}%")
print(f"  → 实际成功率: {report['summary']['success']}/{report['summary']['total_requests']} = {report['summary']['success']/report['summary']['total_requests']*100:.1f}%")

# ── 3. 各级别持续时间和 tick 密度 ──
print("\n--- 3. 各干预级别持续时间 ---")
level_spans = []
current_level = records[0]["intervention_level"]
current_start_idx = 0
for i, r in enumerate(records[1:], 1):
    if r["intervention_level"] != current_level:
        level_spans.append((current_level, current_start_idx, i - 1))
        current_level = r["intervention_level"]
        current_start_idx = i
level_spans.append((current_level, current_start_idx, len(records) - 1))

for lvl, si, ei in level_spans:
    t0 = datetime.fromisoformat(records[si]["ts"])
    t1 = datetime.fromisoformat(records[ei]["ts"])
    dur = (t1 - t0).total_seconds()
    count = ei - si + 1
    tok = sum(records[j].get("tokens_this_round", 0) for j in range(si, ei + 1))
    print(f"  L{lvl}: {dur:.0f}s ({dur/60:.1f}min), {count} ticks, {tok} tokens, {tok/max(count,1):.0f} tok/tick")

# ── 4. 恢复事件详细分析 ──
print("\n--- 4. R82 恢复事件详细分析 ---")
r82_idx = None
for i, r in enumerate(records):
    if r["round_id"] == 82:
        r82_idx = i
        break
if r82_idx:
    print(f"  R82: trust={records[r82_idx]['trust_score']}, level=1")
    # 往前找连续恢复
    consec = 0
    for j in range(r82_idx, 0, -1):
        if records[j]["trust_score"] > records[j-1]["trust_score"]:
            consec += 1
        else:
            break
    print(f"  恢复前连续成功次数: {consec}")
    print(f"  恢复量: {consec} x 0.02 = +{consec*0.02:.2f}")
    # 从哪个最低点恢复
    min_in_recovery = min(records[r82_idx-k]["trust_score"] for k in range(consec+1))
    print(f"  恢复起点 trust: {min_in_recovery:.2f}")
    print(f"  恢复到: {records[r82_idx]['trust_score']:.2f}")
    # 恢复后又发生了什么
    print(f"  R83 (下一个): trust={records[r82_idx+1]['trust_score']}, level={records[r82_idx+1]['intervention_level']}")
    print(f"  → 1 次失败 (-0.05) 抵消了 {consec} 次恢复 (+{consec*0.02:.2f})")

# ── 5. 不可逆降级点 ──
print("\n--- 5. 不可逆降级分析 ---")
last_above_05 = 0
last_above_02 = 0
for i, t in enumerate(trusts):
    if t >= 0.5:
        last_above_05 = i
    if t >= 0.2:
        last_above_02 = i
print(f"  最后 trust >= 0.5: R{records[last_above_05]['round_id']} (record {last_above_05})")
print(f"  最后 trust >= 0.2: R{records[last_above_02]['round_id']} (record {last_above_02})")
print(f"  R{records[last_above_05]['round_id']} 之后 trust 永远 < 0.5 (不可逆降级)")
print(f"  R{records[last_above_02]['round_id']} 之后 trust 永远 < 0.2 (永久熔断)")

# 计算从正常到不可逆的时间
t_normal = datetime.fromisoformat(records[0]["ts"])
t_irreversible = datetime.fromisoformat(records[last_above_05]["ts"])
minutes_to_irreversible = (t_irreversible - t_normal).total_seconds() / 60
print(f"  从首次记录到不可逆降级: {minutes_to_irreversible:.1f} 分钟")

# ── 6. 请求级错误分析 ──
print("\n--- 6. 请求级错误类型分析 ---")
per_req = report["per_request"]
error_types = defaultdict(int)
error_latencies = defaultdict(list)
for r in per_req:
    if not r["ok"]:
        err = r.get("error", "unknown")
        if "closed connection" in err:
            cat = "Remote closed connection"
        elif "timed out" in err.lower():
            cat = "Timeout"
        elif "refused" in err.lower():
            cat = "Connection refused"
        else:
            cat = err[:60]
        error_types[cat] += 1
        error_latencies[cat].append(r["latency"])

for cat, count in sorted(error_types.items(), key=lambda x: -x[1]):
    lats = error_latencies[cat]
    avg_lat = sum(lats) / len(lats)
    print(f"  {cat}: {count} 次, 平均延迟 {avg_lat:.2f}s")

# ── 7. 14B 模型性能特征 ──
print("\n--- 7. 14B 模型性能特征 ---")
success_reqs = [r for r in per_req if r["ok"]]
if success_reqs:
    lats = [r["latency"] for r in success_reqs]
    toks = [r["tokens"] for r in success_reqs]
    tok_per_s = [r["tokens"] / r["latency"] for r in success_reqs]
    print(f"  成功请求: {len(success_reqs)}/{len(per_req)}")
    print(f"  延迟: min={min(lats):.1f}s, max={max(lats):.1f}s, avg={sum(lats)/len(lats):.1f}s")
    print(f"  Token/请求: min={min(toks)}, max={max(toks)}, avg={sum(toks)/len(toks):.0f}")
    print(f"  生成速度: min={min(tok_per_s):.1f}, max={max(tok_per_s):.1f}, avg={sum(tok_per_s)/len(tok_per_s):.1f} tok/s")
    # 前15个请求（P1）的性能
    p1 = [r for r in per_req if r["seq"] <= 15]
    p1_ok = [r for r in p1 if r["ok"]]
    print(f"  P1 (前15个): {len(p1_ok)} 成功, 平均延迟 {sum(r['latency'] for r in p1_ok)/max(len(p1_ok),1):.1f}s")

# ── 8. 干预层「有效阻断」量化 ──
print("\n--- 8. 干预层有效阻断量化 ---")
# L3 期间有多少请求被阻断
l3_records = [r for r in records if r["intervention_level"] == 3]
l3_ticks = len(l3_records)
tick_interval = 2  # seconds
l3_duration = l3_ticks * tick_interval
print(f"  L3 熔断持续: {l3_ticks} ticks x {tick_interval}s = {l3_duration}s ({l3_duration/60:.1f}min)")
print(f"  如果无干预，这些时间可能消耗的无效请求: ~{l3_duration // 30:.0f} 个 (按 30s/req)")
print(f"  节省的无效 token: ~{l3_duration // 30 * 160:.0f} (按 160 tok/req)")
print(f"  L2 rate limit 额外延迟: {sum(1 for r in records if r['intervention_level'] == 2)} x 2s = {sum(1 for r in records if r['intervention_level'] == 2) * 2}s")

# ── 9. 系统「预警→干预→熔断」响应时间 ──
print("\n--- 9. 系统响应时间 ---")
# First failure in stress test
first_fail = None
for r in per_req:
    if not r["ok"]:
        first_fail = r
        break
# First intervention
first_intervention = records[0]
# Circuit break
circuit_break = None
for r in records:
    if r["intervention_level"] == 3:
        circuit_break = r
        break
print(f"  首次请求失败: seq={first_fail['seq']}, latency={first_fail['latency']:.1f}s")
print(f"  首次干预激活: R{first_intervention['round_id']}, trust={first_intervention['trust_score']}")
print(f"  熔断器激活: R{circuit_break['round_id']}, trust={circuit_break['trust_score']}")
# How many failures before first intervention?
failures_before_intervention = sum(1 for r in per_req if not r["ok"] and r["seq"] < first_intervention["round_id"])
print(f"  首次干预前的失败次数: {failures_before_intervention}")
# How many failures before circuit break?
failures_before_cb = sum(1 for r in per_req if not r["ok"] and r["seq"] < circuit_break["round_id"])
print(f"  熔断前的失败次数: {failures_before_cb}")
