# 实现计划：用户登录流程（user-login）

Spec Type: Feature
Workflow: requirements-first
Status: Tasks Confirmed
Review Status: confirmed

> 这是一份 **specode 风格** 的 tasks.md 示例。
> 在 specode 会话中走到"任务执行"selector 时选第 3 项「用 task-swarm 多 agent 并发」，
> 或直接 `/task-swarm <此文件路径>` 触发。
>
> task-swarm 会按一级阶段聚合派发：
> - 阶段 1（3 子任务）→ 1 个 coder + 1 个 reviewer
> - 阶段 2（检查点）→ 1 个 validator
> - 阶段 3（2 子任务）→ 1 个 coder + 1 个 reviewer
> - 阶段 4（检查点）→ 1 个 validator
> - 阶段 5（可选，coder-only）→ 1 个 coder
>
> 总共 7 个 subagent，而不是朴素 1:3 展开的 21 个。

## 概述

实现用户登录流程：账号密码登录、登出、密码强度校验、登录失败锁定。

## 任务

- [ ] 1. 实现登录核心流程
  - [ ] 1.1 写 User model
    - 文件：`src/models/user.py`
    - 验证：`pytest tests/test_user.py`
    - _需求：1.1_
  - [ ] 1.2 写 auth service
    - 文件：`src/auth/service.py`
    - 验证：`pytest tests/test_auth_service.py`
    - _需求：1.2_
  - [ ] 1.3 写 login controller
    - 文件：`src/api/login.py`
    - 验证：`pytest tests/test_login_api.py`
    - _需求：1.3_

- [ ] 2. 检查点 — 登录核心流程通过端到端
  - 运行 `pytest tests/test_login_e2e.py -v`。
  - 如有失败，停止继续执行并向用户确认。

- [ ] 3. 实现登出与会话失效
  - [ ] 3.1 auth service 增加 logout(token)
    - 文件：`src/auth/service.py`
    - _需求：2.1_
  - [ ] 3.2 logout controller
    - 文件：`src/api/logout.py`
    - _需求：2.2_

- [ ] 4. 检查点 — 登出流程跑通
  - 运行 `pytest tests/test_logout.py -v`。

- [*] 5. 优化：登录失败锁定计数器
  - [ ] 5.1 加 Redis 失败计数 @swarm:coder-only
    - 文件：`src/auth/lockout.py`
    - _需求：可选_

## 测试要点

供测试人员快速了解需要验证的场景。每行对应 `requirements.md` 中的一条 SHALL；需求变更时由 agent 在同一轮 turn 内同步本节。

- [ ] 输入合法用户名+密码 → 登录成功，返回 token（需求 1.1）
- [ ] 密码少于 8 位 → 拒绝并提示"密码长度不足"（需求 1.2）
- [ ] 已登录用户调用 /logout → token 失效（需求 2.1）
- [ ] 连续 5 次错误密码 → 账号锁定 15 分钟（需求 1.3 / 可选 5.1）

## 验收

- [ ] 所有 required 任务完成。
- [ ] 所有指定验证命令通过。
- [ ] 未完成或跳过的 optional 任务已记录。
- [ ] 用户确认验收。
