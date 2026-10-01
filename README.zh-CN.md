# web-kit

[English](./README.md)

AI Agent 上网的兜底通道：Agent 内置的网页工具失败、被拦、额度用尽或做不到时，通过服务器上一个真实的、已登录的 Chrome 完成**搜索、读网页、爬取、下载**。Agent 只调用一个很小的 CLI（`webkit`），浏览器的活都在服务端。

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

**先用内置工具，内置工具失败或做不到时再用 web-kit。** 内置搜索返回模型整理后的答案，通常更适合先用。以下情况交给 web-kit：

- 网页搜索额度用尽或报错；
- 读网页失败（403、429、超时、内容过大），或返回的是验证页、登录页而不是正文（验证页即使返回 HTTP 200 也算失败）；
- 需要逐字原文，而内置读取只给摘要（Claude 的 WebFetch）；
- 需要文件本身（通过网址、DOI 或 arXiv ID 获取论文 PDF）。

| 命令 | 输入 | 输出 | 用途 |
|---|---|---|---|
| `webkit search` | 查询词 | 链接和摘要列表，不是答案 | 找网址（不占 WebSearch 额度；可用 Scholar、Semantic Scholar、arXiv） |
| `webkit read` | 网址、DOI、`arXiv:ID` | 网页或 PDF 的文本 | 了解内容写了什么（`--pdf`：读论文页对应的 PDF） |
| `webkit download` | 网址、DOI、`arXiv:ID` | 存到本地的文件（只打印路径） | 保存或交付文件；论文页会自动解析到它的 PDF |

不适用：内置工具已经能做好的事、大批量检索、点击或填表。所有客户端共用一个 Chrome（`WEBKIT_MAX_PAGES`，默认 5 个页面，超出后返回退出码 7「忙」），所有流量都从后端主机的出口出去，并发用得多会让大家都更容易遇到验证码。

## 客户端

```bash
uv tool install git+https://github.com/huangyrcn/web-kit     # 升级：uv tool upgrade web-kit
webkit config set url http://your-server:8082
webkit config set api-key            # 从标准输入读取 key
webkit doctor
webkit skill install                 # 生成与当前 CLI 版本一致的 ~/.claude/skills/web-kit/SKILL.md
webkit skill install --agent codex   # 生成 ~/.codex/skills/web-kit/（SKILL.md + reference.md）
```

```bash
webkit search "query"                          # 通用：google > duckduckgo > bing
webkit search -p academic --time year "query"  # semantic_scholar > google_scholar > openalex > arxiv
webkit search --read 3 -o out/ "query"         # 搜索后保存前 3 条结果的正文
webkit read https://arxiv.org/pdf/1706.03762   # PDF 直接返回文字
webkit read --pdf -o paper.md 10.1145/3774904.3792101   # DOI -> 论文页 -> 它的 PDF，返回文字
webkit crawl https://docs.example.com -o docs/ --max-pages 10
webkit download https://example.com/paper.pdf  # 默认存到 ~/.cache/web-kit/downloads/
webkit download arXiv:2403.01092               # 论文页或 DOI 会解析到它的 PDF
webkit status                                  # 浏览器、出网、各引擎健康状况
```

`search` 输出的第一行汇报各引擎结果，例如 `# engines google=error:network duckduckgo=ok:8 bing=skipped`。
退出码：0 成功 · 1 用法错误 · 2 无结果 · 3 失败（最多重试一次）· 4 认证 · 5 后端不可达 · 6 需要人工处理（验证码或登录，不要重试）· 7 忙（稍后重试）· 8 被封锁（网站拒绝这台服务器，不要重试，换来源）。错误以一行 JSON 输出到标准错误，其中的 `hint` 说明下一步怎么做。

输出有总量预算，正文不会大量涌入 agent 的上下文：每条命令的标准输出和标准错误合计最多 `--max-chars` 个字符（默认 4000，上限 20000）。较长的页面保存到 `~/.cache/web-kit/pages/`，只打印预览和文件路径；用了 `-o` 就只打印路径；其余放不下的内容（长列表、大量错误）写入一个文件，最后一行给出路径；`--json` 只改变格式，不改变输出量。

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

Chrome 版本是固定的（Dockerfile 里的 `CHROME_VERSION`）：浏览器一升级，profile 里所有 Cloudflare 通过凭证都会失效，所以要有意识地升级。

`webkit status` 会显示出网探测结果（对每个上游域名只做一次 TLS 握手，不消耗搜索次数）、浏览器状态，以及各引擎最近的成败。自愈机制：supervisord 负责重启进程；CDP 连接失效时 watchdog 重启 Chrome 和 API；容器健康检查覆盖 Chrome、API 和 SearXNG。

能力边界：从机房出口访问时，DataDome 保护的站点（如 ResearchGate）会直接拒绝这台服务器（退出码 8）；Cloudflare 保护的出版商在通行凭证过期后（约 30 分钟）会再次要求人工验证，所以 noVNC 里过一次验证只在有人值守时有用。真正的解决办法只有住宅出口。

许可证：MIT。
