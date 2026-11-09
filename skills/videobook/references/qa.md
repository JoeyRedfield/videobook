# 第 4b 步：浏览器 QA（book.html）

渲染并起 HTTP 预览后，在真实 Chrome 里做一遍可重复的验收。脚本在 `scripts/verify_page.py`，
期望值全部从 `output/<video_id>/book.md` 推导（Mermaid 数、截图数、目录链接数、图片文件），不写死数字。

## 用法

```bash
# 1) 先起预览（换书时先停旧进程，见 troubleshooting.md「预览端口 12345 打开的是上一本书」）
python -m http.server 12345 --directory output/<video_id>

# 2) 跑 QA
python skills/videobook/scripts/verify_page.py output/<video_id>

# 端口不同 / 需要调试时
BOOK_REVIEW_URL=http://127.0.0.1:8080/book.html python skills/videobook/scripts/verify_page.py output/<video_id>
python skills/videobook/scripts/verify_page.py output/<video_id> --headed
```

- 依赖 Playwright + 本机 Chrome（`channel="chrome"`）。脚本检测到 `HTTPS_PROXY / HTTP_PROXY / ALL_PROXY`
  时，会给 `127.0.0.1,localhost,::1` 加 bypass，避免本地页面被代理吞掉。
- 产物写在 `output/<video_id>/`：`qa-desktop.png`、`qa-mobile.png`、`qa-mobile-toc.png`、
  `qa-lightbox.png`、`qa-diagram.png`、`qa-report.json`。
- 退出码：0 = 硬性检查通过；1 = 有硬伤（裂图 / Mermaid 未渲染 / iframe 降级 / 横向溢出 /
  lightbox 或目录失效 / 本页 JS 异常）。

## 覆盖项

- 0 pageerror、本页 console error、本页资源 4xx/5xx（第三方 CDN 报错只记录不判失败）；
- Mermaid 全部渲染成 `.mermaid svg`，无残留代码块、无空图；**宽扁 LR 图**单独告警（见 `writing.md`）；
- 截图卡片数 = `book.md` 占位符数，全部 `naturalWidth>0`，无裂图、无 iframe 降级、无未物化占位符，
  图注与 `book.md` 逐条一致；
- 截图非黑帧（canvas 亮度统计）、宽高比异常告警；
- lightbox 打开 / 图片加载 / src 一致 / Esc 关闭 / 点空白关闭；
- 目录可见、链接数 = `##` + `###`、锚点全部存在、抽 3 个跳转确认标题贴顶；
- 桌面 1600×1000 与手机 390×844 无横向溢出；手机目录默认折叠、点开、点链接自动收起；
- WCAG AA 对比度（页面内取 computed color 与合成背景）。

## 已知非本书告警（不要当硬伤）

- `lightbox_caption_contrast`：lightbox 图注压在深色遮罩上约 2.75:1，是模板 CSS 的既有问题，所有书一致；
- `contrast_marginal`：书首淡蓝 tinted blockquote 内链接约 4.33:1，同为模板问题；
- 这两条记录到报告即可，别改 `book.md` 或单本书的样式；要修就统一改 `src/post_process.py` 的模板
  （需跑 `pytest` + 全量重渲染再发布）。

## 脚本之外还要看一眼

1. 抽查一张幻灯片帧和一张演示/口播帧：清晰度、是否有播放器 UI、图注与画面是否一致；
2. 打开 `qa-desktop.png`、`qa-mobile.png`、`qa-lightbox.png`、`qa-diagram.png` 各看一遍；
3. 在真实浏览器里随机点 2–3 个目录项与 2 张截图，确认交互成立；
4. 若有截图与图注不符：改 `book.md` 的图注后重跑 `post_process.py` 即可，不需要重截图片。

## 脚本抓不到、必须人工补的两项

- **字面 `**`**：加粗在中文边界会失效（见 `writing.md`）。交付前 `grep -o '\*\*' book.html` 应为空（CSS 的 `*` 不算）；有就是漏渲染。
- **图注 vs 画面**：`verify_page.py` 只比对 alt 与渲染后的图注文本，**不判断图注是否符合画面**。用缩略图总览复核全部截图，再挑 5–8 张（尤其图表、标题卡）原尺寸看轴标题/单位；发现不符改 `book.md` 图注或换帧，重跑 `post_process.py`。
