# Agent 任务监督组件第二阶段计划

## 第二阶段目标

第一阶段已经完成：

- 任务模型
- 规则校验
- 默认裁决
- JSONL 审计
- 自测闭环

第二阶段要解决的问题是：

- 让 supervisor 接入真实执行流，而不是只消费外部 observation
- 让 `retry / block / manual_review / complete` 真正驱动一次任务运行
- 给后续接外部 Agent 平台留出稳定适配协议

## 第二阶段范围

本阶段只做三个新增模块：

1. 执行适配器协议
2. 监督运行器
3. 最小 CLI 演示入口

## 模块设计

### 1. 执行适配器协议

新增统一适配层：

- `TaskExecutorAdapter`
- `AdapterExecutionResult`

作用：

- 屏蔽真实任务执行来源
- 统一把执行结果映射成监督系统可消费的结构

第一版先支持：

- 本地函数适配器
- 同步执行
- 返回结构化结果

暂不做：

- 异步队列
- 并发任务池
- 远程 RPC 执行器

### 2. 监督运行器

新增：

- `SupervisedTaskRunner`

职责：

- 按任务规范发起执行
- 收集执行结果
- 转成 `TaskObservation`
- 调用 `TaskSupervisor`
- 根据 `retry / block / manual_review / complete` 决定下一步

它是把四层真正闭成一圈的关键。

### 3. 最小 CLI 入口

新增：

- `python -m supervisor.cli --demo <name>`

作用：

- 低成本演示 supervisor 的真实运行效果
- 方便后续接 CI、自测或 demo

## 第二阶段验收

达到以下条件即可判定通过：

1. 能通过适配器执行一个真实任务函数
2. 能在失败后自动重试
3. 能在使用禁用工具时阻断
4. 能在缺少证据时转人工复核
5. 能把每轮执行结果写入 JSONL
6. 新增自测通过

## 第三阶段预留

第二阶段完成后，再考虑：

- 外部 Agent 平台适配器
- 多任务树
- 人工接管队列
- 最小监控视图
