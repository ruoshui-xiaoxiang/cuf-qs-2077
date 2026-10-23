# 剑星主角可执行操作与交互清单

> 目的：给 agent 一张“Eve 到底能做什么”的事实表。只写能落到证据上的；推断和未验的单独标。
> 更新：2026-10-23 | 证据截止：v3.2 桥 + UMG 血条 + 实机跑动/战斗

## 证据与置信度

| 标记 | 含义 | 来源 |
|---|---|---|
| ✅ 文件 | 游戏自己的字符串表/存档 | `StringTable_UI.uexp`、`save_skills.json` |
| ✅ 实机 | 本机亲手按出来/量出来 | 2026-10-23 会话 |
| ⚠ 攻略 | 公开攻略/社区，未在本机复验 | PS5/PC 控制页 |
| ❓ 未验 | 游戏动作存在，但键位/行为没确认 | — |

关键文件：
- `extracted/SB/Content/Local/Localize/StringTable_UI.uexp` — 游戏内 80 个可绑定动作 + 27 种交互提示
- `data/save_skills.json` — 当前存档 170 个已获得技能/节点
- `extracted/SB/Content/Local/Data/SkillTable.*` 等 — 技能定义资产（DataTable 未解析）

---

## 1. 游戏自己认的 80 个可绑定动作（✅ 文件）

这是游戏设置里“按键绑定”菜单的动作全集，说明这些行为在引擎层是存在的。

### 移动 / 攀爬 / 游泳（16）
`MoveAxis`、`MoveAxis_Forward/Back`、`MoveButton_Forward/Back/Left/Right`、`Sprint`、`ToggleSprint`、`Walk`、`ToggleWalkType`、`Jump`、`Swim_Up`、`Swim_Down`、`Swim_Dash_Evade`、`GrabPivotChange`

### 战斗（24）
`LightAttack`、`StrongAttack`、`Guard`、`Evade`、`EvadeOnly`、
`Continuation`、`Continuation_Command`、`Continuation_PressCheckCommand`、
`BetaSkill1~4`、`BetaSkillMode`、
`BurstSkill1~4`、`BurstSkillReady`、`BurstSkillView`、`ToggleBurstSkillViewType`、
`TachyMode`、`TachyMode_Hide`、`TachyMode_Hide_Command`、`TachyMode_Hide_PressCheckCommand`

### 锁定（4）
`LockOn`、`LockOnMove_Axis`、`LockOnMove_Next`、`LockOnMove_Previous`

### 无人机远程（5）
`TPSMode`、`TPS_Fire`、`TPS_BulletChange`、`ToggleFireType`、`ToggleTPSModeType`

### 物品 / 菜单 / 交互（14）
`PotionUse`、`PotionChange`、`ItemUse`、`ItemChange`、
`InventoryMenu`、`SkillMenu`、`Map`、`Mission`、`Option`、`PhotoMode`、
`Scan`、`Hint`、`Interaction`、`InteractionCancel`

### 钓鱼（6）
`Fishing_Attack`、`Fishing_LiftUp`、`Fishing_Reel`、`Fishing_Snatch`、`Fishing_Swing`、`Fishing_SwingReady`

### 镜头（7）
`CameraAxis`、`CameraAxis_LookUp`、`CameraAxis_Turn`、
`CameraBtn_LookUpRate_Up/Down`、`CameraBtn_TurnRate_Left/Rigt`（原文拼写 Rigt）

### 触摸板手势（4，PC 键盘无）
`SwipeUp/Down/Left/Right`

---

## 2. 27 种世界交互提示（✅ 文件）

字符串键本身带提示键位字母：

- **F**：`F_InBattle`（战斗中交互）、`F_InConversation`（对话中交互）
- **T**：`Activate_Camp`、`AmmoSupply`、`Check`、`CheckGift`、`Check_Corpse`、`Open_Box`、`Open_Door`、`Operate`、`Pet`、`PhoneBooth`、`Play_Instrument`、`Rest`、`Search`、`SkillMachine`、`Skip_NextMusic`、`Take_a_Look`、`Talk`、`ToolBox`、`TurnOff`、`TurnOn`、`Turntable`、`Use`、`Use_VendingMachine`、`Decorate`、`CompleteEdition`

