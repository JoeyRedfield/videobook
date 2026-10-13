#!/usr/bin/env python3
"""电子书目录（TOC）生成 / 规范化 / 校验。

问题背景
--------
`book.md` 的「## 目录」是第二步由模型手写的，而 Markdown 有序列表**自己会渲染出编号**。
只要模型把标题原文（标题自带 "1. " / "2.1 " 这类编号）整段抄进链接文字，
页面就会渲染成「1. 1. 回顾与开场……」——编号重复。模型每次写目录都靠运气，所以会反复出现。

因此把目录变成**可机械生成**的东西：
- 链接文字与锚点全部从标题推导，锚点用渲染器同款 markdown toc 扩展计算，保证与 `book.html` 里
  heading 的 `id` 逐字一致；
- 链接文字**剥掉标题自带的编号**（有序列表已经提供了编号），因此不可能再出现重复编号；
- 幂等：目录已正确时重跑不改一个字节。

用法
----
    python src/build_toc.py <book.md> [<book.md> ...]   # 生成 / 修复
    python src/build_toc.py <book.md> --check            # 只校验，退出码 1 表示目录不一致
    python src/build_toc.py <book.md> --depth 3          # 连三级标题一起列（默认只列 ## ）

第四步 `post_process.py` 渲染 HTML 前会自动调用本模块兜底，因此写书时漏了也不会漏到成品里。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import markdown

TOC_HEADING = "## 目录"
TOC_MARKER = "<!--TOC-->"

# 行首编号：1. / 1、/ 1.1 / 1.1. 之类
NUMBERING = re.compile(r"^\s*\d+(?:\.\d+)*\s*[.、]?\s+")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
SEPARATOR = re.compile(r"^---\s*$")


def strip_numbering(title: str) -> str:
    """去掉标题自带的编号，避免与有序列表的编号叠加。"""
    stripped = NUMBERING.sub("", title).strip()
    return stripped or title.strip()


def _headings(text: str, depth: int):
    """按文档顺序取出 (level, 标题原文) ，跳过代码围栏内的伪标题。"""
    out, in_fence, fence_tok = [], False, ""
    for line in text.split("\n"):
        m = FENCE.match(line)
        if m:
            if not in_fence:
                in_fence, fence_tok = True, m.group(1)
            elif line.strip().startswith(fence_tok):
                in_fence = False
            continue
        if in_fence:
            continue
        h = HEADING.match(line)
        if h:
            level = len(h.group(1))
            # 一级标题就是书名，不进目录
            if level == 2 or level == depth:
                out.append((level, h.group(2)))
    return [(lvl, t) for lvl, t in out if not (lvl == 2 and t.strip() == "目录")]


def render_toc(text: str, depth: int = 2) -> str:
    """由标题生成目录 Markdown 块（含「## 目录」标题本身）。

    链接文字取自标题**原文**（保留 `code` 这类内联标记），锚点取自渲染器的 toc id，
    两者全部机械推导，不给手写留下漂移空间。
    """
    md = markdown.Markdown(extensions=["toc"])
    md.convert(text)
    tokens = []

    def walk(nodes):
        for node in nodes:
            tokens.append(node)
            walk(node.get("children") or [])

    walk(md.toc_tokens)
    # 渲染器给出的 (level, name, id) 按文档顺序排列，与 _headings 一一对应
    pairs = [(t["level"], t["name"], t["id"]) for t in tokens
             if t["level"] == 2 or t["level"] == depth]
    pairs = [p for p in pairs if not (p[0] == 2 and p[1].strip() == "目录")]

    headings = _headings(text, depth)
    if len(pairs) != len(headings):
        # 极端情况（标题里有裸 HTML 之类）退化为按顺序最小匹配，宁可不列也不错列
        pairs = pairs[: len(headings)]

    lines = [TOC_HEADING, ""]
    n = 0
    for (level, raw), (_lvl, _name, hid) in zip(headings, pairs):
        text_ = strip_numbering(raw)
        if level == 2:
            n += 1
            lines.append(f"{n}. [{text_}](#{hid})")
        else:
            indent = "    " * (level - 2)
            lines.append(f"{indent}- [{text_}](#{hid})")
    lines.append("")
    return "\n".join(lines)


ENTRY = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+\[(.*?)\]\(#(.*?)\)\s*$")


def _parse_entries(lines) -> list[tuple[int, str, str]]:
    """解析文档中**现有的**目录条目：[(level, 链接文字, 锚点)]，没有目录则空列表。"""
    span = _toc_span(lines)
    if span is None:
        return []
    _, start, end = span
    out = []
    for line in lines[start:end]:
        m = ENTRY.match(line)
        if not m:
            continue
        # 二级用有序列表，三级用项目符号；目录本身只有这两级
        level = 2 if re.match(r"^\s*\d+[.)]\s", line) else 3
        out.append((level, m.group(2), m.group(3)))
    return out


def normalize_for_render(text: str, depth: int = 2) -> tuple[str, str]:
    """渲染前调用：返回 (可直接渲染的文本, 模式)。

    - `noop`：目录已经是对的，一个字不动；
    - `cosmetic`：只是编号重复 / 锚点过期，条目集合没变，或文档用了显式目录占位符 —— 可以安全写回 book.md；
    - `structural`：条目缺失、多出或没有目录 —— 只用于本次渲染，**不**自动改文件，
      由人显式跑 `build_toc.py` 决定。这样重渲染别人已发布的书不会静默改写它。
    """
    new, changed = build_toc(text, depth=depth)
    if not changed:
        return text, "noop"
    span = _toc_span(text.split("\n"))
    # 显式占位符代表作者希望目录由脚本生成，可以物化回 book.md。
    if span is not None and span[0] == "marker":
        return new, "cosmetic"
    old_entries = _parse_entries(text.split("\n"))
    new_entries = _parse_entries(new.split("\n"))
    key = lambda es: [(lvl, strip_numbering(t)) for lvl, t, _ in es]
    return new, ("cosmetic" if old_entries and key(old_entries) == key(new_entries)
                 else "structural")


def _toc_span(lines):
    """返回需要被替换的「目录」区间 [start, end)；没有目录时返回 None。"""
    for i, line in enumerate(lines):
        if line.strip() == TOC_MARKER:
            return ("marker", i, i + 1)
        if line.strip() == TOC_HEADING:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## ") and not SEPARATOR.match(lines[j]):
                j += 1
            # 目录本体后面的空行属于文档结构，不纳入替换区间
            # （否则正确目录首次也会被判定为“变化”，且会吃掉与下文之间的空行）
            while j - 1 > i and lines[j - 1].strip() == "":
                j -= 1
            return ("heading", i, j)
    return None


def _insert_at(lines) -> int:
    """没有目录时插到哪儿：H1 之后的第一个 --- 分隔线之后。"""
    seen_h1 = False
    for i, line in enumerate(lines):
        if HEADING.match(line) and len(HEADING.match(line).group(1)) == 1:
            seen_h1 = True
            continue
        if seen_h1 and SEPARATOR.match(line):
            return i + 1
    return len(lines)


def build_toc(text: str, depth: int = 2) -> tuple[str, bool]:
    """返回 (规范化后的全文, 是否发生变化)。缺失目录时插入，已正确时原样返回。"""
    block = render_toc(text, depth=depth).rstrip("\n")
    lines = text.split("\n")
    span = _toc_span(lines)
    if span is None:
        at = _insert_at(lines)
        new = lines[:at] + ["", block, "", "---"] + lines[at:]
        return "\n".join(new), True
    _, start, end = span
    # 目录块前后的空行/分隔线保持原样，只替换目录本体
    new = lines[:start] + block.split("\n") + lines[end:]
    out = "\n".join(new)
    return out, out != text


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 / 修复 / 校验 book.md 的目录")
    ap.add_argument("book", nargs="+", help="book.md 路径")
    ap.add_argument("--depth", type=int, default=2, choices=[2, 3],
                    help="目录收录到几级标题（默认 2，即只列 ## ）")
    ap.add_argument("--check", action="store_true",
                    help="只校验不写盘；目录不一致时以退出码 1 结束")
    args = ap.parse_args()

    bad = 0
    for path in args.book:
        if not os.path.exists(path):
            print(f"[ERR] 找不到文件: {path}")
            bad += 1
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new, changed = build_toc(text, depth=args.depth)
        if args.check:
            if changed:
                print(f"[FAIL] {path}: 目录与标题不一致（编号重复 / 锚点过期 / 条目缺失）")
                bad += 1
            else:
                print(f"[OK] {path}: 目录一致")
            continue
        if changed:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new)
            print(f">> {path}: 目录已规范化")
        else:
            print(f">> {path}: 目录已是最新，未改动")

    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
