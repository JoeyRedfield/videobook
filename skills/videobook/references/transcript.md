# 第一步：提取字幕（含本地 ASR 兜底）

## 1. 平台字幕

```bash
python src/dump_transcript.py "<VIDEO_URL>"
```

- 自动识别平台，产出 `output/<video_id>/transcript.json` 与 `transcript.txt`（`[HH:MM:SS] 原文`，逐段不合并）。
- `transcript.json` 字段：`video_url / title / video_id / duration / chapters / segments`。
  第二步必须用到 `duration` 与 `chapters`（平台官方章节，可能是空数组）。
- **B 站登录态自动获取**：`.capture-profile/` 里有登录态时，脚本自动把 cookies 导出到系统临时目录、用完即删，
  期间会启动无头 Chrome，因此**需沙箱外执行**（Codex 中申请 require_escalated）。
- 提示专用配置无登录态时：先看本机日常 Chrome 是否已登录该站；用户同意复用登录态时按下面
  「登录态快速恢复」同步进专用配置（免扫码）；否则请用户跑一次 `python src/capture_frames.py --setup-profile` 扫码登录，再重试本步。
- cookies 来源三选一：`--cookies-from-profile <dir>`（默认 `.capture-profile`）、`--cookies-file <path>`（Netscape 格式）、
  `--cookies-from <browser>`（旧方式；Windows 主 Chrome 因 App-Bound 加密通常解不开，**不要默认使用**；
  macOS 主 Chrome 已登录时可作应急，实测能抓到带登录态的字幕，但更稳的是把登录态同步进专用配置，见下）。
- 登录态只在 **pi 浏览器**里、`.capture-profile` 没登该站时：`node tools/yt-cookies.mjs` 导出到 `~/cookies/yt-cookies.txt`，
  再 `--cookies-file ~/cookies/yt-cookies.txt`（默认只导 youtube/googlevideo 域，不碰 Gmail/Drive 的 SID/HSID）。
- **覆盖率自检**：成功时打印「字幕覆盖率」= 末段结束时间 / `duration`。低于 50% 以退出码 3 结束并打印修复提示，
  此时**不得进入第二步**（铁律 1）。

### 登录态快速恢复（主 Chrome 已登录、免扫码）

`.capture-profile` 的登录态会过期；而 `--setup-profile` 需要用户在场扫码。若**本机日常 Chrome 已登录 B 站**
且用户同意复用该登录态，可以把它一次性同步进专用截帧配置——抓字幕和第三步高画质截帧会同时恢复：

1. 用 yt-dlp CLI 从主 Chrome 导出 Netscape cookies。macOS 上主 Chrome 的 v10 cookies 实测可用
   （注意要调 CLI；直接用库函数 `extract_cookies_from_browser` 在部分环境会拿不到 keychain key）：

```bash
python -m yt_dlp --cookies-from-browser chrome --cookies /tmp/bili-cookies.txt \
  --skip-download -F "<VIDEO_URL>"
```

2. 用 Playwright 以专用配置启动（与 `--setup-profile` 相同的 mock-keychain 后端），把 cookies 写进去。
   播放器页面的 cookie 需要 `SESSDATA / DedeUserID / bili_jct` 等关键项；导出文件里的过期时间是
   WebKit epoch（微秒），Playwright 只接受合法 unix 秒或 `-1`，`expires=0` 会被拒绝：

```python
from playwright.sync_api import sync_playwright

def norm_exp(v: str) -> int:
    v = int(v)
    if v > 10**11:                      # WebKit 微秒 -> unix 秒
        v = int(v / 1e6 - 11644473600)
    return v if v > 0 else -1

cookies = []
for line in open("/tmp/bili-cookies.txt", encoding="utf-8"):
    if not line.strip() or line.startswith("#"):
        continue
    dom, flag, path, secure, exp, name, value = line.rstrip("\n").split("\t")[:7]
    if "bilibili" in dom:
        cookies.append({"name": name, "value": value, "domain": dom, "path": path,
                        "secure": secure == "TRUE", "httpOnly": False,
                        "expires": norm_exp(exp)})

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(".capture-profile", channel="chrome", headless=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.bilibili.com", wait_until="domcontentloaded")
    ctx.add_cookies(cookies)
    page.reload()
    ctx.close()
```

3. 验证：`python src/dump_transcript.py "<VIDEO_URL>" --cookies-from-profile .capture-profile`
   应直接抓到带登录态的字幕；截帧日志出现 `native stream size: 1920x1080` 也说明登录态生效。

约束：
- cookies 文件只写系统临时目录，用完即删；`.capture-profile/` 与 `output/` 都在 `.gitignore` 里，**绝不入库**。
- 这是「用户本机已登录且明确同意复用」的应急路径。默认交付仍优先 `--setup-profile`，不要把主 Chrome 数据目录当长期方案。
- 不要把 cookies 值打印到终端/日志，也不要在回复里复述。

