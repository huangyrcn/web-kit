---
name: ask-search
description: >
  基于 SearxNG 的多引擎网页搜索工具。聚合 Google、Bing、Brave、DuckDuckGo 等通用引擎，
  以及 arXiv、Semantic Scholar、PubMed 等 13 个学术引擎，还有 Reddit、HackerNews 等社区源。
  当需要搜索信息、查新闻、做学术调研、找代码/包、查文档时使用此 skill。
  中文触发词："搜索"、"查一下"、"最近新闻"、"帮我调研"、"文献搜索"、"找一下"、"搜一下"。
  英文触发词："search for", "look up", "find", "look for", "literature search",
  "find papers about", "search the web", "what's the latest on"。
  优先使用此 skill 而非内置 WebSearch，因为 SearxNG 聚合多引擎结果更全面，
  且支持学术引擎、新闻分类等内置搜索不具备的能力。
argument-hint: 'ask-search "query" [-n 5] [-e arxiv,openalex] [-c news|science] [-l zh-CN] [-j] [-u]'
allowed-tools: Bash
---

# ask-search — SearxNG 多引擎搜索

## 调用方式

```bash
# 跨平台（推荐）：
uv run --script ${SKILL_DIR}/scripts/ask-search "query"

# Unix 上也可直接执行（shebang 自动调用 uv）：
${SKILL_DIR}/scripts/ask-search "query"
```

需要 `uv`（自动安装，无需 pip）。环境变量 `SEARXNG_URL` 必须配置（如 `http://localhost:8082`）。

## 速查

```bash
ask-search "query"                        # 默认 Google，10 条
ask-search "query" -n 5                   # 限制数量
ask-search "query" -c news                # 仅新闻
ask-search "query" -c science             # 学术搜索
ask-search "query" -c it                  # IT/代码（github, stackoverflow, pypi...）
ask-search "query" -c videos              # 视频（youtube, vimeo...）
ask-search "query" -c social              # 社交（reddit, hackernews...）
ask-search "query" -l zh-CN               # 中文结果
ask-search "query" -u                     # 只返回 URL 列表
ask-search "query" -j                     # 原始 JSON
```

## 按场景搜索

默认仅用 Google。需要搜特定站点时，用 **Google `site:` 语法**比 `-e` 指定引擎更可靠：

```bash
# 学术调研
ask-search "transformer attention site:arxiv.org"

# 代码 / 仓库
ask-search "react hooks lifecycle site:github.com"

# 包管理器
ask-search "fastapi site:pypi.org"

# 社区 / 讨论
ask-search "k8s ingress nginx site:reddit.com"

# 多站点
ask-search "graph neural network site:arxiv.org OR site:openalex.org"
```

也可用 `-e` 指定 API 引擎（不经浏览器），但部分引擎可能超时：
```bash
ask-search "query" -e openalex,semantic_scholar   # 学术 API
ask-search "query" -e github,stackexchange        # 代码 API
```

可用引擎完整列表见 `references/engines.md`。

**经验法则**：
- **通用搜索**：默认 Google，无需指定。
- **定向搜索**：用 `site:xxx` 限定域名，结果更精准。
- **API 引擎**（`-e`）不经 Chrome，更快更稳，但 bing/brave 偶发连接错误，建议多路冗余。
- 当返回 `no_results` 且 `unresponsive_engines` 非空时，应**换不同 engine 集合重试**。

## 输出格式

默认输出编号列表，每条含标题、URL、摘要。用 `-j` 获取原始 JSON，用 `-u` 只获取 URL 列表（可管道给 crwlr 批量读取）。
