# Help Output

When `/spec -h` is triggered, output exactly this block and stop:

```text
specode 命令速查
══════════════════════════════════════════════════════

工作流
  /spec <需求描述或文件路径>            一次性规格工作流（需求→设计→任务）
  /spec <名称>：<需求描述>              指定 spec 文件夹名（支持 ： 或 ": "）
  /spec --persist <需求>                启动持久会话模式
  /continue [spec-slug]            列出可继续的 spec，或恢复 / 切换到指定 spec
  /status                          显示当前会话状态（含锁状态）
  /end                             结束当前会话并释放锁（不删除文档）

任务执行（多 agent 并发）
  /task-swarm [<spec-dir>/tasks.md]     按一级阶段派发 coder/reviewer/validator 子 agent
                                        缺省取当前 active spec 的 tasks.md
                                        reviewer/validator 物理无 Edit/Write，防自我认可
                                        协议: references/task-swarm.md

Obsidian / 根目录配置
  /spec --set-vault <vault路径>         设置 Obsidian vault（spec 存入 vault/spec-in/<os>-<user>/specs）
  /spec --set-root <目录>               直接设置 spec 文档根目录（完全自定义路径）
  /spec --detect-vault                  检测已安装的 Obsidian vault
  /spec --vault-status                  显示当前 vault / spec root 配置 + 旧位置警告

任何时候都可以重新运行 --set-vault / --set-root 修改根目录；新值立即写入
~/.config/specode/config.json 并被后续命令使用。

帮助
  /spec -h                              显示本帮助

文档根目录解析
  1. --root 参数 / SPECODE_ROOT 环境变量
  2. ~/.config/specode/config.json → obsidianRoot
  3. 自动检测 Obsidian vault → <vault>/spec-in/<os>-<user>/specs（首次检测自动写入 config）

三级全部未命中 → 终止 /spec，输出引导提示。

/continue 多窗口行为
  - 不同窗口可同时持有不同 spec
  - 同一 spec 同一时刻只允许一个窗口持有写锁（.config.json.lock）
  - 选择已锁定 spec 时，提示三选项：强制接管 / 只读查看 / 取消
  - /end 仅结束当前会话并释放锁，不影响其他会话或 spec 文档

spec 文档结构
  <root>/<spec-slug>/requirements.md        需求与验收标准（agent 必须传 --name slug）
  <root>/<spec-slug>/bugfix.md              缺陷规格（替代 requirements.md）
  <root>/<spec-slug>/design.md              技术设计
  <root>/<spec-slug>/tasks.md               任务列表 + 测试要点（供测试人员的 SHALL 级验证场景）
  <root>/<spec-slug>/.config.json           specId / lock / iterationRound
  <root>/.active-specode.json             v2 窗口索引（slug-only）

持久会话状态行格式
  ─── specode ─── spec: <slug> | session: <id> | phase: <phase> | /end 退出
  只读模式额外标记 [只读]：
  ─── specode ─── spec: <slug> | session: <id> | phase: <phase> | [只读] | /end 退出
```
