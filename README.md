# qs-play —— 让模型自己玩 3A（阶段 0／1／2）

DeepSeek 视觉决策 + 操作系统级键鼠输入 + 像素帧差 + UE4SS 只读状态桥。
不注入 DLL、不改内存；引擎侧只读，写动作要过 `allow_write.txt` 这道闸。

## 现在能跑什么

```bash
# 阶段 0：无游戏、无 GPU，只验决策连贯性与闸门（¥0.02 / 12 轮）
py -3.12 loop_mock.py --rounds 12
py -3.12 loop_mock.py --rounds 12 --no-coach     # A/B：去掉引导句

# 阶段 1：真截图 + 真输入（需要游戏在前台，自动带引擎真值）
py -3.12 loop_play.py --goal "走到正前方最近的可交互对象身边" --shots 6 --dry-run
py -3.12 loop_play.py --goal "..." --shots 20 --interval 3    # 去掉 --dry-run 即动键鼠
py -3.12 loop_play.py --goal "..." --shots 6 --no-engine      # A/B：只给像素通道

# 角色名单（只读游戏文件，不用启动游戏）
py -3.12 game_catalog.py            # 写 data/characters.json
py -3.12 game_catalog.py --print    # 额外打印分类明细
```

依赖：`py -3.12` + PIL（本机已装）。不装 mss / pyautogui。
API：`https://api.deepseek.com/chat/completions`，`deepseek-flash`，`effort=low`，
key 读 `DEEPSEEK_API_KEY` 环境变量。图片走内存 base64 `data:` URL，不落盘不托管。
系统代理开关当前为 0，所以调用是全直连（git 才绑 7890，那是它自己的 config）。

## 模块

| 文件 | 职责 |
|---|---|
| `brain.py` | 观测/截图 → 意图。文本通道 + 视觉通道 |
| `capture.py` | 全屏抓屏 + 16×9 粗网格帧差（客观"世界动没动"） |
| `inputs.py` | SendInput；**白名单外的键在代码层就进不来**（ESC/ALT/CTRL/F 键等） |
| `gate.py` | 钩子写权限闸门（阶段 0 用；按"能否当证据"分级，不按次数） |
| `loop_mock.py` | 阶段 0 驱动器，复用公开仓的 `ActionPlanner.validate` |
| `loop_play.py` | 阶段 1/2 驱动器：截图 + 引擎真值 → 意图 → 输入 → 归档，成本超 ¥0.5 自停 |
| `sb_bridge.py` | 读 UE4SS 文件桥（含新鲜度 1.5s 闸与写闸）；桥停就自动退回纯像素 |
| `hud.py` | 敌人浮动条/自身四条资源条 → 格数（只读观测） |
| `hud_digits.py` | HUD 数字模板匹配草稿；**沙漠亮背景下已判死**，数值优先从引擎读 |
| `probe_reach.py` / `probe_moves.py` | 训练场标定（带对照组与满状态前置校验） |
| `step.py` | 手动单步：动作前后抓帧算帧差，追加 `runs/agent_log.jsonl` |
| `game_catalog.py` | 从 `.utoc` 名字表 + actor dump 列出敌人/NPC 名单 → `data/characters.json` |
| `save_skills.py` | 从存档 `AcquisitionSkillAlias` 读主角已获得技能/能力 → `data/save_skills.json` |
| `check_lua.py` | 用 lupa 编译 Lua，避免语法错要重启游戏才发现 |

## 闸门分级（阶段 0，钩子通道）

| 动作 | 档 | 每场配额 | 证据上限 |
|---|---|---|---|
| move | reach | 无限 | observable |
| pickup | reach | 2 | conditional（移出观测窗≠真拾取） |
| attack | outcome_write | 3 | conditional（钩子写≠游戏因果链） |
| kill | forbidden | 0 | none |

`kill` 永久禁的判据不是审美，是 2026-08-24 的实测：HP 直写能掉血，但 `attack_debug`
里 16 条 OnHit 全是环境战斗、目标一次没中，无动画无布娃娃——判"静默移除"，
不构成 L3 外部证据。2026-08-27 已把 kill 移出自主白名单（见公开仓 config 的 `planner._note`）。

## 阶段 0 实测结论（2026-10-23，12 轮 × 2 组）

| 组 | accepted | 解析失败 | 被闸门拦 | 最长同动作连打 | 攻击过的不同目标数 |
|---|---|---|---|---|---|
| 有引导 | 10/12 | 0 | 2 | 3 | 2 |
| 无引导 | 7/12 | 1 | 4 | 4 | 1 |

延迟 1.8 / 3.1 / 5.2 秒（均值 3.1）。缓存命中 77%。成本 ¥0.0196 / ¥0.0214。

