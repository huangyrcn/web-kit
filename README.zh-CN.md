# web-kit

一个面向 AI 编程代理（Claude Code、Copilot CLI 等）的技能包，提供
**三个轻量级 Web 基元能力**：搜索、网页读取、鉴权下载。
并附带一个可自托管的**单容器后端**来统一支撑这些能力。

```
┌──────────────────────────┐         ┌──────────────────────────────────┐
│   skill/  (AI 技能层)    │  ◄───►  │   backend/  (服务端)             │
│   ─────────────────────  │         │   ─────────────────────────────  │
│   ask-search             │         │   SearxNG + Chrome+Playwright    │
│   crwlr                  │         │   CDP，全都在一个容器里，         │
│   cdp-download           │         │   4 层自愈                        │
└──────────────────────────┘         └──────────────────────────────────┘
        │                                            ▲
        └─ uses ──── SEARXNG_URL=http://...:8082 ────┤
                     CDP_URL=http://...:9223 ────────┘
```

你可以整体一起使用（本仓库主场景），也可以拆分使用：
- **仅 skill**：指向你现有的 SearxNG / 开启 CDP 的 Chrome。
- **仅 backend**：可直接替代常见的 4 容器 searxng + browser-proxy 方案。

---

## skill/ — AI 技能层

代理可调用的三个命令：

| 工具 | 功能 | 代理何时会用 |
|---|---|---|
| `ask-search "<query>"` | 基于 SearxNG 聚合的多引擎搜索 | “搜索 X”、“查一下”、“找一下…” |
| `crwlr crawl -o md "<url>"` | 用真实浏览器渲染页面并转为干净 markdown | “读这个页面”、“这个 URL 说了什么”、“转成 markdown” |
| `cdp-download <url>` | 通过 Chrome DevTools Protocol 下载文件（复用浏览器 Cookie） | 当 `wget`/`curl` 无法下载鉴权资源时 |

相比代理内置的普通 Web 工具，这个 skill 提供：
- 真实 JS 渲染（看到的是用户实际看到的页面，而不是服务端裁剪版本）
- 持久登录 Cookie（通过 noVNC 登录一次，后续抓取可持续带鉴权）
- 多搜索引擎聚合（Google、DDG、Bing、Brave，外加 13 个学术来源）
- 轻量 CLI 接口（不需要 MCP server / 额外 runtime）

### 安装（以 Claude Code 为例）

```bash
git clone https://github.com/huangyrcn/web-kit.git
cp -r web-kit/skill ~/.claude/skills/web-kit
chmod +x ~/.claude/skills/web-kit/scripts/*

# 告诉 skill 后端地址
export SEARXNG_URL=http://your-host:8082
export CDP_URL=http://your-host:9223
```

Copilot CLI、Gemini CLI 或其他平台请参考 `skill/SKILL.md`，文档里列出了各平台的 skill 加载方式。

### 必需环境变量

| 变量 | 被谁使用 | 示例 |
|---|---|---|
| `SEARXNG_URL` | `ask-search` | `http://localhost:8082` |
| `CDP_URL` | `crwlr`、`cdp-download` | `http://localhost:9223` |

如果你已经有可用的 SearxNG 实例和暴露 CDP 的 Chrome，到这里即可。

---

## backend/ — 单容器服务端

自托管后端同时提供 `SEARXNG_URL`（8082 端口）和 `CDP_URL`（9223 端口），
并提供 noVNC 界面（6080 端口）用于首次手动登录以写入 Cookie。

### 为什么打包成一个容器？

传统自托管方案里，带 JS 渲染搜索代理的 SearxNG 往往需要 4 个以上容器
（searxng、redis、browser-proxy、novnc）并靠网络编排连接。
这种方式比较脆弱：一个容器故障可能连锁影响其余组件，且缺乏统一监控与重启入口。

这个 backend 将其收敛为**单容器**，由 `supervisord` 统一管理所有进程，并实现**四层自愈**：

1. **进程级**：supervisord `autorestart=true`，任一进程崩溃自动拉起。
2. **应用级**：每次请求时 `_ensure_browser()` 都会在必要时重连 CDP（覆盖 Chrome 中途崩溃场景）。
3. **容器级**：Docker `HEALTHCHECK` 探测全部三个对外端口。
4. **业务级**：`watchdog.sh` 每 60 秒执行一次真实搜索；连续 3 次失败后执行
   `supervisorctl restart chrome search-proxy`（可覆盖“进程存活但浏览器假死”场景）。

### 快速启动

```bash
cd backend
cp searxng-settings/settings.yml.example searxng-settings/settings.yml
$EDITOR searxng-settings/settings.yml      # 设置 secret_key 及可选 API key

docker compose up -d                        # 首次构建约 5-10 分钟（Chrome + SearxNG）
sleep 90                                    # 预热

curl 'http://localhost:8082/search?q=test&format=json' | jq '.results | length'
curl http://localhost:9223/json/version | jq .Browser
```

