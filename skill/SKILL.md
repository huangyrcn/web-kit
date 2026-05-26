---
name: web-kit
description: >
  优先使用此 skill 处理所有网页相关操作：搜索、阅读、下载。
  三大能力：(1) ask-search — 基于 SearxNG 的网页搜索，聚合 8 个通用引擎 + 13 个学术引擎 + 7 个新闻源等；
  (2) crwlr — 基于 crawl4ai 的网页抓取，真实远程浏览器渲染 JS，输出干净的 markdown；
  (3) cdp-download — 通过 CDP 下载文件（仅当 wget/curl 失败时使用，如需要浏览器认证）。
  触发场景：搜索信息、查新闻、学术文献调研、读网页内容、URL 转 markdown、下载 PDF、提取结构化数据、
  深度爬取站点、读取 JS 动态渲染页面、询问页面相关问题。
  中文触发词："搜索"、"查一下"、"最近新闻"、"读一下这个网页"、"抓取这个页面"、
  "这个链接讲了什么"、"帮我看看这个URL"、"转成markdown"、"爬一下这个网站"、
  "帮我调研"、"文献搜索"、"下载PDF"。
  英文触发词："search for", "look up", "read this page", "scrape this page",
  "what does this URL say", "crawl this site", "extract data from this page",
  "literature search", "find papers about", "download PDF"。
  相比 web-access 等重型浏览器工具，web-kit 更轻量（一条命令出结果），
  仅在需要登录态、页面交互、点击按钮等场景下才应降级到 web-access。
argument-hint: "ask-search <query> | crwlr crawl <url> | wget <url> || cdp-download <url>"
allowed-tools: Bash
---

# web-kit — 网页搜索 + 阅读 + 下载

## 脚本路径

本 skill 随附的脚本位于 `scripts/` 目录，调用时需使用完整路径：

```bash
${SKILL_DIR}/scripts/ask-search "query"
${SKILL_DIR}/scripts/crwlr crawl -o md "<url>"
${SKILL_DIR}/scripts/cdp-download <url> [output]
```

或相对于 skill 目录：
```bash
scripts/ask-search "query"
scripts/crwlr crawl -o md "<url>"
scripts/cdp-download <url> output.pdf
```

