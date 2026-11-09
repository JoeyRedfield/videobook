# 第六步（可选）：发布成品到 pages 分支

```bash
python src/publish.py <video_id>      # 或 --all：发布 output/ 下所有含 book.html 的视频
git push origin pages
```

- **发布内容**：`book.html`、`book.md`、`images/`，以及（若存在）`transcript.corrected.txt`。
  落地页卡片对含对照稿的书自动附「字幕对照」入口。
- **不发布**：原始字幕、`transcript.json` 等中间物不进公开仓库；`output/` 本地工作区不受任何 git 操作影响（铁律 4）。
- 目录名 = 视频标题（例如 `提示词工程 [02-Raw／26生成式软件工程／NJU]`）。
- 内容无变化时自动跳过提交。纯本地 git 操作，沙箱内可跑；`git push` 需要网络。

## pages 分支

- `pages` 是**独立 orphan 分支**，与 `main` 没有共同历史：`main` = 工具代码，`pages` = 成品。
- 首次推送后需在 GitHub 仓库 **Settings → Pages** 一次性启用：分支选 `pages`、目录选 `/ (root)`；
  之后每次 `git push origin pages` 自动部署。
- 在线阅读地址：`https://<owner>.github.io/videobook/`（落地页列出全部电子书，支持截图放大与 Mermaid 交互）。
- 阅读页自动从二、三级标题生成可跳转目录：正文保持居中，宽屏目录固定在左侧留白中，空间不足时改为顶部折叠目录。更新渲染模板后，需重跑 `post_process.py` 再发布，已有 HTML 才会应用新样式。

## 发布后核验（必须等 Pages 真正部署完）

`git push` 成功 ≠ 发布成功。推送后轮询落地页，直到新书出现，再逐个核对线上文件（实测部署约 1–2 分钟）：

```bash
# 1) 落地页出现新书（书名关键词用转义后的中文或 HTML 里能匹配的片段）
for i in $(seq 1 8); do
  curl -s "https://<owner>.github.io/videobook/" | grep -q "<书名关键词>" && break
  sleep 15
done

# 2) 中文 / 全角标点的书名必须做 URL 编码；最稳的是从 index.html 抓真实 href
#    对 book.html、book.md、transcript.corrected.txt、随机 1–2 张截图逐个确认 HTTP 200，
#    并比对响应体大小与本地文件一致
```

- HTTP 200 不够：用浏览器打开线上 `book.html` 再跑一遍 QA —— 截图卡片数、`img.naturalWidth>0`、
  Mermaid SVG 数、`docWidth == clientWidth`、页脚存在、`pageerror` 为空。
- 落地页卡片和阅读页目录都做一次真实点击，确认中文/全角标点的百分号编码正确、不 404。
- 发布后 `git status` 应保持干净：`publish.py` 的孤儿分支提交不会改动 main 工作区与 `output/`（铁律 4）。

## 注意

- 不要对 `pages` 或 `main` 强推。`main` 上已配置 ruleset（禁止强推与删除分支），
  确实需要重写历史时先临时停用规则，改完立刻恢复。
- 发布前确认 `book.html` 是最新的：改过 `book.md`、跑过 `--apply-map` 或更新过校订稿之后，都要重跑第四步 `post_process.py`。
- 课程讲义 / 幻灯片通常是 CC BY-NC 之类授权，发布到公开页面时书首的出处与许可署名**不能删**（铁律 7）。