**最重要的一条，别忘**：无引导组 r2 原话"前两轮攻击**已生效**，继续集中火力摧毁它"，
而那三轮的 `obs_hash` 完全相同。零反馈时模型会凭空宣布进展。
⇒ 所以本项目的裁决规则是：**进展只认帧差与游戏侧观测，模型的 claim 永远只当预期，不当结论。**

## 阶段 1 已验／未验

已验：抓屏 3840×2160 可用；静置两拍帧差 `cells=0.000`（判"没动"正确）；
SendInput 被系统接受（光标实际位移 234px，说明**指针速度 1.56× 会乘进注入值**，转向量要按此校准）；
白名单外按键被代码拒绝；一张真剑星 4K 截图 → 2.7s / ¥0.0018，且模型正确读出
"视角被角色面部特写挡住"（那是拍照模式特写，说明它在读画面不是在编场景）；
端到端 dry-run 3 拍 `cells` 1.000→0.000→0.000 正常。

已答（2026-10-23 真机）：**UE4 Raw Input 接受 SendInput 的相对鼠标位移**。
横移 300 单位后帧差 cells=1.000 / mean=28.2，反向 300 后与原帧只差
cells=0.062 / mean=2.0 ⇒ 镜头真的转了，也能转回来。

## 阶段 2：引擎真值（UE4SS 文件桥）

`SBBridge`（Lua，装在游戏 `…\Win64\ue4ss\Mods\SBBridge\Scripts\main.lua`）
每拍写 `state.json`，Python 侧 `sb_bridge.py` 读。2026-10-23 真机实测：

| 项 | 实测 |
|---|---|
| 写周期 | 请求 80ms，实测 **87~106ms**（游戏线程忙时更慢） |
| 玩家坐标 | 可读，静置 10s 位移 **0.0 cm**（引擎真值地板是 0） |
| 玩家朝向 | `player.yaw` 是**身体朝向**，站着转镜头不动；按 W 才对齐到镜头 |
| 鼠标转角 | **0.0933° / 单位**（100/200/400 单位 → 9.33/18.66/37.33°，线性） |
| 行走 | 约 **290 cm/s**（W） |
| 冲刺（Shift+方向） | 单按 Shift 1s 位移 **1cm**（不冲刺）；组合键一次 0.5~0.8s 实测总位移 **15~16 米**（含停步滑行）。精确瞬时速度要等 v3 的 `vel` |
| 同伴/敌人距离 | 可读（无人机约 130~180cm 浮动；训练靶类 `CH_M_NA_29`） |
| 附近 pawn | `near_pawns` 前 3 个，含类名与距离（沙漠里是 `CH_M_NA_48/49`） |
| 只读探针 | `cmd.json` 写 `{"action":"probe"}` → `probe.json`，无需解锁 |
| tick 日志的"耗时 50ms" | **不是墙钟**：`os.clock()` 是进程 CPU 时间，多线程游戏下会虚高；判成本看 `state.json` 的实际间隔 |

`loop_play.py` 每拍把上一拍的**引擎位移/转向**与像素帧差一起写进提示词，
claim 由两路实测核对；桥过期时自动退回纯像素，`--no-engine` 可做 A/B。

仍缺、且都要改 Lua 并重启才拿得到：
- **镜头朝向**（`SBNetworkPlayerController.ControlRotation`）——身体 yaw 不能当镜头用；
- **敌人 HP/护盾**（pawn 上没有 HP 属性；要么 GenerateSDK 取真实属性名，要么继续读像素条）；
- 敌人类名目前写死 `CH_M_NA_29`，沙漠杂兵是 `CH_M_NA_48/49`。

前两条里的"镜头朝向"和"敌人类名泛化"已经写进 **v3**：
`Scripts/main.v3.lua`（306 行，`check_lua.py` 编译通过），并已拷成 `main.lua`；
**当前这局跑的还是 v2，重启后才会加载 v3**。v2 原件留作 `main.v2.lua`。
v3 新增 `view`（镜头 yaw/pitch）、`vel`（2D 速度）、`enemy` 认任意 `CH_M_*` 类，
风险读全部包在 `pcall` 里，ControlRotation 读不到时只是没有 `view` 字段。
敌人 HP 仍未解决——那条要么 GenerateSDK，要么另找组件。

## 复合动作（Shift+方向）

2026-10-23 实测：**单按 Shift 1 秒位移 1cm**（不会冲刺），必须 Shift 与方向同时按住。
现在 `inputs.COMBOS` 提供 `sprint_fwd / sprint_back / sprint_left / sprint_right`，
`hold_combo()` 按顺序按下、反序松开；模型动作里 `sprint` 已被移出可选名单，
换成了这四个组合。`hold_key` 新增可选 `dur`（0.1~1.5s，默认 0.6）——
空地实测一次 0.5~0.8s 冲刺的总位移是 15~16 米（含停步滑行），
近距离必须给 `dur=0.1~0.2`，默认 0.6 会冲过头。

