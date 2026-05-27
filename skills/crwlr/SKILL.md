---
name: crwlr
description: >
  真实浏览器渲染的网页抓取工具，将任意网页转为干净 markdown。基于 crawl4ai，通过 CDP 连接远程 Chrome，
  支持 JS 渲染页面、反爬绕过、深度爬取、结构化数据提取。
  当内置 WebFetch / web_reader 无法正确读取页面（JS 渲染、反爬保护、内容不完整）时使用此 skill。
  也可用于 REST API 请求和页面内容提取。
  中文触发词："读一下这个网页"、"抓取这个页面"、"这个链接讲了什么"、"帮我看看这个URL"、
  "转成markdown"、"爬一下这个网站"、"提取这个页面的数据"。
  英文触发词："read this page", "scrape this page", "what does this URL say",
  "crawl this site", "extract data from this page", "convert to markdown",
  "what's on this page", "summarize this page"。
  简单的页面读取优先用内置工具；需要真实浏览器渲染、反爬绕过、或内置工具不可用时才用 crwlr。
argument-hint: 'crwlr crawl -o md "<url>" [-O file] [-q "question"] [-j "extract"]'
allowed-tools: Bash
---

# crwlr — 真实浏览器网页抓取

## 调用方式

```bash
# 跨平台（推荐）：
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -o md "<url>"

# Unix 上也可直接执行：
${SKILL_DIR}/scripts/crwlr crawl -o md "<url>"
```

需要 `uv`。环境变量 `CDP_URL` 必须配置（如 `http://localhost:9223`）。

## 速查

```bash
crwlr crawl -o md "<url>"           # 页面转 markdown
crwlr crawl -O out.md "<url>"       # 保存到文件
crwlr crawl -q "问题" "<url>"       # 对页面提问（需要 OPENAI_API_KEY）
crwlr crawl -j "提取" -o json "<url>"  # 结构化提取（需要 OPENAI_API_KEY）
crwlr crawl --deep-crawl bfs --max-pages 20 "<url>"  # 深度爬取
crwlr crawl -bc "<url>"             # 绕过缓存
crwlr --raw crawl "<url>"           # 使用本地 crwl CLI（需单独安装 crawl4ai）
```

## 输出格式

| 格式 | 用途 |
|------|------|
| `-o md` | 网页转 markdown（默认） |
| `-o json` | 含元数据的 JSON（url, title, content, links）|
| `-o md-fit` | 激进清理（去掉导航/广告/侧边栏）|

## API URL 请求

crwlr 也可直接请求 REST API，返回 JSON 包装在 markdown code block 中：

```bash
crwlr crawl -o md "https://api.openalex.org/works/doi:10.1093/bib/bbaf162"
crwlr crawl -o md "https://api.semanticscholar.org/graph/v1/paper/DOI:...?fields=paperId,title"
crwlr crawl -o md "https://api.crossref.org/works/10.1093/bib/bbaf162"
```

## 支持的 URL 类型

| 类型 | 示例 | 输出 |
|------|------|------|
| HTML 页面 | `https://arxiv.org/abs/2002.05287` | markdown |
| REST API | `https://api.openalex.org/works/...` | JSON in code block |
| PDF URL | `https://.../paper.pdf` | **不支持**（用 cdp-download 或 wget）|

## 无法直接抓取的页面

| 类型 | 原因 | 替代方案 |
|------|------|---------|
| Google Scholar | 需登录/JS交互 | 用 ask-search（SearxNG 聚合）|
| PubMed 页面 | Cloudflare 保护 | 用 PubMed E-utilities API |
| 需要登录的页面 | 认证问题 | 用 web-access skill |

## 注意事项

- URL 必须加引号
- 大页面用 `-O` 保存到文件，避免 stdout 截断
- `-q` 和 `-j` 需要 `OPENAI_API_KEY`（LLM 提取功能）
- `--raw` 模式需要单独安装 `crwl` CLI（`pip install crawl4ai`）

## References

| 文件 | 何时加载 |
|---|---|
| `references/workflow.md` | 需要多源对比、文献调研、深度爬取等高级工作流时 |
