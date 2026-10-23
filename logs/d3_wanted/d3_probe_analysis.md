# D3.② 通缉 API 考古报告

**日期**: 2026-08-27  
**CufBridge 版本**: 0.6.17（新增 probe_wanted handler）  
**探针数据**: `probe_wanted_20260827_042847.json`

---

## 最终结论：CET Lua 层面通缉 API 不可达，但通缉反应可间接观测

---

## 探测覆盖

### 1. 游戏系统探测（16 个候选）

| 系统 | 可用 | 备注 |
|---|---|---|
| PoliceSystem | **NO** | 不存在 |
| WantedSystem | **NO** | 不存在 |
| CrimeSystem | **NO** | 不存在 |
| PreventionSystem | **NO** | 不存在 |
| PlayerSystem | YES | 可用但 pairs 枚举失败（StrongReference 限制） |
| AttitudeSystem | YES | 可用但 pairs 枚举失败 |
| BlackboardSystem | YES | 可用但 PlayerMachineBB 获取失败 |
| StatPoolsSystem | YES | 可用但 pairs 枚举失败 |
| UISystem | YES | 可用但 pairs 枚举失败 |
| AICommandingSystem | NO | 不存在 |
| CommandableSystem | NO | 不存在 |
| GameplayLogicSystem | NO | 不存在 |
| PersistenceSystem | NO | 不存在 |
| SpawnSystem | NO | 不存在 |
| DynamicEntitySystem | NO | 不存在 |
| ScriptableSystem | NO | 不存在 |

### 2. 通缉 API 猜测路径（7 条）

| 路径 | 结果 |
|---|---|
| Game.GetPoliceSystem() | NO |
| Game.GetWantedSystem() | NO |
| Game.GetCrimeSystem() | NO |
| Game.GetPlayerSystem() | **OK**（唯一成功） |
| PlayerSystem.Instance | NO |
| PlayerSystem.GetInstance() | NO |
| Game.GetPreventionSystem() | NO |

### 3. 玩家实体通缉方法（11 个探测）

| 方法 | 结果 |
|---|---|
| GetWantedLevel | 不存在 |
| IsWanted | 不存在 |
| GetHeatLevel | 不存在 |
| GetCrimeLevel | 不存在 |
| GetPursuitLevel | 不存在 |
| GetThreatLevel | 不存在 |
| IsInCombat | **存在** → false |
| IsInPursuit | 不存在 |
| IsDetected | 不存在 |
| GetAttitude | **存在** → userdata |
| GetPlayerAttitude | 不存在 |

### 4. 态度系统

- AttitudeSystem 可用但 `pairs` 枚举失败（CET sol2 对 StrongReference 的限制）
- `GetAttitudeBetweenGroups(player, NCPD/Police/PoliceForce)` 全部失败
- 可能是参数类型不匹配（需要 CName 或特定枚举，而非字符串 hash）

### 5. 黑板系统

- BlackboardSystem 可用
- `GetLocalBlackboard('PlayerMachineBB')` 失败——可能黑板名不对或需要不同获取方式

### 6. 警察扫描

- 扫描范围内无警察标签实体（police_count=0, hostile_count=0）
- 当前区域（玩家位置）无警察 NPC

---

## 附带发现

### CET sol2 的 pairs 限制

所有 5 个可用系统（PlayerSystem/AttitudeSystem/BlackboardSystem/StatPoolsSystem/UISystem）
都返回 `type=sys == "userdata"`，但 `pairs(sys)` 全部报错：

```
sol: cannot call '__pairs/pairs' on type 'sol::as_container_t<StrongReference>': 
it is not recognized as a container
```

这意味着 **CET Lua 无法枚举任何游戏系统对象的方法列表**。
只能通过精确猜测方法名 + pcall 调用来探测。

---

## 后续路径（如需继续）

1. **Red4ext C++ 插件**：直接读取游戏内存中的通缉状态（最强大但投入最高）
2. **精确方法名猜测**：对 PlayerSystem 逐个猜测更多方法名（如 `GetStreetLevel`, `GetNotoriety` 等）
3. **间接观测**：通过扫描警察 NPC 的生成/敌对态来间接推断（当前区域无警察，需换区域测试）
4. **记录边界，转向 D3 构成③**

---

## 触发实验 v1：Status Effect 攻击（失败）

**数据文件**: `wanted_trigger_20260827_043730.json`

传送到 Wellspring (-477,-125,8)，用 player_attack（ApplyStatusEffect）攻击 NPCPuppet。
结果：120s 内 0 hostile、0 新实体。Status effect 属于"隐蔽伤害"，不触发犯罪系统。

## 触发实验 v2：键位模拟——近战武器（失败）

**数据文件**: `wanted_trigger_20260827_045536.json`（Wellspring）

Python SendInput 模拟按 key 2（近战刀）+ 鼠标左键。
结果：Wellspring NPC 本身是敌对帮派，攻击不触发通缉。

## 触发实验 v3：键位模拟——手枪射击平民区（成功）

**数据文件**: `wanted_trigger_20260827_045804.json`

### 实验设计

用户手动走到平民密集区域（位置 -241.3, 295.3, 28.8），
Python 通过 Windows SendInput API 模拟：
- 按 key 3（手枪）切枪
- 鼠标左键 x5 射击
- 每 5s 扫描一次，持续 60s

### 关键结果

| 时间 | 总实体 | NPC | 敌对 |
|------|--------|-----|------|
| 基线 | 3 | 1 | 0 |
| 10-40s | 13-21 | 2-7 | 0 |
| **45s** | **20** | **6** | **3** ← 通缉响应 |
| 50s | 20 | 6 | 2 |
| 55s | 17 | 4 | 2 |

### 结论

1. **键位模拟可行**：SendInput 扫描码 + 鼠标点击可成功切枪和射击
2. **通缉反应可间接观测**：通过 scan_entities 的 hostile 标签变化可检测
3. **响应延迟 ~45s**：游戏需要时间处理犯罪并生成响应
4. **API 层仍不可达**：无法读取 WantedLevel 数值，但 hostile 变化是有效间接信号

---

## 三层可达性总结

| 层 | 状态 | 说明 |
|---|---|---|
| API 层 | **不可达** | PoliceSystem/WantedSystem/CrimeSystem 在 CET 中不存在 |
| 触发层 | **可达** | SendInput 模拟枪击可触发通缉系统（需 key 3 手枪，非近战/状态效果） |
| 观测层 | **部分可达** | 无法读取通缉等级数值，但 hostile 标签变化可作为间接信号 |
