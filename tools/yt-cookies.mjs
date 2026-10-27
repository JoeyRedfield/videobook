#!/usr/bin/env node
/**
 * yt-cookies.mjs — 从「pi-browser-use 管理的浏览器」里导出 yt-dlp 用的 cookies.txt，
 * 并且自动发现调试端口（该端口每次启动都随机）。
 *
 * 为什么需要它：
 *   · pi-browser-use 不产出 cookies.txt，它只管理浏览器 profile 本身
 *   · profile 里的 cookie 在磁盘上是 v20/App-Bound 加密的，yt-dlp 读不了
 *   · 但 CDP 从浏览器内部取到的是明文，绕开加密
 *   · Chrome 也不写 DevToolsActivePort 文件（实测），所以端口只能从进程命令行里解析
 *
 * 用法：
 *   node tools/yt-cookies.mjs                      # 自动找端口 → 导出到默认路径
 *   node tools/yt-cookies.mjs --out D:\x.txt
 *   node tools/yt-cookies.mjs --domain x.com --domain twitter.com
 *   node tools/yt-cookies.mjs --launch             # 浏览器没开时，自己起一个（无头），导完就关
 *   node tools/yt-cookies.mjs --port 11594         # 手动指定端口
 *
 * 退出码：0 = 成功；1 = 失败（含"浏览器没在运行"）
 */

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

/* ------------------------------- 参数 ------------------------------- */

const argv = process.argv.slice(2);
const hasFlag = (n) => argv.includes(n);
const getArg = (n, d = null) => {
  const i = argv.indexOf(n);
  return i !== -1 && argv[i + 1] ? argv[i + 1] : d;
};
const getArgs = (n) => argv.reduce((a, v, i) => (v === n && argv[i + 1] ? [...a, argv[i + 1]] : a), []);

const PI_PROFILE_MARKER = join(homedir(), ".pi", "browser-profile");
const PROFILE_DIR_NAME = "pi-browser-use"; // --profile-directory 的值
const DEFAULT_OUT = join(homedir(), "cookies", "yt-cookies.txt");
const outPath = resolve(getArg("--out", DEFAULT_OUT));
// 最小权限：默认只导 youtube 相关域，**不碰** .google.com 的 SID/HSID。
// 实测 yt-dlp 用纯 youtube 域就能过机器人验证、能列字幕、能取格式；
// 这样即使文件泄露，也拿不到 Gmail / Drive / 账号设置。
// 需要全量时再显式 --domain google.com --domain accounts.google.com
const domains = getArgs("--domain").length
  ? getArgs("--domain")
  : ["youtube.com", "googlevideo.com"];
const explicitPort = getArg("--port");
const allowLaunch = hasFlag("--launch");

const CHROME_CANDIDATES = [
  join(process.env.PROGRAMFILES || "C:\\Program Files", "Google", "Chrome", "Application", "chrome.exe"),
  join(process.env["PROGRAMFILES(X86)"] || "C:\\Program Files (x86)", "Google", "Chrome", "Application", "chrome.exe"),
  join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
];
const chromePath = CHROME_CANDIDATES.find((p) => existsSync(p));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* --------------------------- 找调试端口 --------------------------- */

function runPowerShell(script) {
  return new Promise((res) => {
    const child = spawn("powershell", ["-NoProfile", "-Command", script], { windowsHide: true });
    let out = "";
    child.stdout.on("data", (c) => (out += c.toString("utf8")));
    child.on("close", () => res(out));
    child.on("error", () => res(""));
  });
}

async function findPortFromProcess() {
  // 只看 pi-browser-use 那个 profile 的 chrome 主进程
  const script = [
    "Get-CimInstance Win32_Process |",
    `  Where-Object { $_.Name -eq 'chrome.exe' -and $_.CommandLine -like '*${PROFILE_DIR_NAME}*' -and $_.CommandLine -match '--remote-debugging-port=' } |`,
    "  ForEach-Object { $_.CommandLine }",
  ].join("\n");
  const out = await runPowerShell(script);
  const ports = [...out.matchAll(/--remote-debugging-port=(\d+)/g)].map((m) => Number(m[1]));
  return ports.length ? ports[0] : null;
}

async function probePort(port) {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(5000) });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

/* --------------------------- 通过 CDP 取 cookie --------------------------- */

