# T1 燃烧测试异常分析报告

**日期**: 2026-08-27  
**测试文件**: `tests/_archive/t1_burning_repeat_test.py`（已归档）  
**数据文件**: `t1_repeat_20260827_030249.json`  
**CufBridge 版本**: v0.6.16

---

## 1. 测试设计

对单个 NPC (EID:9001333, HP=100) 连续施加 5 次 `variant='burning'` 状态效果攻击，间隔 12s，随后追踪 90s 观察 HP 变化。

**目的**: 验证状态效果（DOT）攻击机制是否可被 CufBridge 正确施加并被 Python 侧观测到 HP 下降。

---

## 2. 观测现象

### 2.1 攻击阶段（0:00 – 1:18）

| 攻击 | 时间 | accepted | reason | hp_before | hp_after | verified |
|---|---|---|---|---|---|---|
| #1 | 03:00:30 | true | status_effect_applied | 100 | 100 | false |
| #2 | 03:00:42 | true | status_effect_applied | 100 | 100 | false |
| #3 | 03:00:54 | true | status_effect_applied | 100 | 100 | false |
| #4 | 03:01:06 | true | status_effect_applied | 100 | 100 | false |
| #5 | 03:01:18 | true | status_effect_applied | 100 | 100 | false |

**关键观察**:
- 5 次全部返回 `accepted=true, reason=status_effect_applied`
- 每次 `hp_before == hp_after == 100`（Lua 侧即时读取也未变化）
- `verified=false`（CufBridge 未做二次确认）

### 2.2 追踪阶段（1:18 – 2:49）

90s 内每 2s 采样一次 `get_hp()`：

| 时间段 | 结果 |
|---|---|
| 0–88s | HP 始终 = 100，目标 present = true |
| ~90s | 目标 present = false，hp = null |

### 2.3 汇总

```json
{
  "target_id": "EID:9001333",
  "initial_hp": 100,
  "final_hp": null,
  "hp_decreased": false,
  "status_confirmed_any": false,
  "disappeared": true,
  "killed": true,
  "attack_count": 5
}
```

**核心矛盾**: HP 全程 100 未降，但目标在 ~90s 后消失（`disappeared=true, killed=true`）。

---

## 3. 可能解释

### 假设 A: DOT 伤害不反映在 `GetStatPoolValue(Health)` 中

Cyberpunk 2077 的 HP 系统可能有多层：
- `GetStatPoolValue(StatsOID.Health)` 读取的是"基础血条"
- Burning DOT 可能走独立的伤害通道（如 `gameDamage`），直接作用于 NPC 死亡判定
- 类似于某些游戏中"护甲/护盾"与"生命值"分离的机制

**支持证据**: Lua 侧 `hp_before/hp_after` 也读 100，说明 `get_hp()` 函数（底层调用 `GetStatPoolValue`）确实无法捕获 DOT 伤害。

### 假设 B: Burning 视觉存在但 HP 系统不挂钩

- 游戏内 NPC 可能确实被施加了燃烧视觉效果
- 但 DOT 伤害值为 0 或极低（被 NPC 的某种抗性完全抵消）
- NPC 最终消失是因为其他原因（如 despawn timer、AI 行为导致走远）

**支持证据**: `killed=true` 的判定逻辑需要验证——如果仅基于"目标从 scan 中消失"就推断 killed，可能是误判。

### 假设 C: NPC 被游戏系统移除（非 HP 归零）

- NPC 可能因死亡动画完成后 despawn
- 也可能因 AI 行为（如逃跑后消失）
- 也可能因游戏世界的清理机制（如远离玩家后卸载）

**支持证据**: 目标在 ~90s 后消失，这个时间窗口与 Cyberpunk 2077 的 NPC despawn 行为一致。

---

## 4. 对实验系统的影响

### 4.1 当前不影响主线

T1 燃烧测试是**探索性测试**，不在主线步骤（S1-S29）中。主线实验使用的是 `variant='normal'` 物理攻击，HP 变化可被正确观测。

### 4.2 若未来需要 DOT/状态效果攻击

如果 D3（观测词表扩展）或后续实验需要引入状态效果作为动作类型：
1. 需要修改 `get_hp()` 或新增 `get_damage_state()` 接口来捕获 DOT 伤害
2. 或者改用 `combat_attack` 事件回流作为"目标被击败"的判据（而非 HP 轮询）
3. CufBridge 的 `check_status_effect` 延迟确认为 `false`，说明该 API 也可能需要排查

### 4.3 不修改代码

本记录仅为观测记录。不触发工程修改。

---

## 5. 后续建议

1. **暂不深入**: T1 是探索性测试，结论已记录，不需要立即修复
2. **D3 阶段可 revisit**: 如果 D3 观测词表扩展中需要状态效果通道，再回来排查 `get_hp()` 的局限性
3. **CufBridge 侧可补充**: 未来可考虑在 `player_attack` handler 中增加 `GetStatPoolValue` 之外的伤害读取（如 `GetHealthPercentage` 或 damage event 订阅），但优先级低