⚠ 注意：`T` 可能是“触摸板（Touchpad）”提示而不是键盘 T。实机在营地自动售货机看到的是 **F 选择音乐**，说明 PC 交互至少有一部分是 F。
❓ 本机代码目前把 `interact` 绑在 **E** 上，尚未验证。

---

## 3. 当前存档已获得的能力（✅ 文件，170 条）

`save_skills.py` 从 `AcquisitionSkillList` 解析；等级由“已获得哪些 Upgrade 节点”体现，不是单一等级字段。

| 类别 | 条数 | 代表节点 |
|---|---|---|
| 战斗基础 | 63 | `DoubleJump1`、`AirBreakFall1`、`FastGetup1`、`JustEvade1`、`JustParry1`、`BackStab1`、`DashAttack1~4`、`Blaze1`、`ShieldBreak1`、`SwordAura1`、`Triplet1`、`MassiveAttack1`、`MoveBackAttack1`、`AirDropAttack1`、`AirGuardBreak1`、`FlashBehindAttack1`、`LightAttackAdditionalCombo1~4`、`StrongAttackAdditionalCombo1~4` |
| 升级节点 | 55 | `Blaze1Upgrade1_1`、`ShieldBreak1Upgrade4_1`、`ParryUpgrade3_1`、`EvadeUpgrade3_1`、`Drone_ScanUpgrade1_1`、`Drone_HackingUpgrade1_1` |
| 爆发技 | 12 | `BurstFinishAttack1`、`BurstSpinAttack1` 及升级 |
| Tachy 模式 | 20 | `TachyMode_SwordAura1`、`TachyMode_JustEvadeLightAttack1`、`TachyMode_ChainStab1`、`TachyMode_ParrySlash1` |
| 点数/道具/模式 | 20 | `P_EVE_PT_Skill_01~03`、`P_EVE_PT_Burst_01~03`、`P_Eve_ModeOpen_Burst/TPS/Tachy`、钓鱼升级、堆叠消耗品 |

从名单能**确定存在**的行为：二段跳、空中受身/落地、快速起身、闪避、完美闪避、招架、完美招架、背后处决/刺杀、冲刺攻击、轻重连段追加、空中攻击、空中破防、闪身背击、格挡反击、贝塔与爆发技、Tachy 模式、无人机扫描/骇入/探测/弹种升级、钓鱼。

⚠ 这些 alias 里哪些属于 Beta / Burst / 被动，需要 `SkillTable` 全表才能精确归类；当前按名字与攻略对照，不硬断。

---

## 4. PC 键位：实测 vs 推断

> 2026-10-24 已在游戏内「设置 → 键盘与鼠标 → 查看和设置操作键位」逐项实拍，
> 下表**键位**一栏均为游戏内原文；"行为实机"一栏才是本机动作验证。

