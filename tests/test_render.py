import html
import os, sys
import re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import post_process as pp

URL = "https://www.bilibili.com/video/BV1xx/"


def test_bilibili_page_param_in_embed():
    html = pp.make_video_card("bilibili", "BV1xx", URL + "?p=2", "00:01:00", "desc")
    assert "page=2" in html and "t=60" in html


def test_open_url_keeps_query_and_overrides_t():
    html = pp.make_video_card("bilibili", "BV1xx", URL + "?p=3&t=5", "00:02:00", "desc")
    assert "p=3" in html and "t=120" in html and "t=5&" not in html


def test_screenshot_card_keeps_anchor():
    html = pp.make_video_card("bilibili", "BV1xx", URL, "00:00:55", "d",
                              image_rel="images/shot_00_00_55.png")
    assert "images/shot_00_00_55.png" in html and "回到原视频 (00:00:55)" in html


def test_slide_card_without_anchor_has_no_link():
    html = pp.make_video_card("bilibili", "BV1xx", URL, None, "d",
                              image_rel="images/slide_005.png")
    assert "images/slide_005.png" in html and "<a " not in html


def test_replace_screenshots_shot_and_slide(tmp_path):
    img = tmp_path / "images"
    img.mkdir()
    (img / "shot_00_01_00.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (img / "slide_007.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    md = "![a](SCREENSHOT:00:01:00)\n\n![b](images/slide_007.png)\n"
    out = pp.replace_screenshots_with_embeds(md, URL, images_dir=str(img))
    assert "shot_00_01_00.png" in out and "🖼️ b" in out


def test_missing_slide_image_keeps_markdown(tmp_path):
    md = "![b](images/slide_009.png)\n"
    out = pp.replace_screenshots_with_embeds(md, URL, images_dir=str(tmp_path))
    assert out == md


def test_toc_links_match_headings_with_duplicate_and_formatted_titles():
    page = pp.md_to_html(
        '# 书名\n\n## 中文章节\n\n### 使用 `API` & <em>工具</em>\n\n'
        '## 中文章节\n\n```text\n## 代码里的标题\n```\n\n#### 深层标题\n',
        '书名',
    )
    nav = re.search(r'<nav aria-label="章节目录">(.*?)</nav>', page, re.S).group(1)
    targets = re.findall(r'href="#([^"]+)"', nav)
    headings = re.findall(r'<h[23] id="([^"]+)"', page)
    assert len(targets) == len(set(targets)) == 3
    assert [html.unescape(target) for target in targets] == headings
    assert '书名' not in nav and '代码里的标题' not in nav and '深层标题' not in nav
    assert '使用' in nav and 'API' in nav and '工具' in nav


def test_no_toc_without_chapters():
    page = pp.md_to_html('# 只有书名\n\n正文。', '只有书名')
    assert '<aside class="book-toc">' not in page
    assert '<h1 id=' in page and '<p>正文。</p>' in page


def test_manual_toc_uses_generated_heading_anchors():
    page = pp.md_to_html('# 书名\n\n## 目录\n\n1. [章节](#wrong-anchor)\n\n## 中文章节\n', '书名')
    assert '#wrong-anchor' not in page
    assert '<h2 id="_3">中文章节</h2>' in page
    assert page.count('href="#_3"') == 2


def test_quoted_screenshot_card_stays_inside_blockquote(tmp_path):
    (tmp_path / 'shot_00_00_55.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    source = '> ![截图](images/shot_00_00_55.png)\n\n## 后续章节\n'
    page = pp.md_to_html(pp.replace_screenshots_with_embeds(source, URL, str(tmp_path)), '书名')
    quote = re.search(r'<blockquote>(.*?)</blockquote>', page, re.S).group(1)
    assert '<div class="video-card screenshot-card">' in quote and '</div>' in quote
    assert '</blockquote>\n</div>' not in page
    assert '后续章节</h2>' in page


def test_process_markdown_writes_toc(tmp_path):
    book = tmp_path / 'book.md'
    book.write_text('# 书名\n\n## 第一章\n\n正文。', encoding='utf-8')
    pp.process_markdown(URL, str(book))
    page = book.with_suffix('.html').read_text(encoding='utf-8')
    assert '<nav aria-label="章节目录">' in page
    assert '<summary>目录</summary>' in page
    assert '{toc}' not in page