首次 Google 登录（有助于降低后续验证码频率）：
1. 打开 `http://localhost:6080/vnc.html`
2. 点击 **Connect**，会看到 fluxbox 桌面和 Chrome 窗口
3. 登录 Google；Cookie 会持久化在 Docker volume 中

### 架构

```
        backend 容器（supervisord PID 1，监管 9 个程序）
   ┌─────────────────────────────────────────────────────────────────────┐
   │                                                                     │
   │  Xvfb :99 ── fluxbox ── x11vnc :5900 ── websockify ─► host:6080     │
   │                                                                     │
   │  google-chrome --remote-debugging-port=9222                         │
   │       │                                                             │
   │       ├─ socat ─► host:9223  (CDP，供 Playwright/Puppeteer 使用)    │
   │       │                                                             │
   │       └─ search-proxy (FastAPI :3100，仅 localhost)                 │
   │              ↑                                                      │
   │              └─ /google、/ddg 端点 —— Chrome 渲染 HTML              │
   │                                                                     │
   │  SearxNG (granian :8080) ── http://localhost:3100/{google,ddg}      ���
   │       └─► host:8082                                                 │
   │                                                                     │
   │  watchdog.sh (60s 探测) ── 异常时 supervisorctl 重启                │
   │                                                                     │
   └─────────────────────────────────────────────────────────────────────┘
```

### 可靠性（单台 x86_64 主机实测）

| 故障模式 | 恢复表现 | 机制 |
|---|---|---|
| Chrome 被杀死（`pkill -9`） | CDP 约 12s 恢复，搜索结果约 43s 恢复 | supervisord + `_ensure_browser()` 重连 |
| FastAPI search-proxy 卡死 | < 60s | watchdog 检测并重启 |
| 上游网络抖动 | 每次隔离 < 60s | failure cache + SearxNG `suspended_times` |
| 容器 OOM | 取决于主机 | `restart: unless-stopped` |

### 反检测能力

浏览器侧使用 [`patchright`](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
（可直接替换 Playwright，修补 CDP 层如 `Runtime.enable` / `Console.enable` 泄露）
并配合最小化 Chrome flag 集合。
在典型测试站点上的验证结果：

| 测试站点 | 结果 |
|---|---|
| `bot.sannysoft.com` | WebDriver / WebDriver Advanced / Chrome / Plugins / `debugTool: false` 全部通过 |
| `nowsecure.nl`（Cloudflare anti-bot） | 连续 3 次以上稳定通过 Cloudflare 挑战 |
| Google / DuckDuckGo / Bing SERP | 稳定可用，持久 profile 下验证码频率较低 |

能力边界：backend **无法稳定通过**以下场景：
- Cloudflare Turnstile（交互式组件）
- DataDome / PerimeterX / Akamai Bot Manager
- 任何必须住宅代理 IP 的站点（数据中心 IP 指纹会触发风控）

这些场景建议将 `ask-search` / `crwlr` 接入 Bright Data、ZenRows、ScrapFly 等 SaaS。

自行验证：
```bash
docker exec web-kit-backend pkill -9 -f google-chrome-stable
time until curl -s 'http://localhost:8082/search?q=test&format=json' \
            | jq -e '.unresponsive_engines | length == 0' >/dev/null; do sleep 1; done
```

### 性能

主机回环测试：

| 场景 | P50 | P95 |
|---|---|---|
| 串行 SearxNG 搜索 | ~3.6s | ~4.5s |
| 并发 ×3 | ~9s | ~12s |
| 局域网跨主机 | ~5.7s | ~5.9s |

运行你自己的基准：
```bash
python3 backend/bench/speed-test.py -n 8 --target http://localhost:8082
```

### 可调配置

| 位置 | 含义 | 默认值 |
|---|---|---|
| `searxng-settings/settings.yml` | SearxNG 引擎、secret key、API key | 必填，见 `.example` |
| `docker-compose.yml` 的 `mem_limit` | 容器内存上限 | 4 GB |
| `docker-compose.yml` 的 `shm_size` | Chrome 使用的 `/dev/shm` 大小 | 1 GB |
| 环境变量 `SEARCH_PROXY_CONCURRENCY` | 并行 Chrome 页面数 | 3 |
| 环境变量 `FAILURE_CACHE_SECONDS` | 引擎隔离时长 | 60s |
| 环境变量 `PROBE_INTERVAL` | watchdog 探测间隔 | 60s |

---

## 仓库结构

```
.
├── skill/                       ← AI 技能包
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── ask-search.py
│   │   ├── crwlr
│   │   └── cdp-download
│   └── references/
│       ├── engines.md
│       └── workflow.md
│
├── backend/                     ← 单容器服务端
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── supervisord.conf
│   ├── server.py                — 含 _ensure_browser() 的 FastAPI search-proxy
│   ├── start-chrome.sh / start-searxng.sh / start-xvfb.sh
│   ├── healthcheck.sh, watchdog.sh
│   ├── searxng-settings/settings.yml.example   ← 实际 settings.yml 已在 gitignore
│   └── bench/speed-test.py
│
├── README.md
├── LICENSE          (MIT)
└── .gitignore
```

---

## 许可证

[MIT](./LICENSE)。
