# 第三步：截帧并把占位符物化为图片

画面必须**直接来自平台播放器、使用已登录账号的高画质**；本步任何方式都不得下载视频 / 音频文件（铁律 2）。
按优先级执行，**上一级全部失败才进入下一级**。

## 优先级 0（桌面环境首选）：Chrome 扩展 @Chrome

仅当运行在 Codex / ChatGPT 桌面应用内、且 ChatGPT 浏览器扩展已安装并连接时使用（在 设置 > Computer Use 中确认）。

- 通过扩展控制用户真实、已登录的 Chrome：对 `book.md`（或 `book.tagged.md`）里每个 `SCREENSHOT:` 时间戳，
  打开对应平台播放器页面，等视频画面渲染后对该标签页截图，保存为 `output/<video_id>/images/shot_HH_MM_SS.png`。
  - B 站播放器地址：`https://player.bilibili.com/player.html?bvid=<video_id>&t=<秒>&autoplay=1&high_quality=1&danmaku=0`
  - YouTube 播放器地址：`https://www.youtube.com/embed/<video_id>?start=<秒>&autoplay=1&high_quality=1`
- 特点：使用登录态画质、后台运行、不接管用户屏幕。
- 全部时间戳截完后执行物化：`python src/capture_frames.py <video_id> --materialize-only`。
- 扩展未连接、截图失败、或运行环境不是桌面应用时 → 进入优先级 1。

## 优先级 1：专用截帧配置（v2 管线，所有 agent 环境通用）

Chrome 136+ 的安全策略禁止对**默认**用户数据目录做任何远程调试，所以脚本改用专用数据目录
（默认仓库根目录下的 `.capture-profile/`）：既不与主 Chrome 冲突，也不受调试禁令限制。

一次性初始化（登录一次，长期复用）：

```bash
python src/capture_frames.py --setup-profile
```

在弹出的 Chrome 窗口里登录需要的平台（B 站 / YouTube），然后关闭窗口。cookies 持久化在 `.capture-profile/`，
此后截帧自动携带登录态画质。**登录账号的会员等级决定截图清晰度上限。**

日常截帧（启动 Chrome，**需沙箱外执行**）：

```bash
python src/capture_frames.py <video_id> "<VIDEO_URL>"
```

v2 管线自动完成下面这些，agent 无需也不应手工干预：

- **无头运行**：默认 headless 不弹窗打扰用户，失败自动回退有头模式；
- **权益探针**：抓前查询该账号 / 该视频的顶档原生分辨率，viewport 按 1:1 设置（不放大、不糊，换账号换视频自动适配）；
- **流锁定**：路由拦截 playurl 响应，强制首帧即拉顶档最高码率流（根除「自动档从 360P 起播、暂停冻结升档」造成的糊图）；
- **纯净帧**：用 visibility CSS 隐藏全部播放器 UI（顶栏 / 控制栏 / 引流条 / 暂停推荐层），只对 `<video>` 元素截图，无黑边；
  嵌入播放器优先，失败自动降级到主站观看页；
- **精确帧**：`seek(目标秒)` → 等 `seeked` → `pause` 后截图，时间戳精确且为静止帧；
- **QA 自检**：截图文件过小判为黑帧，自动偏移重试；**只截缺失帧**，全部存在时直接跳过；
- **增量友好**：占位符清单始终读自 `book.tagged.md`（若存在），因此物化后重跑也能正确补帧。

其他：更换配置目录用 `--profile-dir <path>`；截图变回未登录态（cookie 过期）就重新执行 `--setup-profile`
登录一次；若本机日常 Chrome 已登录且用户同意，可走 `transcript.md` 的「登录态快速恢复」同步 cookies，免扫码。

**Agent QA 习惯**：截帧后抽查 1–2 张图（一张幻灯片帧、一张演示帧）确认清晰、无 UI 遮挡。
若整体发糊，通常是账号档位问题 —— 请用户在专用配置里登录大会员账号后重跑本命令，管线会自动按新档位原生分辨率重截。

