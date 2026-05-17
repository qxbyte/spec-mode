"""Unit tests for task_swarm_outbox parsers."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import task_swarm_outbox as O  # noqa: E402


# ---------- result.md ----------

RESULT_OK = """\
# 阶段 1 执行结果

## 子任务状态
- 1.1 写 user model: done — src/models/user.py
- 1.2 写 auth service: done — src/auth/service.py
- 1.3 写 controller: done — src/api/login.py

## 关键变更
- 新增 User dataclass

STATUS: ok
"""


def test_parse_result_ok():
    v = O.parse_result(RESULT_OK)
    assert v.judgment == "ok"
    assert len(v.subtasks) == 3
    assert v.subtasks[0]["num"] == "1.1"
    assert v.subtasks[0]["status"] == "done"


def test_parse_result_failed():
    text = RESULT_OK.replace("STATUS: ok", "STATUS: failed: ImportError 缺 init")
    v = O.parse_result(text)
    assert v.judgment == "failed"
    assert "ImportError" in v.status_reason


def test_parse_result_missing_status():
    text = "## 子任务状态\n- 1.1 x: done — src/x.py\n"
    v = O.parse_result(text)
    assert v.judgment == "schema-error"
    assert any("STATUS" in e for e in v.raw_errors)


def test_parse_result_missing_subtasks_section():
    text = "## 关键变更\n- 新增 X\n\nSTATUS: ok\n"
    v = O.parse_result(text)
    assert v.judgment == "schema-error"
    assert any("子任务状态" in e for e in v.raw_errors)


def test_parse_result_empty_subtasks_section():
    text = "## 子任务状态\n\n## 关键变更\nx\n\nSTATUS: ok\n"
    v = O.parse_result(text)
    assert v.judgment == "schema-error"


# ---------- review.md ----------

REVIEW_APPROVED = """\
## 结论
approved-with-comments

## P0 — 阻塞，coder 必须修复（修完才能进 validator）
(none)

## P1 — 建议修复，不阻塞
- src/models/user.py:12 — email 字段没做格式校验

## P2 — 可选改进
- 命名 auth_svc 可改

STATUS: ok
"""


def test_parse_review_approved():
    v = O.parse_review(REVIEW_APPROVED)
    assert v.judgment == "approved"
    assert v.p0_count == 0
    assert v.loop_warning is False


REVIEW_P0 = """\
## 结论
needs-changes

## P0 — 阻塞，coder 必须修复（修完才能进 validator）
- src/auth/service.py:34 [req:1.3] — login 失败没区分错误码
- src/api/login.py:8 [security] — 缺 rate limit

## P1 — 建议修复，不阻塞
- src/models/user.py:12 — email

## P2 — 可选改进
- 命名

STATUS: ok
"""


def test_parse_review_p0_items():
    v = O.parse_review(REVIEW_P0)
    assert v.judgment == "p0"
    assert v.p0_count == 2
    assert "service.py" in v.p0_items[0]


def test_parse_review_loop_warning():
    text = "## 进入死循环风险\n连续 2 轮同 P0\n\n" + REVIEW_P0
    v = O.parse_review(text)
    assert v.judgment == "loop"
    assert v.loop_warning is True


# ---------- P0 evidence-tag rules (C) ----------

REVIEW_P0_NO_TAGS = """\
## 结论
needs-changes

## P0 — 阻塞，coder 必须修复（修完才能进 validator）
- src/auth/service.py:34 — 我觉得这里命名不够清晰
- src/api/login.py:8 — 可以加点防御性校验

## P1 — 建议修复，不阻塞
- ...

## P2 — 可选改进
- 命名

STATUS: ok
"""


def test_parse_review_no_evidence_tags_downgrades_to_advisory():
    """P0 lines without [req:..]/[security]/[contract] tag → advisory only."""
    v = O.parse_review(REVIEW_P0_NO_TAGS)
    assert v.judgment == "approved"  # no blocking P0 → approved
    assert v.p0_count == 0
    assert v.advisory_p0_count == 2
    assert "service.py" in v.advisory_p0_items[0]


REVIEW_P0_MIXED = """\
## 结论
needs-changes

## P0 — 阻塞，coder 必须修复（修完才能进 validator）
- src/auth/service.py:34 [req:1.3] — 违反 SHALL 1.3
- src/api/login.py:8 — 我觉得这里可以更好（无证据）
- src/api/login.py:22 [contract] — 接口契约不一致

## P1 — 建议
- ...

## P2 — 可选
- ...

