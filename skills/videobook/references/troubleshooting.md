# 排错速查

## YouTube 字幕抓不到 / 页面里 iframe 报「视频配置错误 (153)」

不是脚本问题，是网络审查与封锁。处理海外视频必须两头都代理：

- **终端走代理**：底层基于第三方库爬取时，才能拿到字幕和源信息。
- **浏览器走全局代理**：生成的 HTML 里 YouTube iframe 是读者本机直连发出的请求；
  读者不挂梯打开这本电子书，依旧是一片黑块裂图。发布前把这点写进交付说明。
- 若该视频**需登录**（会员/年龄限制）而终端仍抓不到字幕：登录态在 pi 浏览器里时用
  `node tools/yt-cookies.mjs` 导出 cookie，再给 `dump_transcript.py` 加 `--cookies-file ~/cookies/yt-cookies.txt`（细节见 `transcript.md`）。

## 为什么必须起 `python -m http.server`，不能双击打开 .html

跨域安全与 Cookie 隐私协议：地址栏是 `file:///...` 的纯静态环境下，带交互控制器的内嵌播放组件会被视频源强制拒载。
必须通过本地 HTTP 服务访问 `http://localhost:12345/book.html`。

## 大会员 / 登录限制视频抓不到

给提取命令挂 cookies 参数，优先用专用配置（先 `--setup-profile` 登录一次）：`--cookies-from-profile <dir>`，
或 `--cookies-file <path>`（Netscape 格式）。**Windows 上不要用 `--cookies-from chrome`**：Chrome 新版
App-Bound 加密使 yt-dlp 无法解密。macOS 上主 Chrome 已登录时可作应急（实测能抓到带登录态的字幕），
但更稳的是把登录态同步进 `.capture-profile`，见 `transcript.md` 的「登录态快速恢复」。
截图清晰度同理 —— 在 `--setup-profile` 窗口登录大会员账号，截帧管线会自动按顶档原生分辨率截取。

## 专用配置无登录态，但本机日常 Chrome 已登录

不要急着让用户扫码：先按 `transcript.md` 的「登录态快速恢复」把主 Chrome 的 B 站 cookies 注入
`.capture-profile`（yt-dlp CLI 导出 → Playwright `add_cookies`），再跑
`dump_transcript.py --cookies-from-profile .capture-profile` 验证 `SESSDATA` 存在。
注入后截帧日志应出现 `native stream size: 1920x1080`；若分辨率仍低，说明 cookies 没生效，回到扫码方案。
cookies 文件只放临时目录、用完即删，不要打印值、不要入库。

## 在 AI 沙箱里报「拒绝访问」

启动 Chrome / Playwright、读取浏览器 cookie 库的命令属于沙箱外权限，需申请提权（Codex 中即 require_escalated）：
`capture_frames.py <video_id> <url>`、`capture_frames.py --setup-profile`、
`dump_transcript.py <url>`（B 站需登录态时会起无头 Chrome 导出 cookies）、`course_assets.py` 的幻灯片渲染与 `--locate-slides`。
纯本地步骤（`post_process.py`、`make_corrected.py`、`publish.py`、`python -m http.server`）沙箱内即可。

受限沙箱里 `uv run` 可能先写 `~/.cache/uv` 而报 `failed to open file .../.cache/uv/...: Operation not permitted`。
这类环境直接改用仓库解释器：`.venv/bin/python src/xxx.py`（Windows `.venv\Scripts\python.exe`），不要为了绕开缓存去改 uv 配置。

## 视频完全没有字幕

`--list-subs` 只有 `danmaku`、播放器接口 `subtitle.subtitles` 为 `[]`（新上传视频常见，B 站 AI 字幕尚未生成）
→ 走本地 ASR 兜底，见 `transcript.md` 第 3 节。**不要因此停止流程。**

## 字幕覆盖率过低（退出码 3）

先分清是登录态 / 代理问题，还是平台真没字幕（`transcript.md` 第 2 节的表格）。
覆盖率不足 50% 时**不得进入第二步**，否则成书会缺内容。

## 截图发糊 / 有黑边 / 带播放器 UI

- 发糊通常是账号档位问题：请用户在 `.capture-profile/` 里登录大会员后重跑 `capture_frames.py`。
- 画面其实是幻灯片却没升级成官方图：用 `course_assets.py --match-shots` + `--apply-map`（见 `course-assets.md`）。
- v2 管线本身已做流锁定、隐藏播放器 UI、只对 `<video>` 元素截图；不要手工改 viewport，更不要改用屏幕截图。

## 截图变回未登录态

`.capture-profile/` 里的 cookie 过期，重跑 `python src/capture_frames.py --setup-profile` 登录一次。

## ASR 相关

- **`TypeError: open() got an unexpected keyword argument 'metadata_errors'`**：PyAV 版本与 faster-whisper
  预期的解码 API 不匹配（如 av 19 + faster-whisper 1.2.x，2026-11 实测）。临时绕过：用 ffmpeg 把音频解成
  16k 单声道 PCM，再 `np.fromfile` 传给 `model.transcribe()`（faster-whisper 接受 numpy 数组），绕开 PyAV 解码；
  或把 `av` 降到与 faster-whisper 兼容的版本。`asr_transcript.py` 内部用 `faster_whisper.decode_audio`，
  同类环境会踩到同一个坑，先确认再整篇转写，别跑到一半才发现。
  ```bash
  ffmpeg -y -i audio.m4a -ar 16000 -ac 1 -f s16le /tmp/audio.pcm
  ```
  ```python
  import numpy as np
  from faster_whisper import WhisperModel
  pcm = np.fromfile("/tmp/audio.pcm", dtype=np.int16).astype(np.float32) / 32768.0
  segs, _ = WhisperModel("small", device="cpu", compute_type="int8").transcribe(pcm, language="zh", beam_size=5)
  ```