## 主角技能/能力：能从存档读“已获得”，定义表还没解

存档（`%LOCALAPPDATA%\SB\Saved\SaveGames\<SteamID>\StellarBladeSave00.sav`）
不是加密/压缩的：外层 8 字节 `EVAS`，里面是标准 GVAS 属性序列化，
`AcquisitionSkillList` 的每一项是 `AcquisitionSkillAlias`（NameProperty）。
`save_skills.py` 实测读出 **170 条**：

| 类别 | 条数 | 例子 |
|---|---|---|
| 战斗基础 | 63 | `Blaze1`、`ShieldBreak1`、`JustParry1`、`JustEvade1`、`SwordAura1`、`DashAttack1` |
| 升级节点 | 55 | `Blaze1Upgrade1_1`、`ShieldBreak1Upgrade4_1`、`Drone_ScanUpgrade1_1` |
| 爆发技 | 12 | `BurstFinishAttack1`、`BurstSpinAttack1` 及升级 |
| Tachy 模式 | 20 | `TachyMode_SwordAura1`、`TachyMode_JustEvadeLightAttack1` |
| 点数/道具/模式 | 20 | `P_EVE_PT_Skill_01..03`、`P_Eve_ModeOpen_Burst`、钓鱼与堆叠消耗品 |

同时读到 `SPLevel=279`、`SPExp=9809299`（存档时）。

边界：
- 这是**已获得状态**，不是“游戏里全部技能定义”；名字/描述/消耗/命中效果在
  `SkillTable`、`SkillStringTable`、`SkillCommandTable`、`SkillResultTable` 等资产里，
  在 `.ucas` 内，**尚未解包**，也没做运行时 DataTable dump。
- 存档只在保存时更新（本轮实测 22:24 落盘）；刚学的技能要看内存，得等 v3.1 探针。
- `SkillSlotMap`、`ExospineEnhanceStateMap`、`Gear` 也在存档里，属性名可见，
  但本轮只解析了技能列表与 SP，槽位/外骨骼尚未解析。

## 角色名单：能从游戏文件读，且不用解包工具

`pakchunk0-WindowsNoEditor.utoc` 里有一段**明文 ASCII 名字表**（无 `/Game/` 前缀，
只有资产基名），直接扫可打印串即可得到全部角色资产：

| 来源 | 结果 |
|---|---|
| `.utoc` 名字表 | **170 个**角色蓝图：怪 **106** / NPC **53** / 玩家 6 / 无人机 2 / 道具 3 |
| `.utoc` 骨架表 | 170 个骨架基名：怪 84 / NPC 61 |
| UE4SS actor dump（Ctrl+7） | 当次地图**实际 spawn** 的 15 个 `CH_*` 类（训练场：`CH_M_NA_29` 靶机 ×1、`CH_M_NA_47` ×10 等） |

结论与边界：
- **"游戏里有哪些角色"** → 能读，`data/characters.json` 已落盘；名字是资产代号
  （`CH_NPC_Kaya/Barry/Adam` 可读，杂兵是 `CH_M_NA_xx` 代号）。
- **"这张图实际刷了谁、在哪"** → 名字表答不了，要用运行时 dump 或桥的
  `near_pawns`（v3 起敌人按任意 `CH_M_*` 识别）。
- 没找到生怪表（`SpawnTable`/`MonsterTable` 均 0 命中）；要读关卡刷怪逻辑得解包
  或走 UE4SS 运行时查 DataTable，暂未做。
- 武器/材质等带 `_A/_N/_ORM` 后缀的同名资产不算角色，脚本已按 `_Blueprint`/`_BP`
  与 `_Skeleton` 过滤。

## 已知边界与坑

- 阶段 0 的 mock **没有后果模拟**（`mock_game_server.py` 只有只读 handler，无 hp/damage）
  ⇒ `executed=false / effect=not_simulated`，跑绿不等于闭环成立。
- 阶段 2 的引擎真值只覆盖**玩家位置/身体朝向、同伴与最近 pawn**；敌人 HP/护盾
  仍不在桥里（pawn 上找不到属性），所以"打掉多少盾"暂时只能靠 HUD 像素条，
  而 `hud_digits.py` 在沙漠亮背景下已判死——这一块要么 GenerateSDK，要么停。
- 剑星 PC 版带 Epic Online SDK / PlayStation PC SDK（需 PSN 登录）与 `crs-uploader.exe`
  等上传件。本回路只做读屏＋OS 级按键，但不能保证带在线组件的发行版监测不到第三方输入。