function cdpOnce(wsUrl, method, params = {}) {
  return new Promise((res, rej) => {
    const ws = new WebSocket(wsUrl);
    const done = (e, v) => {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      e ? rej(e) : res(v);
    };
    ws.addEventListener("open", () => ws.send(JSON.stringify({ id: 1, method, params })));
    ws.addEventListener("message", (ev) => {
      let m;
      try {
        m = JSON.parse(typeof ev.data === "string" ? ev.data : String(ev.data));
      } catch {
        return;
      }
      if (m.id !== 1) return;
      m.error ? done(new Error(`${method}: ${m.error.message}`)) : done(null, m.result);
    });
    ws.addEventListener("error", () => done(new Error(`${method}: WebSocket 连接失败`)));
    setTimeout(() => done(new Error(`${method}: 超时`)), 20000);
  });
}

const domainMatches = (d, f) => {
  const a = d.replace(/^\./, "").toLowerCase();
  const b = f.replace(/^\./, "").toLowerCase();
  return a === b || a.endsWith(`.${b}`);
};

async function exportCookies(port, version) {
  const { cookies } = await cdpOnce(version.webSocketDebuggerUrl, "Storage.getCookies");
  const picked = cookies.filter((c) => c.domain && domains.some((f) => domainMatches(c.domain, f)));
  if (!picked.length) {
    console.error(`❌ 浏览器里没有 ${domains.join(", ")} 的 cookie —— 说明还没登录。`);
    console.error("   先在 pi 的浏览器窗口里登录一次，再重跑本脚本。");
    return 1;
  }

  mkdirSync(dirname(outPath), { recursive: true });
  const header = [
    "# Netscape HTTP Cookie File",
    "# 由 tools/yt-cookies.mjs 生成（CDP Storage.getCookies，绕开 Windows App-Bound Encryption）",
    `# 生成时间: ${new Date().toISOString()}`,
    `# 来源: pi-browser-use 浏览器 profile（端口 ${port}）`,
    `# 刷新: node tools/yt-cookies.mjs`,
    "",
  ].join("\n");

  const lines = picked.map((c) =>
    [
      c.domain,
      c.domain.startsWith(".") ? "TRUE" : "FALSE",
      c.path || "/",
      c.secure ? "TRUE" : "FALSE",
      c.session || !c.expires || c.expires < 0 ? 0 : Math.floor(c.expires),
      c.name,
      c.value,
    ].join("\t"),
  );
  writeFileSync(outPath, `${header}\n${lines.join("\n")}\n`, "utf8");

  const counts = new Map();
  for (const c of picked) counts.set(c.domain, (counts.get(c.domain) || 0) + 1);
  console.log(`✅ 已写入 ${outPath}（${picked.length} 条）`);
  for (const [d, n] of [...counts].sort((a, b) => b[1] - a[1]).slice(0, 8)) console.log(`   ${d.padEnd(26)} ${n}`);
  return 0;
}

/* ------------------------------- 主流程 ------------------------------- */

let port = explicitPort ? Number(explicitPort) : await findPortFromProcess();
let version = port ? await probePort(port) : null;
let launched = null;

if (!version && !explicitPort && allowLaunch) {
  if (!chromePath) {
    console.error("❌ 找不到 chrome.exe，无法 --launch");
    process.exit(1);
  }
  const launchPort = 9223;
  console.log(`浏览器没在运行 —— 用 pi 的 profile 起一个无头实例（端口 ${launchPort}）…`);
  launched = spawn(
    chromePath,
    [
      `--user-data-dir=${PI_PROFILE_MARKER}`,
      `--profile-directory=${PROFILE_DIR_NAME}`,
      `--remote-debugging-port=${launchPort}`,
      "--remote-allow-origins=*",
      "--no-first-run",
      "--no-default-browser-check",
      "--headless",
      "about:blank",
    ],
    { stdio: "ignore", windowsHide: true },
  );
  for (let i = 0; i < 20 && !version; i++) {
    await sleep(500);
    version = await probePort(launchPort);
    port = launchPort;
  }
}

if (!version) {
  console.error("❌ 没有找到 pi-browser-use 的浏览器。");
  console.error("");
  console.error("   原因可能是：浏览器后端已停止（pi 闲时会关掉它）。");
  console.error("");
  console.error("   三种解决办法：");
  console.error("     1) 加 --launch 让本脚本自己起一个无头实例（导完自动关闭）");
  console.error("     2) 让 pi 随便打开一个页面以唤醒浏览器，然后重跑本脚本");
  console.error("     3) 在 pi 的浏览器窗口里直接用：node tools/yt-cookies.mjs --port <端口>");
  process.exit(1);
}

let code = 1;
try {
  code = await exportCookies(port, version);
} finally {
  if (launched) {
    try {
      launched.kill();
    } catch {
      /* ignore */
    }
    console.log("（已关闭本脚本启动的临时实例）");
  }
}
process.exit(code);