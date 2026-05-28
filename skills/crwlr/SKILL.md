---
name: crwlr
description: >
  用 Chrome 浏览器获取网页内容（执行 JS，绕过反爬）。当 curl/wget/WebFetch 被拦截时使用。
  中文触发词："读一下这个网页"、"抓取这个页面"、"网页被拦截了"。
  英文触发词："read this page", "fetch this page", "page blocked", "cloudflare"。
argument-hint: 'crwlr crawl -O <file> "<url>"'
allowed-tools: Bash
---

# crwlr

用 Chrome 浏览器获取网页内容，转为 markdown。当 curl/wget/WebFetch 被拦截时使用。

```bash
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -O out.md "<url>"
```

环境变量：`CDP_URL`（必需）。