STATUS: ok
"""


def test_parse_review_mixed_tags_only_tagged_block():
    v = O.parse_review(REVIEW_P0_MIXED)
    assert v.judgment == "p0"
    assert v.p0_count == 2  # only the two tagged items
    assert v.advisory_p0_count == 1
    assert "[req:1.3]" in v.p0_items[0]
    assert "[contract]" in v.p0_items[1]
    assert "无证据" in v.advisory_p0_items[0]


def test_parse_review_req_tag_with_dotted_id():
    text = REVIEW_P0_MIXED.replace("[req:1.3]", "[req:2.4.7]")
    v = O.parse_review(text)
    assert v.p0_count == 2
    assert "[req:2.4.7]" in v.p0_items[0]


def test_parse_review_tag_case_insensitive():
    text = REVIEW_P0_MIXED.replace("[security]", "[SECURITY]").replace("[contract]", "[Contract]")
    v = O.parse_review(text)
    # Two tagged items remain blocking (one with [req:1.3], one with [Contract])
    assert v.p0_count == 2


def test_parse_review_missing_p0_section():
    text = "## 结论\napproved\n\nSTATUS: ok\n"
    v = O.parse_review(text)
    assert v.judgment == "schema-error"
    assert any("P0" in e for e in v.raw_errors)


def test_parse_review_missing_status():
    text = "## 结论\napproved\n\n## P0 — 阻塞\n(none)\n"
    v = O.parse_review(text)
    assert v.judgment == "schema-error"


def test_parse_review_short_p0_heading():
    """Reviewer might emit `## P0` instead of the long form."""
    text = (
        "## 结论\nneeds-changes\n\n"
        "## P0\n- src/x.py:1 [req:1.1] — bad\n\nSTATUS: ok\n"
    )
    v = O.parse_review(text)
    assert v.judgment == "p0"
    assert v.p0_count == 1


# ---------- validation.md ----------

VALIDATION_PASS = """\
## 判定
pass

## 复现命令
```bash
pytest tests/test_login.py
```

## 按子任务的验证结果
- [x] 1.1 user model
- [x] 1.2 auth service

STATUS: ok
"""


def test_parse_validation_pass():
    v = O.parse_validation(VALIDATION_PASS)
    assert v.judgment == "pass"


VALIDATION_FAIL = """\
## 判定
fail

## 复现命令
```bash
pytest tests/test_lockout.py
```

## 失败现场
expected 423 got 401

## 按子任务的验证结果
- [ ] 1.3 controller: fail

## 给 coder 的修复指引（必填）
- 文件: src/api/login.py
- 位置: login() 失败分支
- 问题: 没调 lockout

STATUS: ok
"""


def test_parse_validation_fail_with_guidance():
    v = O.parse_validation(VALIDATION_FAIL)
    assert v.judgment == "fail"
    assert "src/api/login.py" in v.fix_files
    assert "lockout" in v.fix_guidance


def test_parse_validation_fail_without_guidance():
    text = VALIDATION_FAIL.replace("## 给 coder 的修复指引（必填）", "## 注释")
    v = O.parse_validation(text)
    assert v.judgment == "schema-error"
    assert any("修复指引" in e for e in v.raw_errors)


def test_parse_validation_loop():
    text = "## 进入死循环风险\n同一处 fail\n\n" + VALIDATION_FAIL
    v = O.parse_validation(text)
    assert v.judgment == "loop"


def test_parse_validation_missing_judgment_heading():
    text = (
        "## 复现命令\n```bash\nx\n```\n\n"
        "## 给 coder 的修复指引\n- 文件: a.py\n\nSTATUS: ok\n"
    )
    v = O.parse_validation(text)
    assert v.judgment == "schema-error"


def test_parse_validation_invalid_verdict_word():
    text = "## 判定\nmaybe\n\n## 复现命令\n```bash\nx\n```\n\nSTATUS: ok\n"
    v = O.parse_validation(text)
    assert v.judgment == "schema-error"


# ---------- parse_outbox dispatch ----------

def test_parse_outbox_dispatch():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        (out / "result.md").write_text(RESULT_OK)
        (out / "review.md").write_text(REVIEW_P0)
        (out / "validation.md").write_text(VALIDATION_PASS)

        c = O.parse_outbox("coder", out)
        assert c["judgment"] == "ok"
        r = O.parse_outbox("reviewer", out)
        assert r["judgment"] == "p0"
        assert r["p0_count"] == 2
        v = O.parse_outbox("validator", out)
        assert v["judgment"] == "pass"


def test_parse_outbox_missing_file():
    with tempfile.TemporaryDirectory() as td:
        v = O.parse_outbox("coder", Path(td))
        assert v["judgment"] == "schema-error"
        assert any("result.md" in e for e in v["errors"])


# ---------- R10: STATUS must be the strict last non-empty line ----------

def test_status_in_middle_does_not_count():
    text = (
        "## 子任务状态\n"
        "- 1.1 写 a: done — src/a.py\n"
        "\n"
        "STATUS: ok\n"
        "\n"
        "## 关键变更\n"
        "- 后写的内容把 STATUS 推离末尾\n"
    )
    v = O.parse_result(text)
    assert v.judgment == "schema-error"
    assert any("STATUS" in e for e in v.raw_errors)
