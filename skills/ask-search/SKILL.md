---
name: ask-search
description: >
  基于 SearxNG 的 Google 搜索工具。默认走 Google，支持 `site:` 语法限定站点搜索。
  学术搜索用 `site:arxiv.org`，代码搜索用 `site:github.com`，比指定学术 API 引擎更可靠。
  当需要搜索信息、查新闻、做学术调研、找代码/包、查文档时使用此 skill。
  中文触发词："搜索"、"查一下"、"最近新闻"、"帮我调研"、"文献搜索"、"找一下"、"搜一下"。
  英文触发词："search for", "look up", "find", "look for", "literature search",
  "find papers about", "search the web", "what's the latest on"。
argument-hint: 'ask-search "query" [-n 5] [-e engine] [-c news|science] [-l zh-CN] [-u]'
allowed-tools: Bash
---

# ask-search — SearxNG 搜索

默认走 Google，用 `site:` 限定站点。

## 调用方式

```bash
uv run --script ${SKILL_DIR}/scripts/ask-search "query"
```

## 速查

```bash
ask-search "query"                                    # Google，默认 10 条
ask-search "query" -n 5                               # 限制数量
ask-search "query" -l zh-CN                           # 中文结果
ask-search "query" -u                                 # 只返回 URL 列表
```

## 按场景搜索

**优先用 Google `site:` 语法**，比 `-e` 指定 API 引擎更可靠：

```bash
# 学术论文
ask-search "transformer attention site:arxiv.org"

# 代码 / 仓库
ask-search "react hooks lifecycle site:github.com"

# 包管理器
ask-search "fastapi site:pypi.org"

# 社区讨论
ask-search "k8s ingress nginx site:reddit.com"

# 多站点
ask-search "graph neural network site:arxiv.org OR site:openalex.org"

# 新闻
ask-search "AI regulation" -c news
```

也可用 `-e` 指定 API 引擎（不经浏览器），但部分引擎可能超时：
```bash
ask-search "query" -e openalex,semantic_scholar   # 学术 API（可能慢）
ask-search "query" -e github,stackexchange        # 代码 API
```

可用引擎列表见 `references/engines.md`。

**经验法则**：默认 Google + `site:` 就够了。`-e` 是备选，不要作为首选。
