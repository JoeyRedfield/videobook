#!/usr/bin/env python3
"""浏览器 QA 脚本：在真实 Chrome 中验收 output/<video_id>/book.html。

用法（仓库根目录，先起 HTTP 预览）：
    python -m http.server 12345 --directory output/<video_id>
    python skills/videobook/scripts/verify_page.py output/<video_id>
    BOOK_REVIEW_URL=http://127.0.0.1:12345/book.html python skills/videobook/scripts/verify_page.py output/<video_id>
    python skills/videobook/scripts/verify_page.py output/<video_id> --url http://127.0.0.1:12345/book.html --headed

期望值全部从 output/<video_id>/book.md 推导，不硬编码任何一本书的数字：
  * Mermaid 图数量     = ```mermaid 代码围栏数量
  * 截图卡片数量       = SCREENSHOT:HH:MM:SS / shot_HH_MM_SS.png / slide_NNN.png 占位符数量
  * 目录链接数量       = (## 数量 - “目录”标题) + ### 数量（post_process 的 toc_depth 2-3）
  * 图片文件           = images/shot_HH_MM_SS.png，逐个检查是否存在及大小

产物（写到本脚本同目录）：
  qa-desktop.png  qa-mobile.png  qa-lightbox.png  qa-diagram.png
  qa-mobile-toc.png（附加证据）  qa-report.json

退出码：0 = 全部硬性检查通过；1 = 有硬伤（裂图 / Mermaid 未渲染 / iframe 降级 /
横向溢出 / lightbox 或目录失效 / 本页 JS 异常等）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

VIDEO_DIR = Path(".")
ROOT = VIDEO_DIR
MD_PATH = ROOT / "book.md"
DEFAULT_URL = "http://127.0.0.1:12345/book.html"

# 截图占位符：与 src/post_process.py 的 replace_screenshots_with_embeds 保持一致
SCREENSHOT_PLACEHOLDER = re.compile(
    r"!\[([^\]]*)\]\("
    r"(?:SCREENSHOT:(\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"|(?:images/)?shot_(\d{2})_(\d{2})_(\d{2})\.png"
    r"|(?:images/)?slide_(\d{3})\.png"
    r")\)"
)
MERMAID_FENCE = re.compile(r"^\s*```mermaid\s*$", re.M)
HEADING = re.compile(r"^(#{2,3})\s+(.*?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
MERMAID_PREFIX = re.compile(
    r"^(graph|flowchart|sequenceDiagram|gantt|pie|classDiagram|stateDiagram|mindmap)\b"
)


# ─────────────────────────────────────────────
# book.md 侧的期望值
# ─────────────────────────────────────────────

def parse_book_md(text: str) -> dict:
    """从 book.md 推导所有期望值（跳过代码围栏内的伪标题 / 伪占位符）。"""
    headings: list[tuple[int, str]] = []
    in_fence = False
    fence_tok = ""
    for line in text.split("\n"):
        fence = FENCE.match(line)
        if fence:
            if not in_fence:
                in_fence, fence_tok = True, fence.group(1)
            elif line.strip().startswith(fence_tok):
                in_fence = False
            continue
        if in_fence:
            continue
        m = HEADING.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 2 and title == "目录":
                continue  # 目录标题本身不进目录
            headings.append((level, title))

    screenshots = []
    for m in SCREENSHOT_PLACEHOLDER.finditer(text):
        desc = m.group(1).strip()
        if m.group(2):  # SCREENSHOT:HH:MM:SS
            ts = m.group(2).split(".")[0]
            filename = "images/shot_" + ts.replace(":", "_") + ".png"
        elif m.group(6):  # slide_NNN.png（官方课件图，无时间锚点）
            ts = None
            filename = "images/slide_" + m.group(6) + ".png"
        else:  # 已物化的 shot_HH_MM_SS.png
            ts = ":".join(m.group(i) for i in (3, 4, 5))
            filename = "images/shot_" + ts.replace(":", "_") + ".png"
        screenshots.append({"desc": desc, "timestamp": ts, "file": filename})

    title_match = re.search(r"^#\s+(.+)$", text, re.M)
    return {
        "headings": headings,
        "mermaid": len(MERMAID_FENCE.findall(text)),
        "screenshots": screenshots,
        "toc_links": len(headings),
        "toc_texts": [title for _lvl, title in headings],
        "doc_title": title_match.group(1).strip() if title_match else "",
    }


# ─────────────────────────────────────────────
# 颜色 / 对比度
# ─────────────────────────────────────────────

def _parse_rgb(value: str):
    m = re.search(r"rgba?\(([^)]+)\)", value or "")
    if not m:
        return None
    parts = [p for p in re.split(r"[,\s/]+", m.group(1).strip()) if p]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) < 3:
        return None
    return nums[0], nums[1], nums[2]


def _parse_rgba(value: str):
    """解析 rgba(...)，返回 (r, g, b, a)；_parse_rgb 只返回前三个分量。"""
    m = re.search(r"rgba?\(([^)]+)\)", value or "")
    if not m:
        return None
    parts = [p for p in re.split(r"[,\s/]+", m.group(1).strip()) if p]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) < 3:
        return None
    return nums[0], nums[1], nums[2], (nums[3] if len(nums) > 3 else 1.0)


def _blend_rgba(fg, base):
    """把带 alpha 的前景色叠到不透明白底（或任意底色）上。"""
    a = fg[3]
    return tuple(fg[i] * a + base[i] * (1 - a) for i in range(3))


def _relative_luminance(rgb) -> float:
    def channel(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg) -> float:
    lf, lb = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


# ─────────────────────────────────────────────
# 页面内的 JS 片段
# ─────────────────────────────────────────────

JS_IMG_PROBE = r"""
() => {
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return u; } };
  const cards = Array.from(document.querySelectorAll('.screenshot-card img'));
  const imgs = cards.map((img) => {
    const r = img.getBoundingClientRect();
    return {
      src: img.getAttribute('src'),
      abs_src: abs(img.getAttribute('src') || ''),
      alt: img.getAttribute('alt') || '',
      complete: img.complete,
      naturalWidth: img.naturalWidth,
      naturalHeight: img.naturalHeight,
      renderedWidth: Math.round(r.width),
      renderedHeight: Math.round(r.height),
    };
  });
  const plain = Array.from(document.querySelectorAll('.book-content img'))
    .filter((img) => !img.closest('.screenshot-card'))
    .map((img) => ({ src: img.getAttribute('src'), alt: img.getAttribute('alt') || '' }));
  const iframes = Array.from(document.querySelectorAll('.book-content iframe')).map((f) => ({
    src: f.getAttribute('src'), inCard: !!f.closest('.video-card'),
  }));
  return {
    cards: document.querySelectorAll('.screenshot-card').length,
    images: imgs,
    plainImages: plain,
    iframes: iframes,
    youtubeCards: document.querySelectorAll('.youtube-card').length,
    fallbackCards: document.querySelectorAll('.video-card.fallback').length,
    videoCards: document.querySelectorAll('.video-card').length,
    captions: Array.from(document.querySelectorAll('.screenshot-card .video-caption'))
      .map((p) => (p.textContent || '').trim()),
  };
}
"""

JS_DARK_FRAME = r"""
async () => {
  const out = [];
  for (const img of document.querySelectorAll('.screenshot-card img')) {
    const src = img.getAttribute('src');
    if (!img.complete || !img.naturalWidth) { out.push({ src, ok: false }); continue; }
    try {
      if (img.decode) { await img.decode(); }
      const W = 160;
      const H = Math.max(1, Math.round(W * img.naturalHeight / img.naturalWidth));
      const c = document.createElement('canvas');
      c.width = W; c.height = H;
      const ctx = c.getContext('2d');
      ctx.drawImage(img, 0, 0, W, H);
      const d = ctx.getImageData(0, 0, W, H).data;
      let sum = 0, dark = 0;
      const n = W * H;
      for (let i = 0; i < d.length; i += 4) {
        const l = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
        sum += l;
        if (l < 16) dark++;
      }
      out.push({ src, ok: true,
                 mean: Math.round(sum / n * 10) / 10,
                 darkRatio: Math.round(dark / n * 1000) / 1000 });
    } catch (e) {
      out.push({ src, ok: false, error: String(e) });
    }
  }
  return out;
}
"""

JS_CONTRAST = r"""
() => {
  const specs = [
    ['p', '正文段落'],
    ['.video-caption', '截图图注'],
    ['blockquote p', '引用文字'],
    ['h2', 'h2 标题（大字号）'],
    ['h3', 'h3 标题'],
    ['.book-content > p a', '正文链接（顶层段落，普通背景）'],
    ['blockquote a', '引用内链接'],
    ['.book-content code', '行内代码'],
    ['th', '表头'],
    ['td', '表格单元'],
    ['em', '斜体/强调'],
  ];
  function parse(c) {
    const m = c && c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[,\s\/]+/).filter(Boolean).map(Number);
    if (p.length < 3) return null;
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  function effectiveBg(el) {
    const chain = [];
    let n = el;
    while (n && n.nodeType === 1) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) chain.push(c);
      n = n.parentElement;
    }
    let r = 255, g = 255, b = 255;
    for (let i = chain.length - 1; i >= 0; i--) {
      const c = chain[i];
      r = c.r * c.a + r * (1 - c.a);
      g = c.g * c.a + g * (1 - c.a);
      b = c.b * c.a + b * (1 - c.a);
    }
    return { r: Math.round(r), g: Math.round(g), b: Math.round(b) };
  }
  return specs.map(([sel, label]) => {
    const el = document.querySelector(sel);
    if (!el) return { label, selector: sel, missing: true };
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') {
      return { label, selector: sel, missing: true, hidden: true };
    }
    const bg = effectiveBg(el);
    return {
      label, selector: sel,
      text: (el.textContent || '').trim().slice(0, 40),
      color: cs.color,
      bg: `rgb(${bg.r}, ${bg.g}, ${bg.b})`,
      fontSize: parseFloat(cs.fontSize) || 16,
      fontWeight: parseInt(cs.fontWeight, 10) || 400,
    };
  });
}
"""


class QA:
    def __init__(self, url: str, headed: bool):
        self.url = url
        self.headed = headed
        self.report = {
            "url": url,
            "book_md": str(MD_PATH),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "expectations": {},
            "checks": {},
            "failures": [],
            "warnings": [],
            "passed": False,
        }
        self.out_dir = ROOT
        self.console_errors: list[dict] = []
        self.page_errors: list[str] = []
        self.request_failed: list[dict] = []
        self.origin = urlparse(url)

    # ── 记录 ──
    def fail(self, code: str, message: str, evidence=None):
        item = {"code": code, "message": message}
        if evidence is not None:
            item["evidence"] = evidence
        self.report["failures"].append(item)
        print(f"[FAIL] {code}: {message}", file=sys.stderr)

    def warn(self, code: str, message: str, evidence=None):
        item = {"code": code, "message": message}
        if evidence is not None:
            item["evidence"] = evidence
        self.report["warnings"].append(item)
        print(f"[WARN] {code}: {message}", file=sys.stderr)

    def check(self, name: str, fn):
        """执行一个检查段；异常记为硬伤，但不中断其余检查。"""
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - QA 脚本必须把异常变成报告
            self.report["checks"].setdefault(name, {})["error"] = str(exc)
            self.fail(f"{name}_exception", f"{name} 检查抛异常: {exc}",
                      {"traceback": traceback.format_exc()[-2000:]})

    # ── 事件收集 ──
    def attach_listeners(self, page, tag: str):
        def on_pageerror(exc):
            self.page_errors.append(f"[{tag}] {exc}")

        def on_console(msg):
            if msg.type != "error":
                return
            loc = msg.location or {}
            self.console_errors.append({
                "page": tag,
                "text": msg.text,
                "url": loc.get("url", ""),
                "line": loc.get("lineNumber"),
            })

        def on_request_failed(request):
            url = request.url
            parsed = urlparse(url)
            if (parsed.hostname, parsed.port) == (self.origin.hostname, self.origin.port):
                self.request_failed.append({"page": tag, "url": url,
                                            "failure": request.failure})

        page.on("pageerror", on_pageerror)
        page.on("console", on_console)
        page.on("requestfailed", on_request_failed)

    # ── 等待 Mermaid ──
    def wait_mermaid(self, page, expected: int) -> bool:
        if expected == 0:
            return True
        try:
            page.wait_for_function(
                "(n) => document.querySelectorAll('.mermaid svg').length === n",
                arg=expected, timeout=45000,
            )
            return True
        except PlaywrightError:
            return False

    # ── 滚动触发懒加载 ──
    def force_load_images(self, page):
        page.evaluate(
            "() => document.querySelectorAll('.screenshot-card img')"
            ".forEach((i) => { i.loading = 'eager'; })"
        )
        page.evaluate(
            """async () => {
              const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
              const h = document.documentElement.scrollHeight;
              for (let y = 0; y < h; y += 600) {
                window.scrollTo(0, y);
                await sleep(80);
              }
              window.scrollTo(0, h);
              await sleep(300);
              window.scrollTo(0, 0);
              await sleep(200);
            }"""
        )
        try:
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('.screenshot-card img'))"
                ".every((i) => i.complete && i.naturalWidth > 0)",
                timeout=30000,
            )
        except PlaywrightError:
            pass
        page.wait_for_timeout(600)

    def wait_scroll_settled(self, page, timeout: int = 8000):
        """等 CSS smooth-scroll 动画停下来（跨几百行滚动时 900ms 并不够）。"""
        try:
            page.wait_for_function(
                """() => new Promise((resolve) => {
                  let last = null, stable = 0, frames = 0;
                  const tick = () => {
                    frames++;
                    const y = Math.round(window.scrollY);
                    if (last !== null && Math.abs(y - last) < 2) stable++;
                    else stable = 0;
                    last = y;
                    if (stable >= 6 || frames > 900) resolve(true);
                    else requestAnimationFrame(tick);
                  };
                  tick();
                })""",
                timeout=timeout,
            )
        except PlaywrightError:
            pass
        page.wait_for_timeout(120)

    # ─────────────────────────────────────────
    # 各项检查
    # ─────────────────────────────────────────
    def run(self):
        exp = parse_book_md(MD_PATH.read_text(encoding="utf-8"))
        self.expected = exp
        self.report["expectations"] = {
            "doc_title": exp["doc_title"],
            "mermaid": exp["mermaid"],
            "screenshots": len(exp["screenshots"]),
            "toc_links": exp["toc_links"],
            "headings_total_with_toc": len(exp["headings"]) + 1,
        }

        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
        launch_kwargs = {"channel": "chrome", "headless": not self.headed}
        if proxy:
            # 自动化访问 127.0.0.1 必须绕过代理，否则本地页面会被代理吞掉
            launch_kwargs["proxy"] = {"server": proxy, "bypass": "127.0.0.1,localhost,::1"}

        with sync_playwright() as pw:
            browser = pw.chromium.launch(**launch_kwargs)
            try:
                desktop = browser.new_page(
                    viewport={"width": 1600, "height": 1000}, device_scale_factor=1
                )
                self.attach_listeners(desktop, "desktop")
                self.qa_desktop(desktop)
                self.qa_mobile(browser)
            finally:
                browser.close()

        self.finalize()

    # ── 桌面端 ──
    def qa_desktop(self, page):
        loaded = self.qa_load_desktop(page)
        if not loaded:
            return
        self.check("mermaid", lambda: self.qa_mermaid(page))
        self.check("screenshots", lambda: self.qa_screenshots(page))
        self.check("lightbox", lambda: self.qa_lightbox(page))
        self.check("toc", lambda: self.qa_toc(page))
        self.check("contrast", lambda: self.qa_contrast(page))

        # 桌面截图（回到页面顶部；等待字体渲染；目录内部滚动也回到顶部）
        page.evaluate(
            "() => { window.scrollTo({ top: 0, behavior: 'instant' });"
            " const nav = document.querySelector('.book-toc nav'); if (nav) nav.scrollTop = 0; }"
        )
        self.wait_scroll_settled(page)
        try:
            page.evaluate("() => (document.fonts ? document.fonts.ready.then(() => true) : true)")
            page.wait_for_timeout(300)
            page.screenshot(path=str(self.out_dir / "qa-desktop.png"))
            self.report["checks"].setdefault("screenshots_files", {})["desktop"] = "qa-desktop.png"
        except PlaywrightError as exc:
            self.warn("desktop_screenshot_failed", str(exc))

        # 桌面通用检查
        self.check("desktop_layout", lambda: self.qa_desktop_layout(page))

    def qa_load_desktop(self, page) -> bool:
        info = {"ok": False, "status": None, "title": None}
        try:
            response = page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
            info["status"] = response.status if response else None
            info["title"] = page.title()
            info["ok"] = True
        except PlaywrightError as exc:
            self.fail("page_load_failed",
                      f"无法打开 {self.url}：{exc}。若 Lead 的 http.server 尚未启动，"
                      "请先等待其启动后重跑。")
        self.report["checks"]["load"] = info
        if not info["ok"]:
            return False
        if info["status"] and info["status"] != 200:
            self.fail("http_status", f"book.html 返回 HTTP {info['status']}")
        self.force_load_images(page)
        return True

    def qa_mermaid(self, page):
        expected = self.expected["mermaid"]
        rendered = self.wait_mermaid(page, expected)
        data = page.evaluate(
            """() => {
              const svgs = Array.from(document.querySelectorAll('.mermaid svg'));
              const raw = Array.from(document.querySelectorAll('.book-content pre code'))
                .map((c) => (c.textContent || '').trim().slice(0, 60))
                .filter((t) => /^(graph|flowchart|sequenceDiagram|gantt|pie|classDiagram|stateDiagram|mindmap)\\b/.test(t));
              return {
                svgCount: svgs.length,
                mermaidContainers: document.querySelectorAll('.mermaid').length,
                svgs: svgs.map((s) => {
                  const r = s.getBoundingClientRect();
                  return {
                    width: Math.round(r.width),
                    height: Math.round(r.height),
                    nodes: s.querySelectorAll('path,line,rect,circle,ellipse,polygon,text').length,
                    labels: s.querySelectorAll('foreignObject, text').length,
                    labelText: (s.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 50),
                  };
                }),
                leftoverRawCode: raw,
              };
            }"""
        )
        checks = {
            "expected": expected,
            "rendered": data["svgCount"],
            "containers": data["mermaidContainers"],
            "did_timeout": not rendered,
            "leftover_raw_code": data["leftoverRawCode"],
            # 真正空图：尺寸过小 / 没有任何图元 / 没有任何文本标签
            "empty_svgs": [i for i, s in enumerate(data["svgs"])
                           if s["width"] < 60 or s["height"] < 20
                           or s["nodes"] == 0 or s["labels"] == 0],
            # 宽而扁的 LR 图：渲染出来了，但为了塞进 780px 正文会被等比缩小
            "shallow_svgs": [{"index": i, "width": s["width"], "height": s["height"],
                              "labels": s["labels"]}
                             for i, s in enumerate(data["svgs"])
                             if s["width"] >= 60 and s["height"] < 40 and s["labels"] > 0],
            "svgs": data["svgs"],
        }
        checks["passed"] = (
            rendered
            and data["svgCount"] == expected
            and not data["leftoverRawCode"]
            and not checks["empty_svgs"]
        )
        self.report["checks"]["mermaid"] = checks
        if rendered and data["svgCount"] != expected:
            self.fail("mermaid_count",
                      f"Mermaid 渲染 {data['svgCount']} 个，期望 {expected} 个",
                      {"svgs": data["svgs"]})
        if not rendered:
            self.fail("mermaid_not_rendered",
                      f"45s 内未渲染出全部 {expected} 个 .mermaid svg（实际 {data['svgCount']}）",
                      {"containers": data["mermaidContainers"]})
        if data["leftoverRawCode"]:
            self.fail("mermaid_raw_block",
                      f"仍有 {len(data['leftoverRawCode'])} 个 Mermaid 代码块没被渲染成图",
                      {"blocks": data["leftoverRawCode"]})
        if checks["empty_svgs"]:
            self.fail("mermaid_empty_svg",
                      f"{len(checks['empty_svgs'])} 个 Mermaid SVG 尺寸/内容异常",
                      {"indexes": checks["empty_svgs"]})
        if checks["shallow_svgs"]:
            self.warn("mermaid_shallow",
                      f"{len(checks['shallow_svgs'])} 个宽 LR 图被缩放到只有 "
                      f"{min(s['height'] for s in checks['shallow_svgs'])}px 高，节点文字偏小",
                      {"svgs": checks["shallow_svgs"]})

        # 给一张图的证据截图：优先选能完整放进视口、面积最大的图（避免超长图）
        if data["svgCount"]:
            candidates = [
                (i, s) for i, s in enumerate(data["svgs"])
                if s["height"] <= 700 and s["width"] <= 760
            ]
            if candidates:
                idx = max(candidates, key=lambda pair: pair[1]["width"] * pair[1]["height"])[0]
            else:
                idx = min(expected - 1, 4)
            try:
                page.locator(".mermaid").nth(idx).scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                page.screenshot(path=str(self.out_dir / "qa-diagram.png"))
                checks["diagram_screenshot"] = "qa-diagram.png"
                checks["diagram_index"] = idx
            except PlaywrightError as exc:
                self.warn("diagram_screenshot_failed", str(exc))

    def qa_screenshots(self, page):
        expected = len(self.expected["screenshots"])
        data = page.evaluate(JS_IMG_PROBE)
        images = data["images"]
        broken = [i for i in images if (not i["complete"]) or i["naturalWidth"] == 0]
        small = [i for i in images if 0 < i["naturalWidth"] < 320]
        odd_ratio = []
        for i in images:
            if i["naturalWidth"] and i["naturalHeight"]:
                ratio = i["naturalWidth"] / i["naturalHeight"]
                if abs(ratio - 16 / 9) > 0.05:
                    odd_ratio.append({"src": i["src"], "ratio": round(ratio, 3)})

        # 未物化：post_process 会把缺失图片的占位符原样留在正文里 → 浏览器请求 SCREENSHOT:... / shot_*.png
        placeholders = [
            img for img in data["plainImages"]
            if (img["src"] or "").startswith("SCREENSHOT:") or "shot_" in (img["src"] or "")
        ]
        iframes = [f for f in data["iframes"] if f["inCard"]]

        # 与 book.md 的图注逐一对齐（顺序检查）
        exp_descs = [s["desc"] for s in self.expected["screenshots"]]
        caption_mismatch = []
        if len(data["captions"]) == len(exp_descs):
            for idx, (want, got) in enumerate(zip(exp_descs, data["captions"])):
                if want not in got:
                    caption_mismatch.append({"index": idx, "expected": want, "actual": got[:120]})
        else:
            caption_mismatch = [{"error": "caption 数量与占位符数量不一致",
                                 "captions": len(data["captions"]), "placeholders": len(exp_descs)}]

        dark = page.evaluate(JS_DARK_FRAME)
        dark_suspects = [
            d for d in dark
            if d.get("ok") and (d.get("mean", 255) < 12 or d.get("darkRatio", 0) > 0.95)
        ]

        checks = {
            "frame_luminance": dark,
            "expected_cards": expected,
            "cards": data["cards"],
            "images": len(images),
            "video_cards": data["videoCards"],
            "youtube_cards": data["youtubeCards"],
            "fallback_cards": data["fallbackCards"],
            "broken": broken,
            "not_materialized_placeholders": placeholders,
            "iframe_in_cards": iframes,
            "small_images": small,
            "odd_aspect_ratio": odd_ratio,
            "dark_frame_suspects": dark_suspects,
            "caption_mismatch": caption_mismatch,
            "image_sizes": [
                {"src": i["src"], "w": i["naturalWidth"], "h": i["naturalHeight"]}
                for i in images
            ],
        }
        checks["passed"] = (
            len(images) == expected
            and not broken
            and not placeholders
            and not iframes
            and not caption_mismatch
        )
        self.report["checks"]["screenshots"] = checks

        if len(images) != expected:
            self.fail("screenshots_count",
                      f"截图卡片 {len(images)} 张，期望 {expected} 张"
                      f"（video-card 总数 {data['videoCards']}，iframe 降级 {len(iframes)}）",
                      {"sizes": checks["image_sizes"]})
        if broken:
            self.fail("screenshots_broken",
                      f"{len(broken)} 张截图未加载/裂图（naturalWidth=0）",
                      {"broken": broken})
        if placeholders:
            self.fail("screenshots_not_materialized",
                      f"{len(placeholders)} 个占位符没物化成 <img>（帧可能没截到）",
                      {"placeholders": placeholders})
        if iframes:
            self.fail("iframe_degradation",
                      f"{len(iframes)} 个截图卡片降级成了 iframe（应为 <img>）",
                      {"iframes": iframes})
        if data["youtubeCards"] or data["fallbackCards"]:
            self.fail("video_card_fallback",
                      f"出现 YouTube iframe 卡片 {data['youtubeCards']} / fallback 卡片 {data['fallbackCards']}",
                      {"youtube": data["youtubeCards"], "fallback": data["fallbackCards"]})
        if caption_mismatch:
            self.fail("screenshot_caption_mismatch",
                      f"{len(caption_mismatch)} 条图注与 book.md 不一致", {"mismatch": caption_mismatch})
        if small:
            self.warn("screenshots_small",
                      f"{len(small)} 张截图自然宽度 < 320px", {"small": small})
        if odd_ratio:
            self.warn("screenshots_aspect_ratio",
                      f"{len(odd_ratio)} 张截图不是 16:9", {"odd": odd_ratio})
        if dark_suspects:
            self.warn("screenshots_dark",
                      f"{len(dark_suspects)} 张截图疑似黑帧/全黑", {"dark": dark_suspects})

        # 独立核对磁盘上的图片文件
        self.check("image_files", lambda: self.qa_image_files())

    def qa_image_files(self):
        missing, too_small, listed = [], [], []
        for s in self.expected["screenshots"]:
            path = ROOT / s["file"]
            if not path.exists():
                missing.append(s["file"])
                continue
            size = path.stat().st_size
            listed.append({"file": s["file"], "bytes": size})
            if size < 20_000:
                too_small.append({"file": s["file"], "bytes": size})
        checks = {"listed": listed, "missing": missing, "too_small": too_small,
                  "passed": not missing}
        self.report["checks"]["image_files"] = checks
        if missing:
            self.fail("image_files_missing",
                      f"{len(missing)} 个截图文件不存在", {"missing": missing})
        if too_small:
            self.warn("image_files_too_small",
                      f"{len(too_small)} 个截图文件 < 20KB，疑似低清/占位图", {"files": too_small})

    def qa_lightbox(self, page):
        imgs = page.locator(".screenshot-card img")
        count = imgs.count()
        result = {"found": count > 0, "opens": False, "escape_closes": False,
                  "backdrop_closes": False, "img_loaded": False, "src_matches": False,
                  "caption": None}
        self.report["checks"]["lightbox"] = result
        if count == 0:
            self.fail("lightbox_no_image", "页面没有截图可点，无法验证 lightbox")
            return
        idx = min(max(count // 2, 0), count - 1)
        result["index"] = idx
        target = imgs.nth(idx)
        target_src = target.get_attribute("src")
        try:
            target.scroll_into_view_if_needed()
            page.wait_for_timeout(250)
            target.click()
        except PlaywrightError:
            target.click(force=True)
        try:
            page.wait_for_selector(".lightbox.open", state="visible", timeout=5000)
            result["opens"] = True
        except PlaywrightError:
            self.fail("lightbox_open_failed", "点击截图后 .lightbox.open 没有出现")

        if result["opens"]:
            try:
                page.wait_for_function(
                    "() => { const i = document.querySelector('.lightbox.open img');"
                    " return i && i.complete && i.naturalWidth > 0; }",
                    timeout=8000,
                )
                result["img_loaded"] = True
            except PlaywrightError:
                self.fail("lightbox_img_broken", "lightbox 内的图片没有加载成功")
            lb_src = page.locator(".lightbox.open img").get_attribute("src")
            result["src_matches"] = bool(
                lb_src and target_src
                and (lb_src == target_src or lb_src.endswith("/" + target_src.lstrip("./")))
            )
            result["caption"] = page.locator(".lightbox.open .lightbox-caption").inner_text()
            cap_info = page.evaluate(
                """() => {
                  const lb = document.querySelector('.lightbox.open');
                  const cap = lb && lb.querySelector('.lightbox-caption');
                  if (!cap) return null;
                  return {
                    color: getComputedStyle(cap).color,
                    fontSize: parseFloat(getComputedStyle(cap).fontSize) || 13.6,
                    overlay_bg: getComputedStyle(lb).backgroundColor,
                  };
                }"""
            )
            if cap_info:
                fg = _parse_rgba(cap_info["color"])
                bg = _parse_rgba(cap_info["overlay_bg"])
                if fg and bg:
                    # 遮罩下面内容颜色不定，按最亮/最暗两种底色算区间，取较差值
                    over_light = _blend_rgba(bg, (255, 255, 255))
                    over_dark = _blend_rgba(bg, (16, 16, 19))
                    r_light = round(contrast_ratio(fg[:3], over_light), 2)
                    r_dark = round(contrast_ratio(fg[:3], over_dark), 2)
                    worst = min(r_light, r_dark)
                    result["caption_contrast"] = {
                        "color": cap_info["color"],
                        "overlay_bg": cap_info["overlay_bg"],
                        "fontSize": cap_info["fontSize"],
                        "ratio_over_white_content": r_light,
                        "ratio_over_dark_content": r_dark,
                        "ratio_min": worst,
                        "wcag_aa_min": 4.5,
                    }
                    if worst < 4.5:
                        self.warn("lightbox_caption_contrast",
                                  f"lightbox 图注在深色遮罩上对比度约 {worst}:1 < 4.5:1（模板 CSS，非本书内容问题）",
                                  result["caption_contrast"])
            if not result["src_matches"]:
                self.warn("lightbox_src_mismatch",
                          "lightbox 图片 src 与原图不一致",
                          {"lightbox": lb_src, "card": target_src})
            try:
                page.screenshot(path=str(self.out_dir / "qa-lightbox.png"))
                result["screenshot"] = "qa-lightbox.png"
            except PlaywrightError as exc:
                self.warn("lightbox_screenshot_failed", str(exc))

            page.keyboard.press("Escape")
            try:
                page.wait_for_function(
                    "() => !document.querySelector('.lightbox.open')", timeout=5000
                )
                result["escape_closes"] = True
            except PlaywrightError:
                self.fail("lightbox_escape_failed", "Esc 没有关闭 lightbox")

            # 重新打开并点击空白处关闭
            try:
                target.click(force=True)
                page.wait_for_selector(".lightbox.open", state="visible", timeout=5000)
                page.mouse.click(6, 6)
                page.wait_for_function(
                    "() => !document.querySelector('.lightbox.open')", timeout=5000
                )
                result["backdrop_closes"] = True
            except PlaywrightError:
                self.fail("lightbox_backdrop_failed", "点击空白处没有关闭 lightbox")

        result["passed"] = (
            result["opens"] and result["escape_closes"] and result["backdrop_closes"]
            and result["img_loaded"]
        )

    def qa_toc(self, page):
        expected = self.expected["toc_links"]
        aside = page.locator(".book-toc")
        visible = aside.is_visible()
        link_count = page.locator(".book-toc a").count()
        details_open = page.locator(".book-toc details").get_attribute("open") is not None
        anchor_info = page.evaluate(
            """() => Array.from(document.querySelectorAll('.book-toc a')).map((a) => {
              const href = a.getAttribute('href') || '';
              let exists = false;
              try { exists = href.startsWith('#') && !!document.getElementById(decodeURIComponent(href.slice(1))); }
              catch (e) { exists = false; }
              return { href, text: (a.textContent || '').trim(), exists };
            })"""
        )
        dom_texts = [a["text"] for a in anchor_info]
        missing_anchors = [a for a in anchor_info if not a["exists"]]

        # 目录文字与 book.md 标题（含标题自带编号，post_process 用 renderer.toc，text 即标题原文）
        md_texts = self.expected["toc_texts"]
        text_mismatch = []
        if len(dom_texts) == len(md_texts):
            for i, (want, got) in enumerate(zip(md_texts, dom_texts)):
                if want != got:
                    text_mismatch.append({"index": i, "expected": want, "actual": got})
        else:
            text_mismatch = [{"error": "数量不同", "md": len(md_texts), "dom": len(dom_texts)}]

        nav_checks = []
        if link_count:
            probe = sorted({0, link_count // 2, link_count - 1})
            for i in probe:
                href = page.locator(".book-toc a").nth(i).get_attribute("href")
                try:
                    page.locator(".book-toc a").nth(i).click()
                    page.wait_for_function(
                        "(h) => location.hash === h", arg=href, timeout=5000
                    )
                    self.wait_scroll_settled(page)  # 等平滑滚动结束
                    pos = page.evaluate(
                        """(h) => {
                          const el = document.getElementById(decodeURIComponent(h.slice(1)));
                          if (!el) return null;
                          const r = el.getBoundingClientRect();
                          return {
                            top: Math.round(r.top),
                            scrollY: Math.round(window.scrollY),
                            atBottom: window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 4,
                          };
                        }""",
                        href,
                    )
                    ok = bool(pos) and (abs(pos["top"]) < 200 or pos["atBottom"])
                    nav_checks.append({"index": i, "href": href, "pos": pos, "ok": ok})
                    if not ok:
                        self.fail("toc_nav_wrong",
                                  f"点击目录第 {i} 项后目标标题不在视口顶部",
                                  {"href": href, "pos": pos})
                except PlaywrightError as exc:
                    nav_checks.append({"index": i, "href": href, "ok": False, "error": str(exc)})
                    self.fail("toc_nav_failed", f"点击目录第 {i} 项失败: {exc}")

        checks = {
            "visible": visible,
            "link_count": link_count,
            "expected_links": expected,
            "details_open_on_desktop": details_open,
            "missing_anchors": missing_anchors,
            "text_mismatch": text_mismatch,
            "nav_checks": nav_checks,
        }
        checks["passed"] = (
            visible and link_count == expected and not missing_anchors and not text_mismatch
            and all(c.get("ok") for c in nav_checks)
        )
        self.report["checks"]["toc"] = checks
        if not visible:
            self.fail("toc_not_visible", ".book-toc 不可见")
        if link_count != expected:
            self.fail("toc_count",
                      f"目录链接 {link_count} 个，期望 {expected} 个"
                      f"（## 与 ### 标题数，不含“目录”自身）")
        if missing_anchors:
            self.fail("toc_anchor_missing",
                      f"{len(missing_anchors)} 个目录锚点在页面里找不到对应标题",
                      {"anchors": missing_anchors})
        if text_mismatch:
            self.fail("toc_text_mismatch",
                      f"{len(text_mismatch)} 条目录文字与 book.md 标题不一致",
                      {"mismatch": text_mismatch[:10]})

    def qa_contrast(self, page):
        raw = page.evaluate(JS_CONTRAST)
        rows = []
        for item in raw:
            if item.get("missing"):
                rows.append({**item, "ratio": None, "passed": None, "threshold": None})
                continue
            fg = _parse_rgb(item["color"])
            bg = _parse_rgb(item["bg"])
            if not fg or not bg:
                rows.append({**item, "ratio": None, "passed": None, "threshold": None})
                continue
            ratio = round(contrast_ratio(fg, bg), 2)
            large = item["fontSize"] >= 24 or (
                item["fontSize"] >= 18.66 and item["fontWeight"] >= 700
            )
            threshold = 3.0 if large else 4.5
            passed = ratio >= threshold
            rows.append({**item, "ratio": ratio, "large": large,
                         "threshold": threshold, "passed": passed})
            if not passed:
                if ratio < 3.0:
                    self.fail("contrast_low",
                              f"{item['label']}（{item['selector']}）对比度 {ratio}:1 < {threshold}:1",
                              {"item": item, "ratio": ratio})
                else:
                    self.warn("contrast_marginal",
                              f"{item['label']}（{item['selector']}）对比度 {ratio}:1 < {threshold}:1",
                              {"item": item, "ratio": ratio})
        checks = {"rows": rows,
                  "passed": all(r["passed"] is not False for r in rows)}
        self.report["checks"]["contrast"] = checks

    def qa_desktop_layout(self, page):
        metrics = page.evaluate(
            """() => ({
              scrollWidth: document.documentElement.scrollWidth,
              innerWidth: window.innerWidth,
              bodyScrollWidth: document.body.scrollWidth,
            })"""
        )
        overflow = metrics["scrollWidth"] > metrics["innerWidth"] + 1
        checks = {**metrics, "horizontal_overflow": overflow, "passed": not overflow}
        self.report["checks"]["desktop_layout"] = checks
        if overflow:
            self.fail("desktop_overflow",
                      f"桌面端横向溢出：scrollWidth {metrics['scrollWidth']} > innerWidth "
                      f"{metrics['innerWidth']}",
                      metrics)

    def qa_console(self, tag: str):
        benign, fatal, third_party = [], [], []
        origin = (self.origin.hostname, self.origin.port)

        def is_local(url: str) -> bool:
            try:
                p = urlparse(url)
                return (p.hostname, p.port) == origin
            except Exception:  # noqa: BLE001
                return False

        for err in self.console_errors:
            if err["page"] != tag:
                continue
            url = err.get("url") or ""
            if "favicon.ico" in url or "favicon.ico" in err.get("text", ""):
                benign.append(err)
            elif is_local(url) or "book.html" in url:
                fatal.append(err)
            else:
                third_party.append(err)
        local_request_failures = [
            r for r in self.request_failed
            if r["page"] == tag and "favicon.ico" not in r["url"]
        ]
        page_errors = [e for e in self.page_errors if e.startswith(f"[{tag}]")]
        checks = {
            "page_errors": page_errors,
            "console_errors_local": fatal,
            "console_errors_third_party": third_party,
            "console_errors_benign": benign,
            "request_failed_local": local_request_failures,
            "passed": not page_errors and not fatal and not local_request_failures,
        }
        self.report["checks"][f"console_{tag}"] = checks
        if page_errors:
            self.fail("js_pageerror", f"页面 JS 异常 {len(page_errors)} 条",
                      {"errors": page_errors})
        if fatal:
            self.fail("console_error", f"本页控制台报错 {len(fatal)} 条", {"errors": fatal})
        if local_request_failures:
            self.fail("request_failed",
                      f"本页资源请求失败 {len(local_request_failures)} 条",
                      {"failures": local_request_failures})
        if third_party:
            self.warn("console_third_party",
                      f"第三方资源控制台报错 {len(third_party)} 条（不影响正文，但记录）",
                      {"errors": third_party})

    # ── 移动端 ──
    def qa_mobile(self, browser):
        page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        self.attach_listeners(page, "mobile")
        try:
            page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            self.fail("mobile_load_failed", f"手机视口打开页面失败: {exc}")
            page.close()
            return
        self.force_load_images(page)
        self.wait_mermaid(page, self.expected["mermaid"])
        self.check("mobile", lambda: self.qa_mobile_checks(page))
        page.close()

    def qa_mobile_checks(self, page):
        def metrics():
            return page.evaluate(
                """() => ({
                  scrollWidth: document.documentElement.scrollWidth,
                  innerWidth: window.innerWidth,
                })"""
            )

        initial = metrics()
        overflow_initial = initial["scrollWidth"] > initial["innerWidth"] + 1
        details_open = page.locator(".book-toc details").get_attribute("open") is not None

        # TOC 收起状态截图
        page.evaluate("() => window.scrollTo({ top: 0, behavior: 'instant' })")
        page.wait_for_timeout(300)
        try:
            page.screenshot(path=str(self.out_dir / "qa-mobile.png"))
        except PlaywrightError as exc:
            self.warn("mobile_screenshot_failed", str(exc))

        # 展开目录
        nav_visible = False
        overflow_open = None
        try:
            page.locator(".book-toc summary").click()
            page.wait_for_timeout(300)
            nav_visible = page.locator(".book-toc nav").is_visible()
            open_metrics = metrics()
            overflow_open = open_metrics["scrollWidth"] > open_metrics["innerWidth"] + 1
            page.screenshot(path=str(self.out_dir / "qa-mobile-toc.png"))
        except PlaywrightError as exc:
            self.warn("mobile_toc_open_failed", str(exc))

        # 点目录链接：应跳转并自动收起目录
        link_clicked = False
        hash_after = None
        details_open_after = None
        try:
            page.locator(".book-toc a").nth(1).click()
            self.wait_scroll_settled(page)
            hash_after = page.evaluate("() => location.hash")
            details_open_after = page.locator(".book-toc details").get_attribute("open") is not None
            link_clicked = bool(hash_after)
        except PlaywrightError as exc:
            self.warn("mobile_toc_link_failed", str(exc))

        # 点完链接、滚到章节后再检查横向溢出
        after = metrics()
        overflow_after = after["scrollWidth"] > after["innerWidth"] + 1

        # 卡片宽度不得超出视口（页面整体溢出检测的补充）
        wide_cards = page.evaluate(
            """() => Array.from(document.querySelectorAll('.screenshot-card, .mermaid, pre'))
              .map((el) => {
                const r = el.getBoundingClientRect();
                return { tag: el.tagName + '.' + (el.className || ''), left: Math.round(r.left),
                         right: Math.round(r.right), width: Math.round(r.width) };
              })
              .filter((x) => x.right > window.innerWidth + 1 || x.left < -1)"""
        )

        checks = {
            "viewport": {"width": 390, "height": 844},
            "scrollWidth": initial["scrollWidth"],
            "innerWidth": initial["innerWidth"],
            "horizontal_overflow": overflow_initial,
            "toc_default_collapsed": not details_open,
            "toc_nav_visible_after_summary_click": nav_visible,
            "horizontal_overflow_toc_open": overflow_open,
            "toc_link_clicked": link_clicked,
            "hash_after_click": hash_after,
            "toc_auto_closed_after_link": (not details_open_after) if details_open_after is not None else None,
            "horizontal_overflow_after_scroll": overflow_after,
            "wide_cards": wide_cards,
        }
        checks["passed"] = (
            not overflow_initial
            and not overflow_open
            and not overflow_after
            and not details_open
            and nav_visible
            and link_clicked
            and checks["toc_auto_closed_after_link"] is not False
            and not wide_cards
        )
        self.report["checks"]["mobile"] = checks

        if overflow_initial:
            self.fail("mobile_overflow",
                      f"手机端横向溢出：scrollWidth {initial['scrollWidth']} > innerWidth "
                      f"{initial['innerWidth']}", initial)
        if details_open:
            self.fail("mobile_toc_not_collapsed",
                      "手机端目录默认处于展开状态（details 带 open）",
                      {"details_open": True})
        if not nav_visible:
            self.fail("mobile_toc_toggle_failed", "点击 summary 后目录没有展开")
        if link_clicked:
            if details_open_after:
                self.fail("mobile_toc_not_auto_closed",
                          "点击目录链接跳转后目录没有自动收起")
        if wide_cards:
            self.warn("mobile_wide_cards",
                      f"{len(wide_cards)} 个元素超出手机视口（含内部可滚动元素）",
                      {"elements": wide_cards})

    # ── 汇总 ──
    def finalize(self):
        # 控制台/JS 异常在所有交互结束后统一判定（页面关闭后事件列表仍在）
        try:
            self.qa_console("desktop")
        except Exception as exc:  # noqa: BLE001
            self.fail("console_desktop_exception", str(exc))
        try:
            self.qa_console("mobile")
        except Exception as exc:  # noqa: BLE001
            self.fail("console_mobile_exception", str(exc))

        self.report["passed"] = not self.report["failures"]
        out = self.out_dir / "qa-report.json"
        out.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")

        summary = {
            "passed": self.report["passed"],
            "expectations": self.report["expectations"],
            "failed_checks": [f["code"] for f in self.report["failures"]],
            "warnings": [w["code"] for w in self.report["warnings"]],
            "artifacts": sorted(p.name for p in self.out_dir.glob("qa-*.png")) + ["qa-report.json"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not self.report["passed"]:
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="book.html 浏览器 QA（Playwright + Chrome）")
    parser.add_argument("video_dir", nargs="?", default=".",
                        help="output/<video_id> 目录，需含 book.md 与 images/；默认当前目录")
    parser.add_argument("--url", default=os.environ.get("BOOK_REVIEW_URL", DEFAULT_URL),
                        help=f"book.html 地址，默认 {DEFAULT_URL}")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（调试用）")
    args = parser.parse_args()
    global ROOT, MD_PATH
    ROOT = Path(args.video_dir).resolve()
    MD_PATH = ROOT / "book.md"
    if not MD_PATH.exists():
        print(f"[ERR] 找不到 {MD_PATH}（第一个参数应是 output/<video_id>）", file=sys.stderr)
        sys.exit(1)
    QA(args.url, args.headed).run()


if __name__ == "__main__":
    main()