## 2. 区分两类失败（决定是修环境还是转 ASR）

| 现象 | 判断 | 处理 |
|---|---|---|
| 覆盖率过低、超时、地区限制、需要登录 | 环境 / 权限问题 | 修代理或登录态后重跑本步 |
| `--list-subs` 只有 `danmaku`；播放器接口 `subtitle.subtitles` 为 `[]` | 平台确实没有字幕（新上传视频常见，B 站 AI 字幕尚未生成） | **不要停流程**，转下面的 ASR 兜底 |

两类都排除后仍失败，才告知用户可能原因（无字幕 / 需代理 / 需登录）并停止。海外视频终端必须走代理。

## 3. 兜底：本地 ASR 转写

```bash
python src/asr_transcript.py <video_id> [--sample-start 900 --sample-dur 180]
```

- 流程：只下载音频轨到 `output/<id>/audio.m4a`（不下载视频流）→ faster-whisper `large-v3` 转写 →
  按标点/时长切成接近平台字幕粒度的短段 → 写出与平台字幕**完全同构**的 `transcript.json` / `transcript.txt`。
  所以第二步之后的所有步骤零改动。
- **先抽样再全量**：`--sample-start 900 --sample-dur 180` 只转 3 分钟，确认质量与速度后再跑全量；
  跑全量**务必带 `--restart`**，否则会漏掉抽样区间之前的内容。
- **领域术语提示**：把本讲主题与术语（一两百字）写进 `output/<id>/_asr_prompt.txt`，能显著压制同音错词并让中文输出自带标点。
  Whisper prompt 上限约 224 token，过长会被截断。**PowerShell 下不要用命令行传含中文引号的长 prompt**
  （弯引号会被当成字符串定界符），一律走文件，或用 `--initial-prompt-file`。
- **算力**：有 NVIDIA 显卡自动用 CUDA；显存 <= 4GB 加 `--compute-type int8_float16`；无卡自动退 CPU int8。
  Windows 报 `Library cublas64_12.dll is not found` 时装 `nvidia-cublas-cu12 nvidia-cudnn-cu12`（脚本会自动注册 DLL 目录）。
- **断点续跑**：进度实时写入 `output/<id>/_asr_progress.jsonl`，中断后重跑自动从末尾续接。
- **耗时预期**：100 分钟课程在 RTX 3050 (4GB) 上约 35 分钟，会产生约 90MB 的 `audio.m4a`。
  这段时间可以并行准备第三步的截帧环境（`--setup-profile`）和术语表。结束时按铁律 9 询问是否删除大文件。
- 转写完成后仍要过覆盖率自检（脚本已内置）；低于 50% 说明没跑完，重跑续写。

## 4. ASR 稿的后续待遇

ASR 产出**同样算「原始字幕」**：同样要建术语表、同样要容忍同音错词、同样要产出 `transcript.corrected.txt`
（见 `correction.md`）。本地 large-v3 的错词率通常低于平台 AI 字幕，但专有名词仍需人工订正。

## 5. YouTube 走 yt-dlp 兜底时的两个坑（2026-11 实测）

- `youtube-transcript-api` 经本地代理可能报 `SSLEOFError`（`SSL: UNEXPECTED_EOF_WHILE_READING`），而 yt-dlp 正常。脚本回退 yt-dlp 后 `--sub-langs en.*,zh.*` 会优先选中文轨；但本片的 zh 轨其实是 **YouTube 机器翻译**（文件名如 `zh-Hant-tr`），质量低于英文原字幕。
- 更隐蔽：`convert_vtt_to_json` 产出的 `start`/`end` 是 VTT 时间串（`00:00:00.160`），`dump_transcript.py` 的覆盖率自检按 `HH:MM:SS` 取整失败，打印 `0.0%` 并以退出码 3 结束——这是**假警报**，不是字幕缺失，也不是登录态问题。
- 处理（不阻断成书）：用 yt-dlp 单独取英文原 SRT，再自己转成与平台同构的 `transcript.json`。

```bash
# 1) 取英文原字幕（auto caption 也行；有 manual en 时优先 manual）
python -m yt_dlp --write-subs --write-auto-subs --sub-langs en --sub-format srt \
  --skip-download --no-check-certificates -o "/tmp/subs/%(id)s.%(ext)s" "<URL>"
# 2) 取元数据（title / duration / chapters）
python -m yt_dlp -j --skip-download "<URL>" > /tmp/meta.json
```

- 解析 SRT 时把 `00:00:04,080` 规范成 `HH:MM:SS`，逐段写入 `segments`；`chapters` 从元数据转成同样格式。写完后仍用「末段结束时间 / duration」做一次覆盖率自检（应接近 100%）。
- **语言选择原则**：平台有官方人工中文字幕才优先中文；只有 auto-caption 时，英文视频一律「英文原字幕 + 自己翻译」，不要拿机器翻译的中文轨当底稿。
