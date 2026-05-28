---
name: ask-search
description: >
  通用多引擎搜索工具，基于 SearxNG 聚合 20+ 搜索引擎。
  支持通用搜索（Google、Bing）、学术搜索（Google Scholar、Semantic Scholar、OpenAlex）、
  新闻、IT/代码（GitHub、StackOverflow）、社交（Reddit、HackerNews）等。
  当需要搜索信息、查新闻、做学术调研、找代码/包、查文档时使用此 skill。
  中文触发词："搜索"、"查一下"、"最近新闻"、"帮我调研"、"文献搜索"、"找一下"、"搜一下"。
  英文触发词："search for", "look up", "find", "look for", "literature search",
  "find papers about", "search the web", "what's the latest on"。
argument-hint: 'ask-search "query" [-n 5] [-e engine] [-c category] [-l lang] [-u]'
allowed-tools: Bash
---

# ask-search — 通用多引擎搜索

基于 SearxNG，聚合 20+ 搜索引擎。按场景选引擎。

## 调用方式

```bash
uv run --script ${SKILL_DIR}/scripts/ask-search "query"
```

## 速查

```bash
ask-search "query"                              # 默认 Google
ask-search "query" -n 5                         # 限制数量
ask-search "query" -l zh-CN                     # 中文
ask-search "query" -u                           # 只返回 URL
```

## 按场景选引擎

```bash
# 通用搜索（默认 Google）
ask-search "query"

# 学术论文
ask-search "transformer attention" -e google_scholar,semantic_scholar,openalex

# 限定站点
ask-search "RAG site:arxiv.org"
ask-search "react hooks site:github.com"

# 限定时间（Google 语法）
ask-search "LLM agent after:2025"

# 新闻
ask-search "AI regulation" -c news

# IT / 代码
ask-search "fastapi middleware" -e github,stackoverflow

# 社区讨论
ask-search "k8s ingress" -e reddit,hackernews
```

## 可用引擎

| 场景 | 推荐引擎 | 备注 |
|------|---------|------|
| 通用 | google, bing | 默认 google |
| 学术 | google_scholar, semantic_scholar, openalex | 避免 `-e arxiv`（有限流） |
| 新闻 | -c news（自动选新闻引擎） | |
| IT | github, stackoverflow, npm, pypi | |
| 社交 | reddit, hackernews | |

完整引擎列表见 `references/engines.md`。