- `Library cublas64_12.dll is not found`：装 `nvidia-cublas-cu12 nvidia-cudnn-cu12`，脚本会自动注册 DLL 目录。
- 显存 <= 4GB：加 `--compute-type int8_float16`；无 NVIDIA 卡自动退 CPU int8（更慢）。
- 中途中断：直接重跑，进度从 `_asr_progress.jsonl` 续接；抽样试跑之后转全量**必须加 `--restart`**。
- 中文错词多、输出没标点：写 `output/<id>/_asr_prompt.txt`（一两百字术语表，Whisper prompt 上限约 224 token）。
- PowerShell 里用命令行传含中文引号的长 prompt 会被弯引号当成定界符截断 → 一律走文件。

## 校订稿相关

- **先 diff 基线再交付**：`MAP` / 口癖清理是全局规则，会误伤当前视频的正常用语（真实案例：
  `"外部": "外包"` 把「外部约束」「外部市场」全改错；`STUTTER` 把「但是 + 是不是」折成「但是不是」）。
  生成前后按 `correction.md` 的「生成前/后必做」打印 `原始 -> 清理后` 全量 diff 逐条确认；
  误伤不要靠改全局 MAP / 正则，用 `--edits` 在基线之上还原，审计报告会记录每条改动。
- `--edits` 报 `source_sha256 不匹配`：transcript 已变，基于当前版本重新生成提案。
- 报「大幅修改」：确认改动合理后加 `--allow-large-edits`，否则拆成更小的行级订正。
- 想重建对照稿：脚本默认拒绝覆盖已有文件，加 `--force`（会丢掉此前的人工订正，先备份）。

## 浏览器 QA 常见误报

- **`full_page=True` 截图在超长页面上重复/错位**：headless Chrome 对约 1.6 万像素以上的页面做全页拼接时
  可能复制上一屏内容。QA 不要只看全页 PNG：改用分段 viewport 截图（`scrollTo(y)` + `page.screenshot()`），
  并用 DOM 计数判定（截图卡片数、`img.naturalWidth>0`、`.mermaid svg` 数、`docWidth == clientWidth`）。
- **懒加载图片被判为裂图**：`loading="lazy"` 的图片在未滚动到视口时 `naturalWidth == 0` 是正常的。
  先按 600px 步长滚到底触发加载，再统计；页尾 lightbox 的 `<img>` 初始 `src` 为空，也会被误报一条。
- 控制台出现 favicon 404 不影响成书，真正的报错看 `pageerror` 与资源 4xx/5xx 列表。

## 页面目录编号重复（「1. 1. 回顾与开场……」）

**现象**：打开 `book.html`，目录里每条前面有两层编号（有序列表自己渲染的 `1.` ＋ 标题原文里带的 `1.`）。

**原因**：`book.md` 的「## 目录」是手写的，而 Markdown 有序列表**自己会渲染编号**；
模型只要把标题原文（含 `1. ` / `2.1 `）整段抄进链接文字就会叠一层。因为目录以前靠手写，
每次写书都靠运气，所以会反复复发。

**修复与防复发**：

```bash
python src/build_toc.py output/<id>/book.md            # 由标题重建目录（剥掉标题编号 + 重算锚点）
python src/build_toc.py output/<id>/book.md --check    # 只校验，不一致退出码 1
python src/post_process.py "<URL>" output/<id>/book.md  # 重渲染 HTML
```

- 新书在 `book.md` 里只写一行 `<!--TOC-->` 占位符，目录由脚本生成（见 `writing.md`）。
- 第四步 `post_process.py` 渲染前会自动调 `build_toc`：**外观级问题（编号重复 / 锚点过期）自动写回 `book.md`**；
  章节增删这类结构差异只用于本次 HTML，不会静默改写源文件，需要保存时显式跑 `build_toc.py`。
- 以前手写的旧书仍能识别（会替换「## 目录」整块），修完记得重跑第四步并重新发布。

## cookies 会泄露吗

不会落仓库：`dump_transcript.py` / `login_utils.py` 导出的 cookies 写入系统临时目录、用完即删；
`cookies.txt`、`.capture-profile/`、`output/` 都已在 `.gitignore` 中。仍不要手工把 cookies 文件放进工作区。

## 预览端口 12345 打开的是上一本书

`http.server` 停掉后端口可能被上一个视频的进程继续占用；`book.html` 同名，很难一眼看出错。换书后先确认：

```bash
lsof -nP -iTCP:12345 -sTCP:LISTEN     # 看 pid
ps -p <pid> -o command                 # 看 --directory 是不是当前 output/<id>
```

不是当前视频就停掉旧进程，再 `python -m http.server 12345 --directory output/<id>`；QA 与交付前各确认一次。

## YouTube 截图只有 830–880px 宽

见 `capture.md` 的「YouTube 画质 preflight」：匿名 720p 流 + embed 布局导致，不是黑帧也不是脚本失败；交付时说明，别改 viewport、别下载视频。

## Mermaid 长链被缩成一条细线

`graph LR` 的 6 节点以上长链会被容器等比缩小，文字只有 9–10px。改 `graph TD`（步骤链）或把并列关系拆成两条图，见 `writing.md`。

## 英文视频的 MAP 误伤

见 `correction.md` 的「英文视频：MAP 是中文向全局词表，先全量 dry-run」；不要改全局 MAP 来修单场视频。
