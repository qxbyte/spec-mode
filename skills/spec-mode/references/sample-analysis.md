# Kiro Sample Analysis

Observed source:

```text
/Users/xueqiang/Git/markdown/.kiro/specs/undo-redo-support/
├── requirements.md
├── design.md
├── tasks.md
└── .config.kiro
```

The sample is a requirements-first feature spec for undo/redo support in a macOS Markdown editor.

## Directory Shape

Kiro stores one folder per concrete requirement:

```text
.kiro/specs/<spec-name>/
```

This skill adapts that shape to the requested project layout:

```text
<document-root>/<requirement-name>/
```

Do not create an extra `spec/` child folder in this project's output layout.

## `requirements.md` Pattern

The sample structure:

```text
# 需求文档
## 简介
## 词汇表
## 需求
### 需求 1：...
**用户故事：** ...
#### 验收标准
1. WHEN ..., THE ... SHALL ...
```

Important characteristics:

- Chinese headings when the user context is Chinese.
- Intro includes implementation context and observed problem roots.
- Glossary defines local domain terms.
- Requirements are numbered.
- Each requirement has a user story.
- Acceptance criteria use EARS-style English modal verbs inside Chinese documents.
- Criteria are specific enough to map into tasks.

## `design.md` Pattern

The sample structure:

```text
# 设计文档：<feature>（<slug>）
## 概述
## 架构
### 现有架构
### 修复后架构
## 组件与接口
## 数据模型
## 正确性属性
```

Important characteristics:

- Design starts by restating the implementation goal.
- It names current failure points before proposing changes.
- It uses code snippets only where useful for precision.
- It prefers minimal invasive changes over broad rewrites.
- It includes correctness properties that map back to requirements.

## `tasks.md` Pattern

The sample structure:

```text
# 实现计划：<feature>（<slug>）
## 概述
## 任务
- [ ] 1. High-level task
  - [~] 1.1 Subtask in progress
    - implementation bullets
    - 文件：...
    - _需求：6.1、6.2_
- [ ] 4. 检查点 —— 确保所有测试通过
```

Important characteristics:

- Tasks are nested and actionable.
- Task numbers are human-readable, not opaque IDs.
- `[~]` is used for in-progress work.
- Requirement traceability uses `_需求：..._`.
- Checkpoint tasks appear between implementation stages.
- Optional task notes may be used for lower-priority work.

## `.config.kiro` Pattern

The sample contains:

```json
{"specId": "...", "workflowType": "requirements-first", "specType": "feature"}
```

This skill uses `.config.json` in the generated requirement folder for portability:

```json
{
  "specId": "...",
  "workflowType": "requirements-first",
  "specType": "feature",
  "documentRoot": "...",
  "requirementName": "...",
  "createdBy": "spec-mode"
}
```