| 键 | 游戏内名称 | 键位来源 | 行为实机 |
|---|---|---|---|
| W/S/A/D | 前/后/左/右 | ✅ 键位页 | ✅ 位移 |
| Space | 跳跃 | ✅ 键位页 | ✅ z 轴变化 |
| **Shift（按住）** | **突进** | ✅ 键位页 | 待重测（旧结论用了错的 VK） |
| **Shift（点按）** | **闪躲** | ✅ 键位页 | ✅ 10-24 键位页确认同键，点按/长按区分 |
| V | 切换突进 / 快速游动 | ✅ 键位页 | 未验 |
| C | 行走切换 / 取消动作 | ✅ 键位页 | 未验 |
| 鼠标移动 | 镜头转向 | ✅ 键位页 | ✅ 0.0933°/单位，需先把光标放回窗口内 |
| **鼠标左键** | 快攻 | ✅ 键位页 | ✅ 连段命中 |
| **鼠标右键** | 强攻 | ✅ 键位页 | ✅ 重击命中 |
| **鼠标中键** | **锁定** | ✅ 键位页 | 未验 |
| 鼠标移动（锁定时） | 切换锁定目标 | ✅ 键位页 | 未验 |
| 滚轮上/下 | 锁定前一个/下一个目标 | ✅ 键位页 | 未验 |
| **E** | **格挡/招架** | ✅ 键位页 | 用户确认 |
| 左键+右键 | 特殊指令 | ✅ 键位页 | 未验 |
| T | 特殊模式 | ✅ 键位页 | 未验 |
| 1/2/3/4 | 贝塔技能 1~4 | ✅ 键位页 | 4 号耗 BE 已实测（588→426） |
| Alt | 爆发技能显示 | ✅ 键位页 | 未验 |
| Alt+1/2/3/4 | 爆发技能 1~4 | ✅ 键位页 | 未验 |
| Ctrl | 远程模式 | ✅ 键位页 | 未验 |
| 左键（远程模式） | 远程攻击 | ✅ 键位页 | 未验 |
| 滚轮（远程模式） | 选择弹药 | ✅ 键位页 | 未验 |
| **R** | **使用/选择物品** | ✅ 键位页 | 阵亡界面 R 复活另验 |
| **Q** | **使用/选择药水** | ✅ 键位页 | 不是格挡、不是闪避 |
| **F** | **互动** | ✅ 键位页 | ✅ 售货机提示 |
| Esc | 菜单 | ✅ 键位页 | ✅ 打开暂停菜单 |
| M / K / I / J | 快速地图 / 技能 / 物品栏 / 任务 | ✅ 键位页 | 未验 |
| H | 提示 | ✅ 键位页 | 未验 |
| P | 拍照模式 | ✅ 键位页 | 未验 |
| Tab | 扫描 | ✅ 键位页 | ✅ 扫描圈+标记 |

暂停菜单结构（10-24 实拍）：
- 顶部标签：装备 / 任务 / 地图 / 背包 / 技能 / 数据库 / 说明手册 / 设置；**E 切下一个标签**，Q 切上一个（顶部有 E 提示）。
- 面板内 W/S 移动选择，Enter 进入/确认，Esc 返回/离开。
- 设置 → 键盘与鼠标 第一项「查看和设置操作键位」= 完整键位表。
- **读档（✅ 10-24 实跑）**：设置 → 游戏 → 最下面「从上一次存档重新开始 → 确认」，弹窗提示「距离上次存档已经过 X」→ Enter 是 / Backspace 否 → 约 10 秒读图。
  实测：读档后坐标 31085,30068,-19481，与存档备份时记录差 1.8 米；HP 6500/6500、SH 500/500、药水满；读档后按 W 1.2 秒移动 2.3 米，操控正常。
- **存档（✅ 10-24 实跑）**：菜单里没有手动存档。在营地终端/营地休息即存档——`StellarBladeSave00.sav` 的 mtime 会更新。
- **菜单方向键**：上下左右必须走 **VK 注入**；带 `extended+scan` 的方向键（0x4D/0x50）游戏不认，按了不移动。

⚠ 更正记录（10-24）：此前把 Q 当闪避是**错的**——Q 是药水；闪避是 **Shift 点按**。旧 `inputs.py` 的 `sprint` 还错写成 VK 0x02（右键），已改为 Shift(0x10)。reflex v2 的 `tap_key("dodge")` 之前按的是 Q，防守动作因此无效/语义不明，需要按新键位重做。

### 10-24 实战：血量与击杀判据的实测结论（重要）

**玩家 HP 三条路全部读不到**：
1. UMG：`probe widgets` 拿到 120 根 ProgressBar，其中 40 根可见，**可见的全是敌方条**（7 根 `WB_MainHUD_EnemyStateBar` 的 HP + 一堆 ShieldSmall），玩家血条不在这个控件树里。
2. 像素数格（`hud.py`）：营地背景下自动锚定失败，把屏幕顶部白条当血条，读出 `band=(81,83)`、hp_pips=10 的假值。
3. 引擎：玩家 77 个组件里没有任何 Stat/Health/Condition 组件；`probe enemy` 被只读闸拒（缺 `allow_write.txt`），props 里也没有血量字段。

