import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import publish


def test_index_uses_site_config_and_escapes_titles():
    html = publish.build_index_html(
        {"vid": {"folder": "notes", "title": "A < B", "updated": "2026-01-02T03:04:05", "corrected": False}},
        {"title": "我的书架", "description": "个人笔记", "footer": "更新中", "github_url": ""},
    )
    assert "<title>我的书架</title>" in html
    assert "<p>个人笔记</p>" in html
    assert "A &lt; B" in html
    assert "A < B" not in html
