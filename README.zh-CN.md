# web-kit

[English](./README.md)

给 AI Agent 用的自托管 Web 访问服务：**搜索、读网页、爬取、下载**，全部由服务器上一个真实的、已登录的 Chrome 完成。Agent 只调用一个很小的 CLI（`webkit`），浏览器的活都在服务端。

```
 Agent ──► webkit CLI ──HTTP + key──►  后端容器（只开一个 API 端口）
                                        ├─ Caddy（鉴权）
                                        ├─ webkit-api ── Chrome（持久化配置，patchright）
                                        │                 └─ 读网页用 crawl4ai，下载走 CDP
                                        ├─ SearXNG（API 型引擎：github、pypi、openalex 等）
                                        └─ noVNC（管理员：手动登录或过一次验证码）
```

和普通元搜索或爬虫服务的区别：web-kit 在服务端保留**你自己的**浏览器身份（cookie、登录状态），用这个浏览器去抓 Google、DuckDuckGo、Google Scholar、Semantic Scholar、arXiv 这几个关键引擎，并逐个汇报每个引擎的结果，空结果不会含糊不清。

## 定位

**WebSearch 负责找，web-kit 负责读。** Agent 做一般检索仍用内置搜索；需要精确内容时用 web-kit：

| 需求 | 用什么 |
|---|---|
| 逐字读网页或 PDF（引文、数字、表格、论文全文），需要执行 JS 或登录才能看的页面 | `webkit read`（不用 WebFetch，它给的是模型摘要） |
| 登录后才能下载的文件 | `webkit download` |
| 找论文（CS / ML） | `webkit search -p academic`（Semantic Scholar、Google Scholar、OpenAlex、arXiv） |
| 一般网页搜索 | Agent 内置搜索；它不可用或额度用尽后改用 `webkit search` |
| 点击、输入、填表 | 不用 web-kit（那是浏览器自动化的活） |

部署上的限制：所有客户端共用一个 Chrome（`WEBKIT_MAX_PAGES`，默认 5 个页面，超出后返回退出码 7「忙」）；所有流量都从后端主机的出口出去，很多 Agent 同时大量搜索会让大家都更容易遇到验证码。所以大批量检索仍留给内置搜索。

## 客户端

```bash
uv tool install git+https://github.com/huangyrcn/web-kit     # 升级：uv tool upgrade web-kit
webkit config set url http://your-server:8082
webkit config set api-key            # 从标准输入读取 key
webkit doctor
webkit skill install                 # 生成与当前 CLI 版本一致的 ~/.claude/skills/web-kit/SKILL.md
```

```bash
webkit search "query"                          # 通用：google > duckduckgo > bing
webkit search -p academic --time year "query"  # semantic_scholar > google_scholar > openalex > arxiv
webkit search --read 3 -o out/ "query"         # 搜索后保存前 3 条结果的正文
webkit read https://arxiv.org/pdf/1706.03762   # PDF 直接返回文字
webkit crawl https://docs.example.com -o docs/ --max-pages 10
webkit download https://example.com/paper.pdf  # 默认存到 ~/.cache/web-kit/downloads/
webkit status                                  # 浏览器、出网、各引擎健康状况
```

`search` 输出的第一行汇报各引擎结果，例如 `# engines google=error:network duckduckgo=ok:8 bing=skipped`。
退出码：0 成功 · 1 用法错误 · 2 无结果 · 3 失败 · 4 认证 · 5 后端不可达 · 6 需要人工处理（验证码或登录）· 7 忙。错误以一行 JSON 输出到标准错误。

## 后端

```bash
cd backend
cp .env.example .env && chmod 600 .env      # key、端口、监听地址
cp searxng-settings/settings.yml.example searxng-settings/settings.yml
docker compose up -d --build
```

- `8082`：API（`X-API-Key`；`/v2/admin/*` 需要 `X-Admin-Key`）
- `6080`：noVNC，仅管理员使用（用户名 `webkit`，密码为管理 key）。在这里登录一次网站，cookie 会保存在 `chrome-profile` 卷里
- `9223`：原始 CDP，仅管理员使用

`webkit status` 会显示出网探测结果（对每个上游域名只做一次 TLS 握手，不消耗搜索次数）、浏览器状态，以及各引擎最近的成败。自愈机制：supervisord 负责重启进程；CDP 连接失效时 watchdog 重启 Chrome 和 API；容器健康检查覆盖 Chrome、API 和 SearXNG。

能力边界：Cloudflare Turnstile、DataDome / PerimeterX，以及必须使用住宅 IP 的站点，单个机房或家宽出口无法稳定访问。

许可证：MIT。
