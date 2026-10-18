# VideoBook Agent 项目约定

## 项目范围

- 从 YouTube / Bilibili 视频生成带时间锚点、截图和字幕对照的静态电子书。
- `main` 保存源码和通用文档；`pages` 保存由 `src/publish.py` 生成的 GitHub Pages 成品。
- 所有命令默认从仓库根目录执行，Python 版本以 `.python-version` 为准。

## 隐私边界

- 不提交 Cookie、登录态、密钥、令牌、密码、`.env`、浏览器配置、缓存、媒体和本机运行记录。
- 不把用户名、主机名、绝对 home 路径、内网地址或代理地址写入源码、文档、测试输出或发布内容。
- 运行时目录 `.capture-profile/`、`output/`、`.venv/`、`.pytest_cache/`、`cookies*.txt` 和 `.DS_Store` 必须保持未跟踪。
- 发布前检查 `git status --short --ignored` 与 `git grep`；公开文档只保留可复现的通用步骤。

## 开发与验证

```bash
uv run pytest -q
git diff --check
```

修改抓取、渲染或发布逻辑时，先阅读对应的 `src/*.py`、`instructions.md` 和 `skills/videobook/` 参考文档。不要把一次性本机排障记录加入仓库；可复用的结论应改写成不含本机信息的通用说明。

## 上游同步与发布

`origin` 是个人仓库，`upstream` 是原项目。同步源码时：

```bash
git fetch upstream
git switch main
git merge upstream/main
git push origin main
```

源码合并后重新生成并发布静态站点：

```bash
uv run python src/publish.py --all
git push origin pages
```

不要直接把 `upstream/pages` 合并进个人 `pages`；该分支是从当前 `main` 和本地 `output/` 重新生成的发布产物。
