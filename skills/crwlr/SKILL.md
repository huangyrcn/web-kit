---
name: crwlr
description: >
  备选网页获取工具：当 curl / wget / WebFetch 被反爬拦截或无法获取完整内容时，用真实 Chrome 浏览器重试。
  通过 CDP 连接远程 Chrome 执行 JavaScript，能绕过 Cloudflare 等反爬保护、获取 SPA 动态内容。
  **仅当常规工具失败时使用**（返回空白页、403/429、Cloudflare challenge、内容不完整）。
  中文触发词："网页被拦截了"、"curl 抓不到"、"换个方式读这个页面"。
  英文触发词："page blocked", "fetch failed", "cloudflare", "403 forbidden"。
  优先用 curl / wget / WebFetch；被拦截时才用 crwlr 重试。
argument-hint: 'crwlr crawl -O <file> "<url>"'
allowed-tools: Bash
---

# crwlr — 被拦截时的备选网页获取

当 curl / wget / WebFetch 被反爬拦截时，用真实 Chrome 浏览器重试获取页面内容。

**使用原则：先试常规工具，失败了才用 crwlr。**

## 调用方式

```bash
# 推荐：保存到文件
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -O out.md "<url>"

# 输出到 stdout
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -o md "<url>"
```

需要 `uv`。环境变量 `CDP_URL` 必须配置（如 `http://localhost:9223`）。

## 速查

```bash
# 获取页面保存到文件（推荐）
crwlr crawl -O out.md "<url>"

# 深度爬取
crwlr crawl --deep-crawl bfs --max-depth 20 -O out.md "<url>"

# 绕过缓存
crwlr crawl -bc -O out.md "<url>"
```

## 什么时候用 crwlr

| 情况 | 用什么 |
|------|--------|
| 普通网页 | curl / wget / WebFetch（优先） |
| 返回 403 / 429 / Cloudflare challenge | crwlr（真实浏览器绕过） |
| SPA / JS 渲染页面内容为空 | crwlr（执行 JavaScript） |
| 内容不完整（缺动态加载部分） | crwlr |
| PDF 文件 | cdp-download 或 wget |

## 输出格式

| 格式 | 用途 |
|------|------|
| `-o md` | 网页转 markdown（默认） |
| `-o json` | 含元数据的 JSON（url, title, content, links）|
| `-o md-fit` | 激进清理（去掉导航/广告/侧边栏）|

## 注意事项

- URL 必须加引号
- **推荐用 `-O` 保存到文件**，避免 stdout 截断大页面
- `--raw` 模式需要单独安装 `crwl` CLI（`pip install crawl4ai`）

## References

| 文件 | 何时加载 |
|---|---|
| `references/workflow.md` | 需要多源对比、文献调研等高级工作流时 |
