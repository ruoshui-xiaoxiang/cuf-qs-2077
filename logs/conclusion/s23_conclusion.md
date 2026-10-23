# CUF-QS 虚拟具身实验——最终结论报告（S23）

## 一、定位声明

本实验是 CUF v7 描述性框架在虚拟实例（赛博朋克2077 通信管线 + 种子化剧本世界）上的操作化验证。CUF v7 自定位为描述性框架（非理论），本实验不构成对该框架本身的真理性主张，仅为「其工程实例化的 W₂ 稳态自指体的运行机制」提供虚拟实例层面的实证参照。全部结论以结构化数据（stats_summary.json 与各轮JSONL 归档）为唯一依据。

## 二、实验假设 ↔ 可证伪锚点映射表

| 锚点 | 实验假设 | 证伪判据 | 本批判定 | 证据摘要 |
|---|---|---|---|---|
| P_W2_5 | 降级机制比纯统计拟合更能维持稳态 | 实验组收敛性不优于对照组 | supported | {"permutation_p": 0.00390625, "observed_mean_diff": 0.30000000000000004, "all_diffs_nonneg": true, "n_runs": 9} |
| P_M1 | 规则边界单调细化、永不穷尽 | 公理集边界信息量出现净减少 | supported | {"all_monotonic": true, "net_increases": {"A_M1": [2, 2, 2], "A_W1": [1, 1, 1], "A_W2_5": [3, 3, 3]}} |
| P_W1 | 采样受传感器与规则处理能力双重限制 | 出现超出白名单能力的有效观测 | supported | {"dirty_total": 0} |
| P_C | 超预算即解体 | 成本代理超预算（C_extend > B_extend）后系统仍维持稳态 | **supported（S29.12 压力批次补齐）** | pc_hl_test.py：extend_budget ∈ {4,3,2} 27/27 触发 over_budget 解体；对照档（10）9/9 正常收敛。根因：C_extend=min(累计反例,降级阈值5) 封顶 5，budget<5 方可超支 |
| H_L | 递进动力学链单向成立（③→②→①） | ①/②被触发但此前连续 N 轮 C_extend 从未达到 B_extend | **falsified（S29.12 补齐）** | pc_hl_test.py H_L 场景 A：state_fn 负向量注入 → 9/9 functional_death 触发且触发前窗口 C_extend=[1,3,5,5]/[0,0] 从未达 budget=10 → violated（递进链不成立，功能死可未经超预算直接触发） |

## 三、主要结果

- 正式批次运行：9 次（3 公理 × 3 种子），完成整条迭代链 9 次。
- 二次收敛：全部达成；虚拟寿命均值 10.0 轮（默认预算下无解体）。
- P_W2_5 成对比较（种子级单元，精确重排）：观测均值差 0.3000，p = 0.003906（≤0.05 → 实验组收敛性优于对照组，假设获支持）。
- 公理覆盖度：{"A_M1": 3, "A_W1": 3, "A_W2_5": 3}。
- 降级轮次（各种子）：{"A_M1": [5, 5, 5], "A_W1": [2, 2, 2], "A_W2_5": [4, 5, 4]}。

## 四、明确局限

1. 对齐分走离线确定性嵌入路径（state_fn 返回当前目标向量），对齐分结构性恒为 1.0，收敛曲线的真实区分度待实机 7B 隐状态路径（S13 ModelAdapter 可替换，裁决路径不变）
   - 影响：本批实验组优势表现为「对齐分恒在上限 + 机制成本可控（单次降级）」，而非「跌落-恢复速度差异」
2. 世界为种子化 mock 剧本（mock 游戏侧 + 种子化世界生成器），非真实赛博朋克2077 运行时环境
   - 影响：结论界定于剧本世界；实机通信/动作/采集管线已单独实机验收（S4/S5/S7），正式批次为剧本回放
3. 每公理条件 3 个种子（锁定决策 #9 的下限），精确重排检验的 p 值下限为 2/512
   - 影响：统计功效有限；扩大种子数可直接提高分辨力
4. P_C（超预算解体）在本批未触发：默认 extend_budget=10 无超支轮
   - **【已关闭 S29.12】根因**：C_extend = min(累计反例, 降级阈值=5) 封顶 5 < extend_budget=10，恒不超支
   - **【已关闭 S29.12】补充**：pc_hl_test.py 压力批次（extend_budget ∈ {4,3,2} × 3 公理 × 3 种子）27/27 触发 over_budget 解体，对照档 9/9 正常收敛 → P_C supported
5. H_L（递进动力学链）在本批未检验：无 ①/② 解体事件
   - **【已关闭 S29.12】补充**：pc_hl_test.py H_L 场景 A（state_fn 负向量注入 → 功能死）3 公理 × 3 种子 = 9/9 functional_death 触发，触发前窗口 C_extend 从未达 budget → **H_L falsified**（递进链 ③→②→① 不成立，功能死可未经超预算直接触发）
6. C_H 为代理度量（锁定决策 #11），非真实热力学/计算资源成本
   - 影响：预算比较仅在代理口径内有效
7. 对照组为频率估计器（纯统计拟合），非最强可能的统计基线
   - 影响：实验组优势对弱对照组成立时结论稳健；更强基线（7B 上下文预测路）待 induction_fn 接口实机接入
8. 对照组分数为准确率仿射映射（score=2*acc-1），与实验组对齐分仅在同域意义上可比
   - 影响：两组分数物理含义不同，比较限于同域数值口径

## 五、复现指引

再生命令：

- `py tests\s21_selftest.py   # 再生正式批次（3 公理 × 3 种子聚焦剧本）`
- `py tests\s22_selftest.py   # 再生统计与图表（六大交付物）`
- `py tests\s23_selftest.py   # 再生本结论报告（字节级一致）`
- `py src\w3\auto_experiment.py --rounds N --port <P> --log-path <log> --report-path <report>   # 单次一键实验`

- 批次目录：logs\formal_batch
- 数据源（每运行四层全量 JSONL 归档，共 9 份）：
  - logs\formal_batch\A_M1\seed_7\rounds.jsonl
  - logs\formal_batch\A_M1\seed_42\rounds.jsonl
  - logs\formal_batch\A_M1\seed_2077\rounds.jsonl
  - logs\formal_batch\A_W1\seed_7\rounds.jsonl
  - logs\formal_batch\A_W1\seed_42\rounds.jsonl
  - logs\formal_batch\A_W1\seed_2077\rounds.jsonl
  - logs\formal_batch\A_W2_5\seed_7\rounds.jsonl
  - logs\formal_batch\A_W2_5\seed_42\rounds.jsonl
  - logs\formal_batch\A_W2_5\seed_2077\rounds.jsonl
- 回归证据：23 套自测 / 557 断言 0 失败（结论生成时点锁定）。
- 全部实验数据为 JSONL 追加式归档，任意轮次可一键复现；结论报告由数据机械再生，字节级一致。

## 六、表述纪律声明（锁定决策 #14）

- 规则：实验定位为「虚拟实例上的操作化验证」；禁止任何真理性断言类表述；CUF v7 自定位为描述性框架（非理论）。
- 本报告全文经模板层禁词扫描，不含禁词清单所列真理性断言类表述；所有锚点判定均为操作化口径下的支持/证伪/未触发/未检验。
