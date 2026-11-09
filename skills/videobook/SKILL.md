---
name: videobook
description: 用 videobook 流水线把一个 YouTube / Bilibili 视频（尤其是课程录像）做成图文并茂的 Markdown + HTML 技术电子书：提取字幕（平台无字幕时用本地 faster-whisper ASR 兜底）、由大模型改写并插入 SCREENSHOT/SLIDE 占位、用已登录浏览器直接截取播放器高画质帧、渲染暗色主题 HTML 并起本地预览、可选抓取课程官方讲义与 4K 幻灯片增强、生成字幕校订对照稿、最后发布到 GitHub Pages 的 pages 分支。当用户给出视频链接或 BV 号并要求做成电子书/教程/图文/文档、要字幕纠错对照稿、要给文章配视频截图、或要发布已生成的 output 时使用。不负责单纯下载视频或字幕、视频剪辑，以及与本流水线无关的网页爬取。
metadata:
  short-description: 视频转图文电子书流水线
---

# VideoBook：把一个视频做成一本图文电子书

用户给一个 YouTube / Bilibili 链接，产出一本可在线阅读的技术电子书：
`output/<video_id>/book.md` + `book.html`（截图卡片 + 官方课件图、点击放大、Mermaid 图），
可选 `transcript.corrected.txt` 字幕校订对照稿，并可发布到 GitHub Pages 供他人阅读。

本 skill 是**编排层**，并且**自包含**：agent 需要的指令全部在本目录内（`SKILL.md` + `references/`），
不依赖仓库里的 `instructions.md` / `README.md`（那两份面向人类读者，保持原样、不由本 skill 维护）。
唯一的外部依赖是流水线的可执行代码 `src/*.py`：命令与参数一律以脚本 `--help` 为准，不要重写脚本逻辑。
易变数据（例如 `src/make_corrected.py` 里的错词 MAP）**不要**复制进 skill，只写规则并指向源文件；
唯一例外是第二步的排版指令，本目录自带同源副本 `references/stitcher-prompt.md`（仓库版本优先）。

## 前置条件（首次运行必读）

1. **工作目录 = videobook 仓库根目录**（判据：存在 `src/dump_transcript.py` 与 `prompts/`），
   所有相对路径（`output/<video_id>/...`）都相对它解析。仓库不在本地时先
   `git clone https://github.com/Luke-Evan/videobook.git` 到用户存放代码项目的目录
   （不要放临时目录，也不要另建 projects 目录），再 `cd` 进去。
2. Python >= 3.12。装依赖：`uv sync`（用 uv 时）或 `pip install -r requirements.txt`。
   执行统一走仓库环境的解释器：`uv run python src/xxx.py` 或 `.venv/Scripts/python.exe src/xxx.py`。
3. **一次性登录**：`python src/capture_frames.py --setup-profile` 弹出专用 Chrome（数据目录 `.capture-profile/`），
   扫码登录 B 站 / YouTube 后关窗即可，登录态长期复用。登录账号的会员档位 = 截图清晰度上限。
   登录态过期时优先按 `references/transcript.md` 的「登录态快速恢复」处理：若本机日常 Chrome 已登录、用户同意复用，
   把 cookies 同步进专用配置即可免扫码（默认仍优先让用户 `--setup-profile`，不要把主 Chrome 数据目录当长期方案）。
4. 海外视频：**终端要代理**（抓字幕），**浏览器也要全局代理**（否则第三步降级出的 YouTube iframe 卡片是一片黑块）。
5. 需要沙箱外/提权的命令见总览表最后一列；纯本地步骤沙箱内即可。
6. **YouTube 截帧先看登录态**：`.capture-profile` 只有 B 站登录态时，YouTube 走匿名 720p 流；脚本又只对 B 站做原生分辨率探针/顶档流锁定，成图通常只有 830–880px 宽（幻灯片文字可读，但不是 1080p）。要更高画质先登录再重跑；交付时如实说明，不要改 viewport、不要改用屏幕截图。详见 `references/capture.md` 的「YouTube 画质 preflight」。
7. **预览端口 12345 先查占用**：上一本书的 `http.server` 可能还占着端口，两个 `book.html` 同名，QA/用户很容易打开错书。起预览前用 `lsof -nP -iTCP:12345 -sTCP:LISTEN` + `ps -p <pid> -o command` 确认服务目录是当前 `output/<video_id>`；换书时先停旧进程再起新服务。交付收尾时停掉自己起的预览进程（managed 环境用 `job_kill`），别留孤儿端口。
8. **YouTube 走 yt-dlp 兜底时先核对字幕语言与时间戳格式**：`youtube-transcript-api` 经本地代理可能报 `SSLEOFError`，脚本回退 yt-dlp；该路径会优先选中文轨（可能是机器翻译），且 `transcript.json` 的 start/end 可能是 VTT 时间串，覆盖率自检会误判成 0% 并退出码 3。不要据此判“无字幕”，按 `references/transcript.md` 第 5 节取英文原字幕重建 transcript。
9. **中文加粗不要贴着后文**：Python-Markdown 在「闭合 `**` 后紧跟汉字、且加粗内容以中文标点结尾」时不识别，页面会露出字面 `**`；写法见 `references/writing.md`。

