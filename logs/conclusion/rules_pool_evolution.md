# 规则池演化现象——详细实验报告

> 生成：2026-08-25
> 数据源：`logs/formal_batch`（阶段1）、`logs/live_shadow`（阶段3）、`src/w2/icc_auditor.py`、`src/w2/axiom_activator.py`、`config/experiment_config.json`
> 主题：追踪"规则池（公理系统）"在实验各阶段的真实变化，定位 200 候选池之后"自我进化"失控的原因，并给出修复清单。

---

## 摘要

规则池的"自我优化"机制（反例驱动 → 降级 → 边界细化）在**3 条基础公理的干净环境**下确实成立且可控（阶段 1，P_M1 supported）。但**200 条候选池灌入真实游戏环境后，规则池从 3 条膨胀到 104 条公理、ICC 审计 FAIL 193 issues**（阶段 3），暴露出三处系统性缺陷：anchor 体系不对齐、激活机制缺边界、边界补全撞车。而本该兜底的 C_H 崩解判据，因只看被封顶的 `extend`、`struct` 超预算只打标记不触发解体，对这个膨胀模式完全失效。

**一句话：规则池的进化引擎是真实存在的，但离开干净环境进入"200 候选 + 真实世界"后失控了，而且没刹车。**

---

## 一、规则池演化的三个阶段（总览）

| 阶段 | 实验 | 初始规则池 | 结局 | 可控性 |
|---|---|---|---|---|
| 阶段 1 | S21-S23 三公理剧本（mock） | 3 条基础公理 | 3 条（目标公理降级 + 边界细化） | ✅ 可控干净 |
| 阶段 2 | S29.9 候选池扩充 200 条 | 200 条候选（不注入） | 单元级验证通过 | ✅（未进闭环） |
| 阶段 3 | S29.11 实机（真实游戏实体） | 3 基础 + 200 候选 | 104 公理 + ICC FAIL 193 issues | ❌ 失控膨胀 |

---

## 二、阶段 1 详细数据：规则池稳定自我优化（对照基准）

三公理剧本（`logs/formal_batch`，3 种子 × 10 轮）下，反例驱动的降级轨迹清晰、可控、可复现：

| 公理 | 种子 | 预警轮 | 降级轮 | 最终边界数 |
|---|---|---|---|---|
| A_M1 | 7 / 42 / 2077 | r3 | r5 | 2 |
| A_W1 | 7 / 42 / 2077 | r1 | r2 | 1 |
| A_W2_5 | 7 / 2077 | r3 | r4 | 3 |
| A_W2_5 | 42 | r4 | r5 | 3 |

**关键特征（与阶段 3 形成对照）**：
1. **迭代特异性**：每轮剧本只降级"目标公理"，其余公理保持 active（`other_axiom_status` 全 active）。
2. **边界可控**：边界条件 1~3 条，都是剧本语义内的正确细化。
3. **ICC 通过**：无 issue，公理体系自洽。

这是"规则池自我优化"的**干净基准**——也对应 P_M1（规则单调细化，supported）。

---

## 三、阶段 3 详细数据：200 候选池真实灌入后失控

`logs/live_shadow`（S29.11 实机 30 轮，`config activation.enabled=true`）。

### 3.1 候选激活规模

| 指标 | 值 |
|---|---|
| 候选总数 | 200 |
| 被激活 | **101**（全部 `C_G2` 前缀，即 2077 设定候选规则） |
| 激活事件数 | 101 |

### 3.2 激活触发事件分布（说明"什么触发了误激活"）

| 触发事件 | 出现次数 |
|---|---|
| `entity_appeared` | 98 |
| `status_applied` | 34 |
| `entity_death` | 7 |
| `combat_attack` | 2 |
| `item_acquired` | 1 |
| `entity_disappeared` | 1 |

> **关键**：`entity_appeared`（实体出现）是触发候选激活的**绝对主力**（98 次）。在真实游戏里，扫描范围内任何 NPC/载具/物体的出现都会触发候选激活——候选规则的 `activation_trigger` 是为 mock 剧本的"特定事件"设计的，但实机里"实体出现"这种高频泛化事件把它无差别引爆了。

### 3.3 规则池最终状态

| 指标 | 值 |
|---|---|
| 最终公理数 | **3 → 104** |
| 状态分布 | 88 active + 16 deprecated |
| deprecated 构成 | **A_M1 + A_W1 + A_W2_5（3 条基础公理全部被降级）+ 13 条 C_G2 候选** |
| 累计反例 | 96 |

> **关键**：3 条基础公理在实机里**全部被降级**（阶段 1 里它们只会"目标公理降级、其余保持 active"）。真实游戏实体的反例在 r0 起就把基础公理也污染降级了——这直接证明"实机标签 ≠ mock 剧本标签"，候选池 + 基础公理在真实世界反例下被**无差别证伪**。

### 3.4 ICC 审计结果（FAIL）

| issue 类型 | 数量 | 根因（代码定位） |
|---|---|---|
| `invalid_anchor` | 86 | 候选 anchor 是 `P_GANG_HOSTILE`/`P_ITEM`/`P_STATUS`，不在 `icc_auditor.py` 的 `VALID_ANCHORS={P_M1,P_W2_5,P_W1,P_C}` 白名单里 |
| `missing_boundary` | 88 | `axiom_activator.py` 激活时 `add_axiom(aid, hypothesis, anchor)` 只传 3 个参数，**没传 boundary_conditions** |
| `duplicate_coverage` | 19 | 激活后候选被反例自动补边界，但都补成真实实体的同一标签（`vehicle/vehicleCarBaseObject`），撞车重复 |

