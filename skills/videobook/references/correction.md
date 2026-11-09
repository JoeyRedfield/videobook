# 第 2c 步（可选）：字幕校订对照稿 transcript.corrected.txt

目的：给读者一份**可对照视频逐段核对**的字幕稿 —— 保留讲师原始字词与顺序，只修 ASR 错词和口癖，
不改写成书面语、不做概括（书面化重构是 `book.md` 的职责）。该文件随第六步发布到 `pages`，
落地页卡片会自动为含对照稿的书加上「字幕对照」入口。

## 命令

```bash
python src/make_corrected.py <video_id> [<video_id> ...]   # 或 --all
python src/make_corrected.py <video_id> --edits corrections.json --force
```

- 输出 `output/<video_id>/transcript.corrected.txt`，格式与 `transcript.txt` 相同：`[HH:MM:SS] 原文`，逐段不合并。
- **重跑保护**：目标文件已存在时脚本拒绝覆盖（防止抹掉后续的人工 / AI 行级订正）。
  确需从头重建加 `--force`；要叠加新校订用 `--edits <corrections.json>` 加 `--force`
  （`--edits` 只决定“叠加什么”，并不豁免重跑保护，漏写 `--force` 会直接报错退出）。
- 纯本地步骤，沙箱内可跑。

## 只允许两类修改（铁律 5）

1. **ASR 错词替换**：来自 `src/make_corrected.py` 里的 `MAP`（按 key 长度降序应用，避免子串误伤）。
   **新视频遇到的系统性错词，直接往 `MAP` 里加一条并提交**（仓库里已有按讲次补充的先例），
   不要把 MAP 复制进 skill 或 `book.md`。
   - 加 key 必须先 `rg` 全仓库 transcript 确认它不会误伤正常用语：MAP 是**全局子串替换**，
     过短的 key 会改坏别的讲次。仓库里真实踩过的坑：`"构图": "goto"` 会把「架构**图**」变成「架goto」，
     `"view": "View"` 会把「review」变成「reView」（后者已改为带前导空格的 `" view"`）。
     **判断标准**：key 只有出现在你正在修的那句话里才安全，否则把 key 写长一点（带上上下文词）。
2. **口癖清理**：纯语气词整段删除、句尾语气词剥离、单字口吃叠词折叠。

## 生成前/后必做：diff 基线，逐条确认每处改动

`MAP` 与口癖清理是**全局规则**，可能误伤当前视频的正常用语。生成前后先跑一遍 dry-run，把所有
「原始段 → MAP/清理后的段」打出来逐条判断（不要只看脚本打印的「修正 N 行」）：

```bash
python - <<'PY'
import json, sys
sys.path.insert(0, "src")
import make_corrected as mc
segs = json.load(open("output/<video_id>/transcript.json", encoding="utf-8"))["segments"]
for i, s in enumerate(segs):
    after = mc.correct(s["text"])
    if after != s["text"] or after in mc.FILLER_ONLY:
        print(i, repr(s["text"]), "->", repr(after))
PY
```

真实踩坑（BV1g6sf67Edz，职场口播视频，2026-11）：

- MAP 里为**别的视频**加的 `"外部": "外包"` 把「外部的约束条件」「对外部市场的了解」全部改成「外包…」。
- `STUTTER` 在跨词边界上误折叠：「但是 + 是不是」里的两个「是」被折成「但是不是忽略了一件事情」。
- 这类误伤**不要改 MAP / 正则本身**（会破坏其他讲次），而是在生成后用 `--edits` 还原：
  `original` 填 **MAP 之后**的基线文本（不是 transcript 原文），`replacement` 填还原文本，
  `category` 用 `transcription` / `segmentation`，`reason` 写明是词表误替换或叠字误折叠。
  审计报告 `transcript.corrections.json` 会记录每条的 `change_ratio`。

## 用画面内嵌字幕核对可疑用词

很多 B 站 / YouTube 口播视频把字幕直接烧进画面。遇到平台 AI 字幕与本地 ASR 意见不一致的**个别可疑词**
（英文术语、同音词），不必整篇重转：在对应秒截一帧，读画面字幕即可定案。用专用配置（保证 1080P）
和 `capture_frames.py` 的内部函数临时截到 `/tmp`，不要污染 `images/`：

```python
import os, sys
sys.path.insert(0, "src")
import capture_frames as cf
from playwright.sync_api import sync_playwright

vid, out = "<video_id>", "/tmp/frame-check"
os.makedirs(out, exist_ok=True)
shots = cf.shots_spec(["00:06:17"], "bilibili", vid)   # 只查可疑秒
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        ".capture-profile", channel="chrome", headless=True,
        viewport={"width": 1920, "height": 1080},
        args=["--autoplay-policy=no-user-gesture-required"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    cf._force_top_quality(page)
    cf._shoot(page, shots[0][0], "check_00_06_17", out, [shots[0][2], shots[0][3]])
    ctx.close()
```