## 流水线总览

| 步 | 做什么 | 命令 | 细节 | 提权 |
|---|---|---|---|---|
| 1 | 提取字幕 | `python src/dump_transcript.py "<URL>"` | `references/transcript.md` | B 站需 |
| 1b | 平台无字幕时本地 ASR 兜底 | `python src/asr_transcript.py <video_id>` | `references/transcript.md` | 否（耗时长） |
| 1.5 | 课程官方讲义 + 4K 幻灯片（**默认执行**，课程无主页才跳过） | `python src/course_assets.py <video_id> --course-url <课程主页>` | `references/course-assets.md` | 渲染需 |
| 2 | 大模型把字幕改写成 `book.md` | 按排版指令生成（`references/stitcher-prompt.md`，仓库 `prompts/stitcher_system.md` 存在时以它为准） | `references/writing.md` | 否 |
| 2b | 目录占位符 → 真目录 | `python src/build_toc.py output/<id>/book.md`（可 `--check` 校验；第四步渲染前也会自动兜底） | `references/writing.md` | 否 |
| 2c | 字幕校订对照稿（可选） | `python src/make_corrected.py <video_id>` | `references/correction.md` | 否 |
| 3 | 截帧并把占位符物化为图片 | `python src/capture_frames.py <video_id> "<URL>"` | `references/capture.md` | 是 |
| 4 | 渲染 HTML + 起本地预览 | `python src/post_process.py "<URL>" output/<id>/book.md` 然后 `python -m http.server 12345 --directory output/<id>` | 渲染前自动兜底：目录（编号/锛点）+ 文末许可页脚 | 否 |
| 4b | 浏览器 QA（建议默认执行） | 先起 HTTP 预览，再 `python skills/videobook/scripts/verify_page.py output/<id>` | `references/qa.md` | 否 |
| 5 | 告知用户结果、询问大文件清理 | — | 见下方「交付」 | — |
| 6 | 发布成品到 `pages` 分支（可选） | `python src/publish.py <video_id>` 然后 `git push origin pages` | `references/publish.md` | 否（push 需网络） |

报错、画质不对、字幕抓不到、ASR 中断等问题先查 `references/troubleshooting.md`。

## 多代理协作（可选，agent 支持队友时）

写书与最终合稿由 Lead 完成；截帧、字幕校订、浏览器 QA 可以并行交给不同 teammate：

- 顺序：抓字幕 → 同时做「字幕校订 + 截帧 preflight」（校订不依赖 book.md）→ `book.md` 定稿 → 截帧 → 渲染 → QA；
- 写文件范围互不重叠（`book.md` / `images/` / `transcript.corrected.txt` / `qa-*`），在任务板记录 owner 与 write scopes；
- **截帧开始后 Lead 不再改 `book.md` 正文**（物化在最后统一读文件，见铁律 13）；
- 队友报告不能替代 Lead 的最终复核：占位符清零、截图逐张与图注一致、`verify_page.py` 通过、`git status` 干净。

## 铁律（不可协商）

1. **字幕覆盖率 < 50%（退出码 3）不得进入第二步**：先修登录态/代理，或转 ASR 兜底。
2. **截图画面必须来自已登录浏览器里的平台播放器**，第三步任何方式都不得下载视频/音频文件（ffmpeg 抽帧只是最后兜底）。禁用方式清单见 `references/capture.md`。
3. **cookies 只写系统临时目录、用完即删**，绝不落仓库，也不要在工作区手放 `cookies.txt`。
4. **`output/` 不受任何 git 操作影响**；成品只发布到 orphan 分支 `pages`，`main` 只放代码。
5. **校订稿保真**：逐段保留讲师原词与顺序，只做 ASR 错词替换 + 口癖清理；禁删段、禁多行、禁大幅改写（校验层会直接拒绝）。
   生成前后必须按 `references/correction.md` 打印「原始 → 清理后」全量 diff 逐条确认——全局 `MAP` / 叠字正则
   曾把别的视频的 `外部→外包` 词条和「但是+是不是」误伤到当前视频，这类误伤用 `--edits` 在基线上还原，
   不要改全局词表。
