# 公开版纳入与排除清单

本仓由私有仓 `cuf-qs` 抽出，裁定依据是仓内 `docs/public_project_inventory_and_disposition.md`
§D「公开同步排除区」。私有仓本身未做任何改动，仍是完整版的唯一真源。

## 已纳入（2820 个文件）

`src/` `config/` `tests/` `tools/` `logs/`（含 `logs/pc_hl/` 实机归档 186 文件，
以及 `logs/formal_batch_7b_v3/` 全量 1992 文件）`paper/` `reports/` `docs/` `provenance/`，
以及根目录除下方脚手架外的历史探索脚本（按处置稿 §C 冻结保留，可追溯，不作当前事实源）。

## 已排除

| 路径 | 文件数 | 排除理由 |
|---|---|---|
| `cpp/` | 5 | 平台专用原生接入实现（§D） |
| `cuf_native/` | 3 | 原生接入构建与源码（§D） |
| `game_mods/CufBridge/init.lua` | 1 | 游戏侧模组实现，属底层接入痕迹（§D） |
| `s1_backup/` | 20 | 模组配置与版本锁序列备份，§D；这 21 个文件在源仓工作区已被删除，仅存于历史。其中 `version_lock.json` 的证据锚点部分已抽取进 `provenance/`（见下） |
| `.trae/cuf_v7_extract.txt` | 1 | CUF v7 理论主文本；处置稿决定 2 已冻结理论扩写，且该文件当初是以 `git add -f` 破例入库，公开与否尚未裁定 |
| `gen_cmake.py` `gen_cufnative.py` `gen_main.py` `gen_main_p1.py` | 4 | 上述被排除区的构建脚手架。四个脚本本身不含实现正文（只有路径与构建骨架），但公开它们等于把底层接入的构建布局带出去，而读者又拿不到被排除的源码，对复现无增益 |
| 实机截图 `*.png` | — | 媒体不入仓，仓内只保留结构化数据与文本 |
| git 历史 | 3 commits | 不随公开版带出。历史中含 22 个盘上已删文件（21 个 `s1_backup/` + `_fix_test.py`），发布历史会把 §D 内容再带一遍 |

## 一处按「证据完整性优先」回收入库的拆分

`s1_backup/version_lock.json` 原件不公开——它的 `ops_notes` 段是逐版 CufBridge 实现笔记、
hook 候选路径、注入 DLL 与传送 API 探测记录，正是 §D 要挡的"可能被误读为平台绕行说明的操作笔记"，
而且它是全项目实现密度最高的文档。

但同一次实验的**版本、模组栈、硬件与 12 条 SHA256 指纹**属于证据锚点，缺了读者无法确认
数据是在哪个环境产出的。因此只抽取这些作证字段，落成 `provenance/version_lock_evidence.json`，
`overlay_binding` 与 `ops_notes` 两个字段在件内点名标注为未公开，并登记原件 SHA256
`a0cbf0de82133a5a3fd6597333ad513c4911ca98913504e517109bfb533fcbe4` 供持有原件者自验。

同理，`logs/formal_batch_7b_v3/`（1992 文件、最大单文件 0.9 MB）在首版公开时以"体积超出代码仓承载"
为由排除，随后按证据完整性优先全量入库——论文 3.4 的六个 pooled 数字就落在这批文件里，
没有它们，那些结论只能靠不可见的项目记录背书。

## 如果你不同意其中某项

最容易反转的是 `gen_*.py` 那 4 个脚手架，以及 `.trae/cuf_v7_extract.txt`。
前者放回是一条 `git add -f` 的量级；后者放回前需要你先裁 v7 理论文本的公开性——
那一份和专利相关稿同线，一旦见光收不回。