- 资源互斥：ComfyUI_H3（8189）带 `--reserve-vram 6.0`，空载也占 5.4GB 显存 / 25GB 私有内存。
  起游戏前先停它；回来用 `D:\AIWorkflow\ComfyUI_H3\run_nvidia_gpu.bat`。
- 全屏抓屏会把桌面其它窗口一起发给云端（用户 2026-10-23 裁定：本机无敏感信息，接受）。
  若日后需要，`--crop` 到游戏窗口是现成改法。
- 修过的两个自己的 bug：`loop_play.py` 的 `prev` 未赋值 → `cells` 恒 1.0（会把"画面动了"变成永真）；
  `brain.py` 首跑把 `max_tokens` 卡在 220，被 reasoning 吃满 → 解析 100% 失败。

## 阶段 3（2026-10-24）：模型进环 —— 战术层每秒一拍

**分工**：模型只做战术（每秒一个词），脚本做 200ms 级反应。

- `tactician.py`：把状态摘要发给 `deepseek-flash`（`thinking.type=disabled`），
  输出 `push / poke / retreat / skill / heal / wait` 之一。
- `fight_agent.py`：反应层（闪光→闪避、距离→接近、低血→喝药、失焦即停）
  + 执行层（意图翻译成连段/后撤/技能）+ 决策日志 `runs/agent_*.csv`。
- 意图带 2.5s TTL：模型慢或挂掉时沿用上一个意图，手不会停。

**实测延迟**（同一状态、关思考）：

| 条件 | 延迟 |
|---|---|
| 单独压测（3 次） | 0.42 / 0.47 / 0.70 s |
| 开思考（对照） | 1.65 / 2.73 / 1.98 s（每次 185~280 reasoning token） |
| 实战 139 次决策 | p50 **0.64 s**、p95 **0.92 s**、最慢 1.08 s |

**首战结果（输）**：意图分布 poke 63% / retreat 24% / push 12% / heal 1%。
结论：延迟不是瓶颈，**信息缺口**才是——模型看不到首领血量，也没有距离趋势，
只能保守试探。下一轮要把这两样喂进去。

## 战斗策略：连段优先（10-24 定）

用户原话：「闪避和格挡会打断主角自己的攻击动作，所以不能连续不停的释放，技能收益更大」。

| 版本 | 击杀用时 | 受到伤害 | 药水 |
|---|---|---|---|
| 旧版（每刀后格挡） | 02:41.76 | 15,888 | 6 |
| 新版（5 连段 + 收尾 0.14s 格挡 + 技能轮询） | **01:24.65** | **2,948** | 0 |

- 技能：BE 满才能放；**2/3/4 实测消耗 BE**，`1` 有一次没掉（不确定）；`Alt+4` 是爆发。
- 闪光→闪避保留为反应层，但阈值别放太低（k=1.8 时 18 次/150s，多数是特效误判）。
- **颜色预警不可用作判据**：蓝色是场景底色（中位 256/p95 459），红色残血晕影
  从 42s 起飙到 8,170；按色判读会误判成"一直在闪红"。

## HUD 充能尺（hud_energy.py）

左下 BU/BE/HP/SH 四条分段条按固定坐标数亮列：

- 满格 ≈ 0.787，空 ≈ 0.02~0.04（段间黑缝导致满格不等于 1.0）；
- 反推真值：读 0.265 ↔ BE 1,192/2,800；读 0.345 ↔ HP 3,205/6,500；
- **游戏切后台会暂停且整块 HUD 不画**，此时读数为零，连续 8 次才按 Space 解暂停。

## 键位修正（10-24 实拍游戏内键位页）

| 动作 | 键 | 备注 |
|---|---|---|
| 格挡/招架 | E | 旧代码误标为"互动" |
| 互动 | F | |
| 药水 | Q / R | |
| 突进 / 闪躲 | Shift | **只认扫描码 0x2A**；纯 VK 注入无效（实测 22cm vs 214cm） |
| 取消 / 行走 | C | |

> 早前"Shift 冲刺无效"的结论作废：旧代码里 `0x02` 是鼠标右键，不是 Shift。

## 底特律：变人 接入勘察（10-24，搁置）

- **无状态接口**：Quantic Dream 自研引擎（非 UE），社区只有 CE 修改器。
- **合成键盘全被过滤**：SendInput（VK/扫描码）、`keybd_event`、`PostMessage` 全部无效；
  **鼠标注入有效**（镜头可转），说明引擎在设备层区分两条通道。
- 绕过需 ViGEm 虚拟手柄或 Interception 驱动；用户 10-24 决定先搁置（不装驱动）。
- 键位/设置菜单结构：影像、音讯、语言、难度、操作方式；未发现独立辅助页。
- 辅助选项（简化 QTE / 关闭选择限时）未核实。