**屏幕左上红度不是可靠的血量尺**（我造的尺，已自验推翻）：

| 状态 | HP | TL 红度 |
|---|---|---|
| 营地满血（7 帧一致） | 6500/6500 | 17.8 |
| 喝药后 | ~4000 | 30.2 |
| 混战中 | 2477/6500 (38%) | 36.6 |
| 濒死 | 1393/6500 (21%) | 79.1 |
| 阵亡黑屏 | 0 | **7.5** |

⇒ 同方向单调，但**会被构图污染**（阵亡黑屏反而最低）。只能当"同场景同机位下的相对信号"，不能当绝对血量。

**HUD 敌条不能当"同一只怪"的证据**：场上 7 根敌条，`hud_live` 取第一根，镜头一动就换目标。实测连打 12 下左键，E.hp 恒为 0.638、护盾恒 0.35×29，无法判断是否命中。⇒ 击杀判据改用**敌人实例名从 `near_pawns` 消失**（`reflex.target_alive()`）。

**Q=药水 实战验证**：HP 2477 时按 Q，红度 36.6→30.2，确实喝到了。⚠ 但菜单开着时 Q 是"上一个标签"，会误触——菜单里不要发战斗键。

**阵亡→R 复活**：三次实测，R 复活后固定回到营地 31232,30080（存档点旁 1.5m），HP/盾/药水全满。阵亡画面判据（候选，正样本仅 1 帧）：中央带暗像素比例落在 0.25~0.60；纯黑过场是 0.77，可区分。


---

## 5. Agent 现在能做 / 不能做

**已能发（`inputs.py` 白名单）**：WASD、Shift（点按闪躲/按住突进）、Space、E（格挡）、Q（药水）、R（物品）、F（互动）、C（取消/行走）、T（特殊模式）、左/右键、中键锁定、Tab（扫描）、1~4（贝塔技能）、M/K/I/J/H/P（UI）、Esc（仅 `pause_menu()` 专项调用）。

**已补的专项组合**：`burst(1~4)`（Alt+数字）、`ranged_mode()`（Ctrl）、`special()`（左+右）、`lock_on()`（中键）。

**不能/不可靠**：
- 长按格挡（E 长按是否持续格挡/完美招架窗口未验）
- 攀爬/游泳/绳索（键位页只给了 V 快速游动，其余触发条件未验）
- 钓鱼（键位页有抛竿/收线/邀鱼/提竿，全是鼠标，触发条件未验）
- 菜单类交互（营地休息/快速旅行/训练场）已能进菜单，但路径未逐项实测
- 存档：仅营地/存档点休息会存；**读档 ✅ 已实测**（设置→游戏→底行→Enter→是）

---

## 6. Agent 操作约定（2026-10-23 用户口述）

1. **卡住先走存档/读档**：遇到卡地形/卡死，优先用存读档脱离，不要在原地硬试。
2. **寻路用截图视觉辅助**：不要只按引擎坐标直线走；用截图看开阔地和地形，配合无人机扫描（Tab）。
3. **允许自由探索/自由验证**：可以按查到的资料自己试键位、交互和战斗，不用每步请示。
4. **战斗只有两条路**：要么一套连招把敌人打死，要么连续撤退直到脱战；不要打三下就停。
5. **键位口径**：E=格挡，F=交互，Q≠格挡；Q 的真实功能待验。

---

## 7. 待验证清单（按影响排序）

1. ~~真闪避键~~ → 已确认 **Shift 点按=闪躲**（键位页+实测 214cm），按住+方向=突进。
2. ~~Q 的真实功能~~ → 已确认 **Q=使用/选择药水**。
3. **格挡用法**：E 点按/长按、完美招架窗口、格挡与贝塔技能的关系。
4. **交互键 E vs F**：在售货机/终端/尸体上分别试 E/F/T。
5. **锁定键**：中键已加（MMB），未实战验证。
6. **技能槽 1~4**：每个键对应哪个 Beta 技能、耗什么资源。
7. ~~菜单键~~ → 已验：M/K/I/J/H/P + Esc 菜单 + 读档路径（设置→游戏→底行）。
8. **攀爬/游泳/绳索**：靠近可攀爬点时的提示键。
9. **截图辅助寻路的最小流程**：截图 → 选开阔方向 → 小步走 → 复核距离/画面。

