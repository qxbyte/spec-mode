# 验收操作清单：{{name}}（{{slug}}）

Spec Type: {{spec_type}}
Workflow: {{workflow}}
Status: Acceptance Checklist Draft
Review Status: unreviewed

## 使用说明

面向测试人员或验收人员，逐项执行以下操作，记录实际结果。所有 required 验收项通过后，才能确认本次需求功能点全部实现。

本文档由 agent 根据 `requirements.md` / `bugfix.md` 中的 EARS `SHALL` 语句自动生成；任何需求文档修改后，agent 必须在同一轮 turn 内重写本文件。

## 前置条件

- [ ] 已切换到包含本次实现的分支或环境。
- [ ] 已完成 `tasks.md` 中 required 任务。
- [ ] 已准备必要账号、数据、配置或测试输入。
- [ ] 已确认需要运行的验证命令或手工测试入口。

## 验收步骤

<!--
agent 填充规则：
- 读取 requirements.md / bugfix.md 中每一条 SHALL 语句
- 每条 SHALL 生成一行：
    功能点    = 该 SHALL 所属的需求名 / 编号
    操作步骤  = 测试人员可执行的具体动作（禁止"触发该能力"这种泛化描述）
    预期结果  = 直接引用 SHALL 后的期望行为
    实际结果  = "待记录"
    结论      = "待验证"
- 必须删除模板里"核心能力 / 异常输入 / 回归行为"这种通用占位行
- iteration 阶段重新生成时，保留上一轮"结论=通过"的行并将结论列标记为
  "已验收（迭代 N-1）"，新增 SHALL 追加新行
-->

| 序号 | 功能点 | 操作步骤 | 预期结果 | 实际结果 | 结论 |
| --- | --- | --- | --- | --- | --- |
| _agent 待填充_ | _基于 requirements.md SHALL 语句_ | _可执行的具体步骤_ | _SHALL 后的期望_ | 待记录 | 待验证 |

## 验收结论

- [ ] 所有 required 功能点已按操作步骤验证。
- [ ] 所有 required 验证命令已通过，或跳过原因已记录并被接受。
- [ ] 未完成、失败或跳过的 optional 项已记录。
- [ ] 用户或验收人员确认通过。

## 问题记录

- [ ] 无阻塞问题。
- [ ] 如存在问题，已记录复现步骤、实际结果、期望结果和证据。
