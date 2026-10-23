# Agent 任务监督组件 MVP 方案

## 目标

把现有四层框架从“实验闭环”收缩成一个可复用的 `Agent Task Supervisor`
内核，优先解决以下问题：

- 任务是否真的完成，而不是只被模型声称完成
- 任务是否触发了越权、超时、空输出、缺证据等风险
- 失败后是重试、转派、人工接管，还是直接阻断
- 整个过程是否可以被审计、回放和复核

本阶段不做“万能 Agent 平台”，只做监督内核。

## 新定位

组件名称建议：

- `agent_task_supervisor`

一句话定义：

- 一个面向多 Agent 任务执行的监督与审计组件

核心卖点：

- 任务真值监督
- 规则化风险闸门
- 裁决与升级策略
- 全链路审计归档

## 四层映射

### W0 任务观测层

输入任务运行事实：

- 当前状态
- 尝试次数
- 使用过的工具
- 输出摘要
- 错误信息
- 证据列表
- 元数据

这层只做结构化采集，不做裁决。

### W1 规则校验层

对任务事实做硬规则判定：

- 是否超时
- 是否调用禁用工具
- 是否空输出却宣称完成
- 是否缺少要求的证据
- 是否超过重试预算

这层只产生违反项，不直接决定怎么处置。

### W2 裁决与升级层

基于违反项和任务状态给出处置：

- `allow`
- `retry`
- `manual_review`
- `block`
- `complete`

这一层是监督组件的大脑。

### W3 执行编排层

输出控制信号和审计记录：

- 下一步动作
- 建议操作者
- 接管建议
- JSONL 归档

这层不负责实现外部 Agent 平台本身，只负责发出监督控制结果。

## MVP 范围

第一版只做以下能力：

1. 任务规范建模
2. 观测结构建模
3. 规则校验器
4. 默认裁决策略
5. JSONL 审计归档
6. 一组自测样例

第一版暂不做：

- GUI
- 多租户
- 分布式队列
- 复杂 DAG 调度
- LLM 自动写规则
- 自动调用真实外部 Agent 平台

## 输入输出协议

### 输入

`TaskSpec`

- 任务 ID
- 任务名称
- 最大重试次数
- 超时阈值
- 必需证据类型
- 禁用工具列表
- 元数据

`TaskObservation`

- 当前任务状态
- 尝试次数
- 输出内容
- 错误列表
- 使用工具列表
- 运行时长
- 证据列表
- 元数据

### 输出

`SupervisorDecision`

- 控制动作
- 目标状态
- 原因
- 确认状态
- 下一步建议
- 违反项列表

## 复用策略

现有工程里可直接借鉴的不是“理论叙事”，而是这些机制：

- [rule_validator.py](file:///E:/6a8b0dd1b3ead574d77ceda3/src/w1/rule_validator.py)
  - 可复用点：硬编码规则校验、fail-loud 口径
- [downgrade_manager.py](file:///E:/6a8b0dd1b3ead574d77ceda3/src/w2/downgrade_manager.py)
  - 可复用点：预警/升级/阈值触发、边界收紧思路
- [closed_loop_runner.py](file:///E:/6a8b0dd1b3ead574d77ceda3/src/w3/closed_loop_runner.py)
  - 可复用点：统一编排骨架、轮级处理顺序
- [log_recorder.py](file:///E:/6a8b0dd1b3ead574d77ceda3/src/w3/log_recorder.py)
  - 可复用点：JSONL 追加式审计归档

## 目录建议

```text
src/supervisor/
  __init__.py
  models.py
  rules.py
  policies.py
  store.py
  supervisor.py
tests/
  agent_supervisor_selftest.py
docs/
  agent_task_supervisor_mvp.md
```

## 第一版验收标准

满足以下条件即可视为第一版可跑：

1. 能输入任务规范和任务观测
2. 能跑出违反项列表
3. 能对以下场景给出明确裁决
   - 正常完成
   - 完成但缺证据
   - 失败且可重试
   - 使用禁用工具
4. 能把每次监督结果落成 JSONL
5. 自测全部通过

## 后续迭代

如果第一版成立，再往下加：

- 人工接管队列
- 多 Agent 任务树
- 子任务依赖与重试预算
- 任务级 SLA 统计
- 简单监控页

## 当前决定

这条线继续推进时，统一遵守以下原则：

- 保留监督机制，不再保留过重理论包装
- 先做监督内核，不做大而全平台
- 一切成功口径以“确认完成”而不是“声称完成”为准