Python 脚本（`ask-search`、`crwlr`、`cdp-download`）通过 [`uv run --script`](https://docs.astral.sh/uv/guides/scripts/)
执行，依赖声明在脚本头部（PEP 723），首次运行时 `uv` 自动安装。系统需预装 `uv`。

---

## 快速参考

| 场景 | 命令 |
|---|---|
| 搜索 | `${SKILL_DIR}/scripts/ask-search "query"` |
| 读网页 | `${SKILL_DIR}/scripts/crwlr crawl -o md "<url>"` |
| 下载文件 | `wget <url>` 或 `curl -O <url>` |
| 下载文件（认证失败时） | `${SKILL_DIR}/scripts/cdp-download <url> [output]` |

典型流程：`ask-search` 找 URL → `crwlr` 读全文 → `wget` 下载（失败则用 `cdp-download`）。

---

## ask-search 速查

```bash
${SKILL_DIR}/scripts/ask-search "query"                        # 默认 10 条（仅 google）
${SKILL_DIR}/scripts/ask-search "query" -n 5                   # 限制数量
${SKILL_DIR}/scripts/ask-search "query" -c news                # 仅新闻
${SKILL_DIR}/scripts/ask-search "query" -c science             # 学术搜索（arxiv, scholar, pubmed 等）
${SKILL_DIR}/scripts/ask-search "query" -l zh-CN               # 中文结果
${SKILL_DIR}/scripts/ask-search "query" -u                     # 只返回 URL 列表
${SKILL_DIR}/scripts/ask-search "query" -j                     # 原始 JSON
```

### 按场景搜索（`site:` 用法）

默认仅用 Google。
需要搜特定站点时，用 **Google `site:` 语法**比 `-e` 指定引擎更可靠：

```bash
# 学术调研
${SKILL_DIR}/scripts/ask-search "transformer attention site:arxiv.org"

# 代码 / 仓库
${SKILL_DIR}/scripts/ask-search "react hooks lifecycle site:github.com"

# 包管理器
${SKILL_DIR}/scripts/ask-search "fastapi site:pypi.org"

# 社区 / 讨论
${SKILL_DIR}/scripts/ask-search "k8s ingress nginx site:reddit.com"

# 多站点
${SKILL_DIR}/scripts/ask-search "graph neural network site:arxiv.org OR site:openalex.org"
```

也可用 `-e` 指定 API 引擎（不经浏览器），但部分引擎可能超时：
```bash
${SKILL_DIR}/scripts/ask-search "query" -e openalex,semantic_scholar   # 学术 API
${SKILL_DIR}/scripts/ask-search "query" -e github,stackexchange        # 代码 API
```

可用引擎完整列表见 `references/engines.md`。

**经验法则**：
- **通用搜索**：默认 Google，无需指定。
- **定向搜索**：用 `site:xxx` 限定域名，结果更精准且不依赖其他引擎。
- **多站点学术搜索**：`site:arxiv.org OR site:openalex.org` 一次搜多个。
- 当 `ask-search` 返回 `no_results` 且 `unresponsive_engines` 非空时，调用方应该
**换不同 engine 集合重试**，而不是认为"没匹配"。

环境变量：`SEARXNG_URL`（必须配置，如 `http://localhost:8082`）

## crwlr 速查

```bash
${SKILL_DIR}/scripts/crwlr crawl -o md "<url>"           # 页面转 markdown
${SKILL_DIR}/scripts/crwlr crawl -O out.md "<url>"       # 保存到文件
${SKILL_DIR}/scripts/crwlr crawl -q "问题" "<url>"       # 对页面提问
${SKILL_DIR}/scripts/crwlr crawl -j "提取" -o json "<url>"  # 结构化提取
${SKILL_DIR}/scripts/crwlr crawl --deep-crawl bfs --max-pages 20 "<url>"  # 深度爬取
${SKILL_DIR}/scripts/crwlr crawl -bc "<url>"             # 绕过缓存
${SKILL_DIR}/scripts/crwlr --raw crawl "<url>"           # 使用本地浏览器
```

输出格式：`md`(markdown) | `md-fit`(激进清理) | `json`(含元数据) | `all`(全部)

环境变量：`CDP_URL`（必须配置，如 `http://localhost:9223`）

### crwlr 进阶用法

#### API URL 请求

crwlr 不仅可以抓取网页，还可以直接请求 REST API：

```bash
# OpenAlex API
${SKILL_DIR}/scripts/crwlr crawl -o md "https://api.openalex.org/works/doi:10.1093/bib/bbaf162"

# Semantic Scholar API
${SKILL_DIR}/scripts/crwlr crawl -o md "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1093/bib/bbaf162?fields=paperId,title,openAccessPdf"

# Crossref API
${SKILL_DIR}/scripts/crwlr crawl -o md "https://api.crossref.org/works/10.1093/bib/bbaf162"

# DBLP API
${SKILL_DIR}/scripts/crwlr crawl -o md "https://dblp.org/search/api?q=paper+title"

# PubMed E-utilities API
${SKILL_DIR}/scripts/crwlr crawl -o md "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=40220278&retmode=json"
```

**注意**：API 返回的 JSON 会被包装在 markdown code block 中：

````markdown
```
{"id":"...","title":"...","authors":[...]}
```
````

#### 输出格式对比

| 格式 | 用途 |
|------|------|
| `-o md` | 网页转 markdown（默认），API URL 也会正常工作 |
| `-o json` | 含元数据的 JSON（url, title, content, links 等）|
| `-o md-fit` | 激进清理（去掉导航/广告/侧边栏）|

#### 支持的 URL 类型

| 类型 | 示例 | 输出 |
|------|------|------|
| HTML 页面 | `https://arxiv.org/abs/2002.05287` | markdown |
| REST API | `https://api.openalex.org/works/...` | JSON in code block |
| PDF URL | `https://.../paper.pdf` | **不支持**（用 wget 或 cdp-download）|

#### 无法直接抓取的页面

| 类型 | 原因 | 替代方案 |
|------|------|---------|
| Google Scholar | 需登录/JS交互 | 用 `ask-search`（SearxNG 聚合）|
| PubMed 页面 | Cloudflare 保护 | 用 PubMed E-utilities API |
| 需要登录的页面 | 认证问题 | 用 web-access skill |

## cdp-download 速查

通过 CDP 下载文件（PDF 等），使用 `Network.loadNetworkResource` + `IO.read`。
仅当 `wget` 或 `curl` 下载失败时使用（如需要浏览器认证/cookie）。

```bash
${SKILL_DIR}/scripts/cdp-download <url>                   # 下载到当前目录（自动命名）
${SKILL_DIR}/scripts/cdp-download <url> output.pdf       # 下载到指定文件
```

环境变量：`CDP_URL`（必须配置）

**优势**：
- 支持认证/cookie（`includeCredentials: true`）
- 流式读取，适合大文件
- 不受 PDF viewer 插件干扰

## Scripts 索引

| 脚本 | 用途 |
|---|---|
| `scripts/ask-search` | SearxNG 多引擎搜索 |
| `scripts/crwlr` | 渲染页面、提取内容 |
| `scripts/cdp-download` | 通过 CDP 下载文件 |

**必须配置的环境变量**：
- `CDP_URL` — Chrome DevTools Protocol endpoint（如 `http://localhost:9223`）
- `SEARXNG_URL` — SearxNG 搜索引擎 endpoint（如 `http://localhost:8082`）

## 注意事项

- URL 必须加引号
- 大页面用 `-O` 保存到文件
- 脚本路径：使用 `${SKILL_DIR}/scripts/` 前缀调用随附脚本

## References 索引

| 文件 | 何时加载 |
|---|---|
| `references/engines.md` | 需要了解具体支持哪些搜索引擎、学术引擎、分类时 |
| `references/workflow.md` | 需要多源对比、文献调研、深度爬取、sub-agent 分派等高级工作流时 |