**截图卡片文案要与画面一致**：每张 `SCREENSHOT:` 的描述会渲染成图注。抽查时对照画面里烧录的字幕/画中画与图注，
不一致就改 `book.tagged.md` 和 `book.md` 里的描述，再重跑第四步（图片本身不用重截）。

**口播类短视频的截图策略**（无幻灯片、无操作界面）：
- 每章 / 每小节起点 1 张，10 分钟左右的视频约 10–15 张即可；优先选章节边界、且被烧录字幕能概括该节主题的时刻。
- 截完先拼一张缩略图网格逐张核对：口播视频几乎每帧都是人像，判断标准是「这张画面＋图注能否支撑相邻文字」，
  不能支撑就换时间戳，而不是硬凑数量。
- 若某帧正好带画中画幻灯片 / 信息卡（如「权责不对等」那一页），优先用它作为该机制的总览图。

## 优先级 2（兜底）：下载视频源 + ffmpeg 抽帧

仅当上述所有浏览器方式全部失败（`capture_frames.py` 以非零码退出并提示 "All browser capture methods failed"），
或环境无浏览器 / 无界面时：

```bash
python src/extract_frames.py <video_id> "<VIDEO_URL>"
```

- `output/<video_id>/video_source.mp4` 不存在时，脚本自动用 yt-dlp 下载**未登录可用的最高 avc1 档**；
  注意这是未登录画质，仅作兜底。需要更高清晰度时，先自行下载（如带登录 cookies）同名文件放进去，脚本会跳过下载。
- 兜底产生的 `video_source.mp4` 属于大媒体文件：流程中途可保留，流程结束时按铁律 9 询问用户是否删除。

## 明确排除的方式（不要用）

- 内置浏览器（@Browser）：独立配置文件，默认没有平台登录态，不满足高画质要求。
- Playwright 自带的干净 Chromium：无登录态。
- 对主 Chrome 默认数据目录的任何远程调试（CDP 端口 / Playwright 管道）：Chrome 136+ 安全策略禁止，永不生效。
- win32 屏幕截取：会接管用户屏幕，已从脚本中移除。
- `--cookies-from chrome` 读主 Chrome：Windows 上 Chrome 新版 App-Bound 加密使 yt-dlp 无法解密，不要默认使用。

## 有 course/ 时：把糊帧升级为官方 4K 幻灯片

截帧之后执行，见 `course-assets.md` 的「截帧后升级」一节（`--match-shots` → 抽查 → `--apply-map`）。

## 完成标志

- `images/` 下每个时间戳都有对应的 `shot_HH_MM_SS.png`，`book.md` 中不再有 `SCREENSHOT:` 占位符
  （原标签稿已自动备份为 `book.tagged.md`）。
- 第四步生成的 HTML 中，截图卡片内置点击放大（lightbox）：点击图片看大图，点空白处或按 Esc 关闭。
- **归一化路径**：占位符物化成真图片后，`post_process.py` 生成的是**静态截图卡片**
  （`<img>` + 「回到原视频 (HH:MM:SS)」回链），而不是内嵌播放器：整本书 0 个 iframe 是正常结果。
  只有没物化成功的占位符（例如截帧被跳过）才会降级成 B 站 / YouTube 的 iframe 卡片——
  看到 HTML 里有 iframe，说明那一帧没截到。

## YouTube 画质 preflight（先测再截）

`capture_frames.py` v2 的「原生分辨率探针 + 顶档流锁定」目前只对 B 站实现。YouTube 分支固定 1280×720 viewport、只截 `<video>` 元素，embed 布局会把 720p 流压到约 **830–880px 宽**（2026-11 实测 830×467 / 880×495，同一批图里两种尺寸都会出现）。若 `.capture-profile` 没有 YouTube 登录态，YouTube 只能提供匿名 720p 流，成图不会是 1080p。

截帧前先确认（只读，不打印 cookie 值）：