实测：BV1g6sf67Edz 里平台字幕的 `daylight` 画面实为 **deadline**、`honor` 实为 **owner**，
据此在 `--edits` 里做了最小订正。注意画面字幕也可能滞后、省略句子或本身就是创作者转写，
**只用来定格个别词**，不能当全文底稿；`capture_frames` 内部函数随仓库版本演进，参数以当前 `src/capture_frames.py` 为准。

## AI 行级订正（第二遍，叠加在 MAP 之后）

MAP 覆盖不到的问题（少字、截断词、不通顺）由 agent 通读全文后提案，脚本校验通过才应用：

1. 通读 `transcript.corrected.txt`，产出 old → new 清单（量大时可委派子代理生成，再由主流程审）。
2. 写成 `corrections.json`：

```json
{
  "source_sha256": "<transcript.json 中 segments 的 sha256>",
  "edits": [
    {
      "segment_index": 123,
      "original": "MAP 之后该段的完整原文",
      "replacement": "订正后的单行文本",
      "category": "transcription",
      "reason": "少识别一个字：车轱话 -> 车轱辘话"
    }
  ]
}
```

3. `python src/make_corrected.py <video_id> --edits corrections.json`，脚本会产出审计报告
   `output/<video_id>/transcript.corrections.json`（含每条的 `change_ratio`）。
4. **生成后抽查若干行确认无过改**，再进入发布。

### 校验层会拒绝什么（见 `src/make_corrected.py` 的 `apply_ai_edits`）

| 规则 | 说明 |
|---|---|
| `source_sha256` 不匹配 | 提案基于旧版 transcript，必须重新生成 |
| `segment_index` 非法或重复 | 必须是 `0 <= idx < len(segments)` 的整数且不重复 |
| 段已被口癖清理删除 | 不允许对已删除的段做校订 |
| `original` 与基线不逐字一致 | 基线 = MAP 应用后的文本，必须逐字相同 |
| `replacement` 为空或含换行 | 禁止删段、空替换、多行替换 |
| `category` 不合法或 `reason` 为空 | `category` 取 `proper_noun` / `technical_term` / `transcription` / `punctuation` / `segmentation` |
| 大幅修改 | 段长 >= 40 且变更率 > 40% 时默认拒绝，人工确认后加 `--allow-large-edits` |

### 订正原则

- 少字 / 错字 / 截断词这类识别错误**必修**（「车轱话」→「车轱辘话」）。
- 讲师**有意为之的强调式重复原样保留、不得折叠**（「工作工作工作工作」「点点点点点点点了十层」）。
- 不替讲师改观点、不补外部知识、不雅化口语梗。

## 英文视频：MAP 是中文向全局词表，先全量 dry-run

`MAP` 主要来自中文视频的 ASR 错词，按**全局子串**替换，对英文 transcript 很容易误伤。2026-11 实测一场 1973 段英文演讲：MAP 命中 16 处，只有 2 处正确，14 处是误伤：

| 原文 | MAP 误改成 | 原因 |
| --- | --- | --- |
| `to call` | `tool call` | 子串命中 |
| `I get it.` | `I git.` | `get it` → `git` |
| `fundamentals` | `fundmentals` | `amental` 子串 |
| `interrogate` | `interrogit` | `gate` → `git` |
| `view` / `viewer` | `View` / `Viewer` | 英文词强制大写 |
| `internet` | `Internet` | 大小写替换 |

规则：

- 不要把“脚本打印修正 N 行”当作成功；先按本文件「生成前/后必做」全量 dry-run 逐条看。
- 误伤用 `--edits` 还原（`original` 填 **MAP 之后**的基线文本），不要为了英文视频去改全局 MAP。
- 英文只在确有必要时做行级订正（专有名词、截断词、不通顺），照旧不改写、不删段。

2026-11 补充案例（英文视频）：

- 英文稿命中 `MAP` 的 `"ripple": "repo"`，把 `crippled` 改成了 `crepod`；这类子串误伤一律用 `--edits` 还原，不改全局 MAP。
- 老视频的英文自动字幕常无标点、`i` 小写。行级订正只修词形/漏词（`death burden → debt burden`、`wealthy halves → haves`、`different for money → from money`、`good services → goods services`、`i → I`），**不要整篇补标点**——那是改写，不是校订。
- 英文 auto 字幕的 corrected 稿价值有限：先判断值不值得做；要做就保持逐段、最小改动，并在交付说明里写清「英文原字幕，仅词形订正」。