6. **写书保真底线**：书面化不等于摘要化；保留类比、案例、推导链；编者补充必须标注「编者说明」。
7. **课程资料版权**：讲义与幻灯片归讲师（常见 CC BY-NC），书首信息块与文末必须保留出处链接、作者署名与许可名。
8. **幻灯片覆盖铁律**：`slides_text.md` 里每一页在书中恰好出现一次，交稿前 `--audit` 必须 PASS。
9. **删除大文件（`audio.m4a`、`video_source.mp4` 等）前必须问用户**，得到确认再删。
10. 生成或修改 HTML 时，文本与背景对比度不低于 WCAG AA 的 4.5:1。
11. 各步幂等可重跑：占位符物化前脚本自动备份 `book.tagged.md`，只要它还在，重跑第三步即可恢复截图。
12. **完整性与浏览器 QA**：默认生成覆盖全片的详细笔记；只有用户明确要求时才生成“提炼核心观点”摘要。`book.md` 定稿后再截帧和物化，交付前按仓库 `docs/quality-checklist.md` 检查实际截图、Mermaid SVG、懒加载图片、目录/lightbox、移动端布局，并在 Pages 更新后核对线上文件。
13. **截帧期间不改 `book.md` 正文**：`capture_frames.py` 在整批截完后才统一读 `book.md` 物化图片。截帧开始后改正文/图注，可能被旧占位符清单或最后一次写入覆盖。正确顺序：`book.md` 定稿 → 截帧 → 逐帧核对画面再改图注 → 重跑 `post_process.py`；需要重截时只删对应 `images/shot_*.png` 再跑。
14. **会议演讲 / 长问答视频的截图按实际画面写图注**：Q&A 时大屏常常停在最后一页，也可能被讲者翻回旧页，截出来的未必是讲者特写。截完逐张看再写/改图注，不要按字幕猜，也不要把同一页幻灯片在多章重复堆图；幻灯片上的精确数字优先于口播约数。

## 交付（第五步要一次性说清）

1. `output/<video_id>/book.md` 的位置；
2. 预览地址 `http://localhost:12345/book.html`，关闭服务在终端按 `Ctrl+C`；
3. YouTube 视频需浏览器能访问 YouTube（代理），B 站视频可直接看；
4. 截图清晰度取决于 `.capture-profile/` 里登录账号的档位；要更清晰就换账号登录后重跑第三步。**YouTube 匿名会话只有 720p 流、成图约 830–880px 宽**（脚本的顶档流锁定目前只对 B 站实现），交付时如实说明，别承诺 1080p（见 `references/capture.md`）；
5. 用了课程官方资料时，说明书中幻灯片插图是官方 4K 渲染图（书首已注明来源与 CC 许可），演示帧仍是视频帧，`course/` 目录可整体删除不影响阅读；
6. 询问是否删除流程中产生的大文件（铁律 9）：媒体文件（`audio.m4a`、`video_source.mp4`），
   以及 `course/_probe_frames/` 这类抽帧中间产物（一次幻灯片定位可能上百 MB）；`images/` 是成品引用，建议保留。

## 本目录内容（按需读取，不要一次性全读）

| 文件 | 什么时候读 |
|---|---|
| `references/transcript.md` | 第一步抓字幕；平台无字幕要转本地 ASR |
| `references/writing.md` | 第二步把字幕改写成 `book.md` |
| `references/stitcher-prompt.md` | 第二步的排版指令原文（仓库 `prompts/stitcher_system.md` 存在时以仓库版本为准） |
| `references/course-assets.md` | 课程有官方主页时的第 1.5 步、覆盖铁律、幻灯片升级 |
| `references/correction.md` | 生成字幕校订对照稿与 AI 行级订正提案 |
| `references/capture.md` | 第三步截帧：优先级、明确禁用的方式、QA 习惯 |
| `scripts/verify_page.py` | 第 4b 步浏览器 QA 脚本；期望值从 `book.md` 推导，不写死数字 |
| `references/qa.md` | 浏览器 QA 用法、覆盖项与已知模板告警 |
| `references/publish.md` | 第六步发布到 `pages` 分支 |
| `references/troubleshooting.md` | 任何报错、画质异常、字幕抓不到、提权被拒 |

## 维护约定

- 本 skill 与流水线代码同仓库版本化：**改了 `src/*.py` 的参数、步骤顺序或铁律，必须同步改本目录**。
- 仓库的 `instructions.md` 与 `README.md` 面向人类读者，保持原样、不由本 skill 维护，
  **不要为了让 skill 生效去改它们**；内容与本目录重叠时，agent 以本目录为准。
- 同步 `references/stitcher-prompt.md` 时，在文件头的来源注记里更新提交号。
- 他人安装：`skill-installer` 用 `--repo Luke-Evan/videobook --path skills/videobook`；
  或 clone 后把本目录复制/软链到 `~/.codex/skills/`（仅 Codex）或 `~/.agents/skills/`（跨 agent）。
- 本目录不含 cookies、登录态、`output/` 产物或任何媒体文件，可安全公开分发。
