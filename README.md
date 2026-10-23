# qs-play —— 让模型自己玩 3A（阶段 0／1）

DeepSeek 视觉决策 + 操作系统级键鼠输入 + 帧差客观证据。不注入 DLL、不碰内存、不碰 UE4SS。

## 现在能跑什么

```bash
# 阶段 0：无游戏、无 GPU，只验决策连贯性与闸门（¥0.02 / 12 轮）
py -3.12 loop_mock.py --rounds 12
py -3.12 loop_mock.py --rounds 12 --no-coach     # A/B：去掉引导句

# 阶段 1：真截图 + 真输入（需要游戏在前台）
py -3.12 loop_play.py --goal "走到正前方最近的可交互对象身边" --shots 6 --dry-run
py -3.12 loop_play.py --goal "..." --shots 20 --interval 3    # 去掉 --dry-run 即动键鼠
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
| `loop_play.py` | 阶段 1 驱动器：截图→意图→输入→归档，成本超 ¥0.5 自停 |

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

未验，且只能真跑才答：**UE4 的 Raw Input 可能忽略注入的鼠标相对位移**。
真跑先验键盘；look 不灵就改用 Q/E 或方向键转身，别在鼠标上耗。

## 已知边界与坑

- 阶段 0 的 mock **没有后果模拟**（`mock_game_server.py` 只有只读 handler，无 hp/damage）
  ⇒ `executed=false / effect=not_simulated`，跑绿不等于闭环成立。
- 阶段 1 只有帧差，**没有游戏侧真值**（剑星侧没有等价于 CufBridge 的只读通道）
  ⇒ 判"它真做到了"要么靠人眼看录像，要么等 2077 那条只读观测通道回来。
- 剑星 PC 版带 Epic Online SDK / PlayStation PC SDK（需 PSN 登录）与 `crs-uploader.exe`
  等上传件。本回路只做读屏＋OS 级按键，但不能保证带在线组件的发行版监测不到第三方输入。
- 资源互斥：ComfyUI_H3（8189）带 `--reserve-vram 6.0`，空载也占 5.4GB 显存 / 25GB 私有内存。
  起游戏前先停它；回来用 `D:\AIWorkflow\ComfyUI_H3\run_nvidia_gpu.bat`。
- 全屏抓屏会把桌面其它窗口一起发给云端（用户 2026-10-23 裁定：本机无敏感信息，接受）。
  若日后需要，`--crop` 到游戏窗口是现成改法。
- 修过的两个自己的 bug：`loop_play.py` 的 `prev` 未赋值 → `cells` 恒 1.0（会把"画面动了"变成永真）；
  `brain.py` 首跑把 `max_tokens` 卡在 220，被 reasoning 吃满 → 解析 100% 失败。