- `.capture-profile` 里有没有 YouTube 的登录 cookie（SID / SSID / SAPISID / LOGIN_INFO 之类）；没有就是匿名会话；
- 或先跑 1 帧到 `/tmp`，用 `read_image` 看文字是否可读、尺寸多少，再决定要不要全量。

结果处理：

- 幻灯片文字可读时，接受 830–880px，并在交付说明里写清楚：本次是匿名 720p 流、非登录顶档；想更高清先登录再重跑。
- 脚本「只截缺失帧」，已有图片会被跳过；确实要升画质时，先备份/删除 `images/shot_*.png` 再重跑，或换一个新的 `--profile-dir`。
- 不要为了“更清晰”改 viewport、改用屏幕截图、下载视频源或读主 Chrome cookies——铁律 2 与上面的明确排除清单优先。
- 用户愿意登录时：`capture_frames.py --setup-profile` 登录 YouTube 后重跑；输出宽度仍受 embed 布局限制，提升有限，别承诺 1080p。

## 截帧期间不要改 book.md 正文

脚本开始时从 `book.tagged.md` / `book.md` 读占位符清单，整批截完后才统一 `materialize` 写回 `book.md`。截帧中途改正文或图注，可能被最后一次写入覆盖，或让“截图—图注”错位。正确顺序：

1. `book.md` 定稿（占位符、图注都先写）；
2. 全量截帧；
3. 逐张看实际画面，再改图注（此时图片已物化，改的是 `images/...` 的 alt/图注文本）；
4. 重跑 `post_process.py`；需要重截某一帧时，只删那一张 `shot_*.png` 再跑 `capture_frames.py`。

物化完成后若又改了时间戳，记得同步 `book.tagged.md`（把 `book.md` 里的 `images/shot_HH_MM_SS.png` 反向换回 `SCREENSHOT:HH:MM:SS` 写回去）。`capture_frames.py` 优先读 `book.tagged.md`；不同步的话，下次重跑会按旧清单补帧，新时间戳永远缺图。

## 问答 / 访谈段落的截图

有长 Q&A 的演讲里，大屏经常停在最后一页幻灯片，讲者也可能在回答时翻回旧页。Q&A 时间戳截出来的往往**不是讲者特写，而是某页旧幻灯片**：

- 截完先逐张 `read_image`，确认画面实际内容，再决定放在哪一节、图注怎么写；
- 同一页幻灯片已经在前文用过时，不要在 Q&A 再堆一张；确需引用要写明“讲者回到某图”；
- 不要按字幕语义臆测画面（例如以为在讲 rubric 就一定显示 rubric 页）。

## 动画 / 信息图视频：先选帧，再写占位符

旁白和画面不是一一对应的。直接从字幕猜时间戳，很容易截到「同主题但不同画面」的帧：2026-11 一次 31 分钟动画科普，第一版 21 个占位符里有 6 个图注与画面不符（图注按旁白写，画面却是另一张示意图）。

推荐两步法：

1. **候选总览**：按 20–40 秒间隔把候选帧截到 `/tmp/<id>-samples/`（复用 `capture_frames._shoot`，写临时目录），再用 Pillow 拼成 3 列缩略图总览；一次看几十帧，省上下文。
2. **定稿**：在总览里挑画面，回到 `book.md` 写最终时间戳与图注；**带文字/图表的帧必须 `read_image` 原尺寸看**，缩略图会把「印钞量（十亿美元）」看成「债务/GDP」——图注失实的代价比多花几秒高。

其他：

- **只换帧不用整批重跑**：官方管线只截缺失帧；改时间戳时直接替换 `book.md` 的图片链接、把新帧按 `shot_HH_MM_SS.png` 放进 `images/`，最后统一重跑 `post_process.py`。
- **时间预算**：YouTube 每条截图都要重新加载播放页，实测约 20–25 秒/帧；30 分钟视频 20+ 帧预留 8–10 分钟，候选帧另算。先算时间，别在交付前才发现。
- 图注写「画面里真实存在的东西」，正文语义由相邻段落承担；不要为了贴章节标题而把图注写成画面没有的内容。
