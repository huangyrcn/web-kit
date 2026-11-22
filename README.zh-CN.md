# web-kit

[English](./README.md)

AI Agent 上网的兜底通道：Agent 内置的网页工具失败、被拦、额度用尽或做不到时，通过 Agent 所在机器上你自己的、已登录的 Chrome 完成**搜索、读网页、下载**。Agent 只调用一个很小的 CLI（`webkit`），它通过 [opencli](https://github.com/jackwener/opencli) 驱动这台 Chrome。

```
 Agent ──► webkit CLI ──► opencli 站点适配、`opencli web read` ──► OpenCLI 插件 ──► 你的 Chrome
             │             （搜索引擎、网页转 Markdown）                          （你的登录状态、你的 IP）
             └─► urllib（download：PDF 和其他文件直接下载，原样保存）
```

`webkit` 保持 Agent 依赖的约定：只读命令、告诉 Agent 下一步怎么做的退出码、逐个引擎汇报结果、回退链、有上限的输出，验证页永远不会当成正文返回。它只调用 opencli 的**只读**命令，而且只走适配这条路（先在空白页附着、复用常驻标签页、从不在页面里执行脚本）。服务器后端仍然可以用：`backend = "remote"`（见下文"远端模式"）。

## 定位

**先用内置工具，内置工具失败或做不到时再用 web-kit。** 内置搜索返回模型整理后的答案，通常更适合先用。以下情况交给 web-kit：

- 网页搜索额度用尽或报错；
- 读网页失败（403、429、超时、内容过大），或返回的是验证页、登录页而不是正文（验证页即使返回 HTTP 200 也算失败）；
- 需要逐字原文，而内置读取只给摘要（Claude 的 WebFetch）；
- 需要文件本身（通过网址、DOI 或 arXiv ID 获取论文 PDF）。

| 命令 | 输入 | 输出 | 用途 |
|---|---|---|---|
| `webkit search` | 查询词 | 链接和摘要列表，不是答案 | 找网址（不占 WebSearch 额度；可用 Scholar、Semantic Scholar、arXiv） |
| `webkit read` | 网址、DOI、`arXiv:ID` | 网页的 Markdown（链接写成 `[文字][N]`，地址列在末尾） | 了解网页写了什么；长页面只打印目录，全文存成文件 |
| `webkit download` | 网址、DOI、`arXiv:ID` | 存到本地的文件（只打印路径） | 获取文件（PDF 也走这里）；论文页会自动解析到它的 PDF；文件内容由 Agent 用自己的工具读 |

不适用：内置工具已经能做好的事、大批量检索、点击或填表。这台机器上的所有 Agent 共用你的 Chrome 和你的 IP，并发用得多会让你自己的账号更容易遇到验证码。

## 安装（本机模式，默认）

```bash
uv tool install git+https://github.com/huangyrcn/web-kit     # 升级：uv tool upgrade web-kit
npm install -g @jackwener/opencli    # 然后在 Chrome 里装 OpenCLI 插件（Chrome 应用商店）
opencli plugin install github:huangyrcn/web-kit   # web-kit 自己的适配：biorxiv、medrxiv
webkit config set profile main       # 可选：opencli 的 profile 别名（`opencli profile list`）
webkit config set s2-api-key         # 可选，从标准输入读取：Semantic Scholar API key
webkit doctor
webkit skill install                 # 生成与当前 CLI 版本一致的 ~/.claude/skills/web-kit/SKILL.md
webkit skill install --agent codex   # 生成 ~/.codex/skills/web-kit/（SKILL.md + reference.md）
```

```bash
webkit search "query"                          # 通用：google
webkit search -p academic "query"              # semantic_scholar > google_scholar > openalex > arxiv
webkit search --read 3 -o out/ "query"         # 搜索后保存前 3 条结果的正文
webkit read https://example.org/post           # 短页面直接打印；长页面打印目录并存成文件
webkit download https://example.com/paper.pdf  # 默认存到 ~/.cache/web-kit/downloads/
webkit download arXiv:2403.01092               # 论文页或 DOI 会解析到它的 PDF
webkit status                                  # opencli、Chrome 插件、web-kit 插件
```

`search` 输出的第一行汇报各引擎结果，例如 `# engines semantic_scholar=error:busy google_scholar=ok:8 openalex=skipped arxiv=skipped`。
退出码：0 成功 · 1 用法错误 · 2 无结果 · 3 失败（最多重试一次）· 4 认证 · 5 后端不可达 · 6 需要人工处理（验证码或登录，不要重试）· 7 忙（稍后重试）· 8 被封锁（网站拒绝这台服务器，不要重试，换来源）。错误以一行 JSON 输出到标准错误，其中的 `hint` 说明下一步怎么做。

输出有总量预算，正文不会大量涌入 agent 的上下文：每条命令的标准输出和标准错误合计最多 `--max-chars` 个字符（默认 4000，上限 20000）。较长的页面保存到 `~/.cache/web-kit/pages/`，只打印大小、路径、第一段和目录（各标题所在的行号）；用了 `-o` 就只打印路径；其余放不下的内容（长列表、大量错误）写入一个文件，最后一行给出路径；`--json` 只改变格式，不改变输出量。

分工：**opencli** 负责"网站"（每个站点一个适配：网址、页面结构、字段）；**webkit** 负责"Agent"（命令、引擎顺序、统一的结果格式、错误分类和退出码、输出量控制、PDF）。opencli 没有适配的站点，在本仓库的 `opencli-plugin/` 里写一个（站点名不和官方重名；上游有了官方适配就删掉我们的），再在 webkit 的引擎表里加一行。

本机模式说明：Chrome 要开着（否则退出码 5）。会往网页里嵌自己页面的扩展（沉浸式翻译、Zotero、Scholar PDF Reader）会让这些页面无法被自动化（错误 `browser_conflict`）。可用引擎见 `webkit engines`（本机模式没有时间过滤）。PDF 直接下载、不带浏览器 cookie，付费出版商的 PDF 会失败。`read -f html` 不可用。

## 远端模式（web-kit 服务端）

服务端就是放进容器里的本机模式：Chrome 加 OpenCLI 插件（用 Chrome 企业策略强制安装）、opencli 守护进程、web-kit 的 opencli 插件，以及 `webkit-serve`（`src/webkit/serve.py`）。`webkit-serve` 用同一份代码回答 `/v2` 接口，所以两种模式的结果、错误和退出码完全一致。

```bash
cp backend/.env.example backend/.env && chmod 600 backend/.env   # 监听地址、key
docker compose -f backend/docker-compose.yml up -d --build      # 构建上下文是仓库根目录
```

端口只绑定在可信客户端才能访问的地址上（例如主机的 tailnet 地址，`WEBKIT_BIND`），即使这样，每个接口仍然要 key。客户端：`webkit config set backend remote`、`webkit config set url http://your-server:8082`、`webkit config set api-key`（从标准输入读取），然后 `webkit doctor`。

- `8082`：API（`X-API-Key`；`/v2/admin/*` 需要 `X-Admin-Key`；`/healthz` 只回答正常或不正常）
- `6080`：noVNC，给人用（用户名 `webkit`，密码为管理 key）。在这里登录一次网站即可，profile 保存在 `chrome-data` 卷里。`download --wait-human` 会在这里打开页面。

固定版本的有：Chrome（`CHROME_VERSION`；换 Chrome 版本会让 profile 里所有 Cloudflare 通过凭证失效）、Node 和 opencli。OpenCLI 插件会从应用商店自动更新；如果新版插件和固定的 opencli 不兼容，`webkit doctor` 会显示没连上，这时用更新的 `OPENCLI_VERSION` 重新构建。任何进程退出，supervisord 都会把它重启。

能力边界：很多网站不信任机房出口（Google 可能给没登录的新 profile 返回跳转链接或 AI Mode；Scholar 和 DataDome 保护的网站可能直接拒绝）。通过 noVNC 登录能改善一部分，真正的解决办法是住宅出口。

许可证：MIT。