---

## 8. 对当前 reflex 的直接结论

- 触发源（HUD UMG 百分比）是可靠的：玩家 HP、两层盾、敌人 HP、四段盾。
- 连招（左3→右→左3→4）实测能把敌人打到血条消失/脱战，玩家从 1.0 只掉到 0.916。
- **防守动作是错的**：reflex v2 按的 Q 既不是格挡也不是已确认的闪避；E 才是格挡。下一步先定 Q 是什么、真闪避键是哪个，再重测存活率。
- 敌人血条会换目标：`EnemyStateBar` 在多个 CH_M 之间切换，目标身份还没锁定。

---

## 8. 首领挑战「亚巴顿」实测（2026-10-24 直播场）

场景：屋顶雨夜。入口在楼梯上方，走近触发过场（约 20 秒，右上角可跳过）；过场结束才能开战。
首领：`CH_M_NA_39_Blueprint_C`，HP 39,600、盾 2,160、9 层盾。玩家进场景时 HP/盾满、**BU/BE 清零、药水 3**。

**伤害量级（读屏幕数字，不是自造尺）**

| 动作 | 首领血量 | 差值 | 单发效率 |
|---|---|---|---|
| 开战 | 39,600 | — | — |
| 38 下普攻（打两下就闪） | 35,869 | −3,731 | ≈98/下 |
| 再 118 下普攻 | 30,273 | −5,596 | ≈47/下 |
| **爆发 Alt+4 一发** | 23,759 | **−6,514** | ≈138 下普攻 |
| 贝塔技能 key_4（BE 950/2800） | 23,759 | 0 | 没放出来（能量不足或槽空） |

普攻打死剩下的 3 万血要 600+ 下；爆发才是主要输出，一次消耗 BU 2,200。

**🔴 首领脱战回血极快**：停手 75 秒（期间只做截图与量尺），血量从 23,759 回到 **37,325**，约 **180 血/秒**。
⇒ `reflex.py` 那套「打几轮没效果就撤 18 秒」在首领战里等于白打——撤退期间它把血回满。首领战必须连续压制，靠闪避+喝药撑，不能靠撤退拖。

**节奏化打法确实能活**：「2 下左键 → Shift 闪避 → 面向修正」循环 40 秒，Eve 只掉 1,091 血（6500→5409）；同期站桩连招 30 秒内阵亡 1 次。

**我造的两把尺在这场景都失效（第三次犯同类错，记死）**
- 屏幕左上红度：雨夜屋顶下满血也只有 1.5 / 0.8 / −0.6，低血时 6.7，阵亡黑屏 7.5——它量的是构图不是血。之前营地那组「17.8/36.6/79.1」是同机位下的巧合，不能当绝对血量。
- 首领条像素填充比：满血读到 0.684 与 0.723（同状态两个值），38 下攻击只从 0.710 动到 0.707，而真实血量掉了 9.4%。原因是条是分段格，阈值把灰空格也算亮。
⇒ 唯一可靠读数：**直接读屏幕上的数字**（39,600 / 26,923 这种），裁顶部条右侧数字区看即可。

**阵亡与复活**：首领战里阵亡 → 画面只有 `R 复活` / `ESC 离开`（没有白血泵）。R 复活退回入口、HP/盾/药水全恢复、**BU/BE 清零**、过场重播（约 20 秒）。

⚠ 上面第 7 节「触发源（HUD UMG 百分比）是可靠的」这条**作废**：10-24 实测玩家 HP 条根本不在 UMG ProgressBar 树里（40 根可见条全是敌方条），当时读到的 1.0→0.916 是敌人条或预置条。

