"""目录（TOC）生成 / 规范化 / 校验层的测试。

回归的是真实事故：模型手写「## 目录」时把标题自带的编号一起抄进链接文字，
而 Markdown 有序列表自己也会渲染编号，页面于是出现「1. 1. 回顾与开场……」。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import build_toc as bt

DOC_OK = """# 书名

> 信息块

---

## 目录

1. [回顾与开场](#1)
2. [Agent Loop](#2-agent-loop)

---

## 1. 回顾与开场

正文

## 2. Agent Loop

正文
"""

DOC_DUP = DOC_OK.replace(
    "1. [回顾与开场](#1)\n2. [Agent Loop](#2-agent-loop)",
    "1. [1. 回顾与开场](#1)\n2. [2. Agent Loop](#2-agent-loop)",
)


def test_strip_numbering():
    assert bt.strip_numbering("1. 回顾与开场") == "回顾与开场"
    assert bt.strip_numbering("2.1 Agent Loop") == "Agent Loop"
    assert bt.strip_numbering("没有编号的标题") == "没有编号的标题"


def test_duplicated_numbering_is_fixed():
    new, changed = bt.build_toc(DOC_DUP)
    assert changed
    assert "1. [回顾与开场](#1)" in new
    assert "1. [1." not in new


def test_normalized_toc_is_idempotent():
    fixed, _ = bt.build_toc(DOC_DUP)
    again, changed = bt.build_toc(fixed)
    assert again == fixed
    assert changed is False


def test_anchors_match_renderer_ids():
    """目录锚点必须与渲染器（markdown toc 扩展）生成的 heading id 一致。"""
    import markdown

    text, _ = bt.build_toc(DOC_DUP)
    html = markdown.Markdown(extensions=["toc"]).convert(text)
    # 目录里的 href 与 heading 的 id 成对出现，说明锚点算对了
    assert 'href="#1"' in html and 'id="1"' in html
    assert 'href="#2-agent-loop"' in html and 'id="2-agent-loop"' in html


def test_missing_toc_is_inserted_and_not_listing_h1():
    text = "# 书名\n\n> 信息块\n\n---\n\n## 1. 第一章\n\n正文\n"
    new, changed = bt.build_toc(text)
    assert changed and "## 目录" in new and "1. [第一章](#1)" in new
    # 书名（一级标题）不能进目录
    assert "[书名]" not in new


def test_h2_inside_code_fence_is_ignored():
    text = ("# 书名\n\n## 目录\n\n1. [真实章节](#1)\n\n---\n\n"
            "## 1. 真实章节\n\n```md\n## 这行在代码块里，不是标题\n```\n")
    new, changed = bt.build_toc(text)
    # 代码围栏里的伪标题不得进目录：只看目录块本身
    toc_block = new.split("## 目录")[1].split("---")[0]
    assert "代码块里" not in toc_block
    assert "真实章节" in toc_block
    assert changed is False


def test_check_mode_exit_code(tmp_path, capsys):
    p = tmp_path / "book.md"
    p.write_text(DOC_DUP, encoding="utf-8")
    sys.argv = ["build_toc.py", str(p), "--check"]
    try:
        bt.main()
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("目录不一致时 --check 应当以退出码 1 结束")

    p.write_text(bt.build_toc(DOC_DUP)[0], encoding="utf-8")
    sys.argv = ["build_toc.py", str(p), "--check"]
    bt.main()  # 一致时不应抛 SystemExit