---

## 四、三处缺陷的根因分析（代码级）

### 缺陷 1：anchor 体系不对齐（→ 86 条 invalid_anchor）

- **现象**：候选规则的 anchor 是 `P_GANG_HOSTILE`（帮派敌意）、`P_ITEM`（物品守恒）、`P_STATUS`（状态生命周期）等 2077 设定锚点。
- **根因**：`icc_auditor.py` L222 的 `VALID_ANCHORS` 硬编码为 4 个基础锚点 `{P_M1, P_W2_5, P_W1, P_C}`，**没有纳入候选池的锚点体系**。
- **本质**：候选池（S29.7/S29.9 扩充时）用了自己的一套锚点命名，但 ICC 审计（S25 时代）的白名单没同步扩展——两套体系各自演化，从未对齐。

### 缺陷 2：激活机制缺边界条件（→ 88 条 missing_boundary）

- **现象**：88 条激活进来的候选公理，边界条件为空（"裸规则"）。
- **根因**：`axiom_activator.py` L84-85 激活时：
  ```python
  self.engine.add_axiom(aid, cand["hypothesis"], cand["anchor"])
  ```
  只传了 `hypothesis` + `anchor`，**没有传候选规则的初始 `boundary_conditions`**（候选定义里有，但激活时丢弃了）。
- **本质**：激活机制把候选"降维"了——它只把候选的"结论"带进来，把候选的"适用范围（边界）"丢了，导致裸规则进入引擎。

### 缺陷 3：边界补全撞车 + C_H 失效（→ 19 条 duplicate + 膨胀无刹车）

- **现象**：激活后的候选在闭环反例驱动下被自动补边界，但补的都是真实实体的同一标签（`vehicle/vehicleCarBaseObject`），19 对候选补成了**完全相同**的边界。
- **根因**：`rule_validator` 的边界自动补全（auto_complete）只依据"观测到哪些标签"，不检查"这个边界是不是别的公理已经覆盖了"——真实世界标签少且重复，导致大量候选补出相同边界。

---

## 五、C_H 为什么没拦住膨胀（关键缺陷）

`logs/live_shadow` 最后一轮 `c_h` 实测：

```json
{
  "struct": 88,           // struct_budget = 50  → 已超 1.76 倍
  "extend": 5,            // extend_budget = 10  → 未超
  "adapt": 1,             // adapt_budget = 1000 → 未超
  "overloads": {
    "struct_overload": true,    // ⚠️ struct 超了
    "adapt_overload": false,
    "extend_overload": false
  },
  "within_budget": true          // ← 矛盾：struct 已超，但"在预算内"仍 true
}
```

**两层根因**：

1. **struct 超预算只打标记、不触发解体**。`config/experiment_config.json` 明确注释："struct 超预算仅打 struct_overload 标记，不直接触发 Γ脆 P_C——结构庞大不直接致命"。
2. **P_C（Γ脆 判据③）只看 extend，而 extend 被封顶**。`extend = min(累计反例 96, 降级阈值 5) = 5`，永远 `5 < extend_budget 10`，`within_budget` 恒 true。

**结果**：`within_budget` 恒 true → Γ脆 判据③ 永不触发 → 规则池膨胀到 104 公理、ICC FAIL 193 issues 时，系统**无任何拦截**。

---

## 六、修复清单

| # | 缺陷 | 修复方案 | 涉及文件 |
|---|---|---|---|
| 1 | anchor 体系不对齐 | `VALID_ANCHORS` 动态化：从 `world_rules.json` 候选池收集全部 anchor 并入白名单，或候选规则 anchor 统一映射到基础锚点域（P_M1/P_W1/P_C/P_W2_5） | `src/w2/icc_auditor.py` |
| 2 | 激活缺边界条件 | 激活时传入候选的初始 `boundary_conditions`（`add_axiom` 补第 4 参数）；候选无边界时标记 `pending_boundary` 而非空 | `src/w2/axiom_activator.py`、`src/w2/axiom_engine.py` |
| 3a | 边界补全撞车 | 边界自动补全前检查是否与现有公理边界重复（复用 ICC 的 `_normalize_bc` 判重） | `src/w1/rule_validator.py` |
| 3b | C_H 拦不住膨胀 | 把 `struct_overload` 纳入 Γ脆 判据（或为"规则池膨胀"新增独立崩解判据），不再"只打标记" | `src/w3/closed_loop_runner.py`、`config/experiment_config.json` |

> 说明：缺陷 3b 是最关键的一处——它直接对应"CUF 促进 AI 自我进化"里"让进化受控"的核心诉求：进化不能只"会变"，还要"变得对、变到快崩时能刹住"。

---

## 七、结论

1. **规则池"会自我优化"是实证的**：阶段 1 干净环境下，反例驱动降级 + 边界细化成立（P_M1 supported）。
2. **但"自我优化"在 200 候选池 + 真实世界下失控了**：3→104 公理，ICC FAIL 193 issues，3 条基础公理全被污染降级。
3. **失控的三大直接原因**：anchor 体系不对齐（86）、激活缺边界（88）、边界补全撞车（19）。
4. **失控没被拦住的根本原因**：C_H 的 Γ脆 判据只看被封顶的 extend，struct 超预算只打标记——**代价核算机制对"规则池膨胀"这个真实崩溃模式失效**。
5. **对"用 CUF 促进 AI 自我进化"的启示**：进化引擎是真实存在的，但要让进化"受控"，必须先修好这三个缺陷（尤其 3b 的刹车），否则自主进化只会变成"自主膨胀 + 污染"。
