# SearxNG 引擎列表

默认使用 Google。先直接运行 `ask-search "query"`；只有用户明确指定来源/引擎，或默认结果质量不足时，才用 `-e` 切换或升级引擎。

```bash
ask-search "query"                                      # 默认 Google
ask-search "query" -e google_scholar,semantic_scholar,openalex  # 学术升级（默认结果不足时）
ask-search "query" -e arxiv                             # 明确需要 arXiv 时
```

## 通用搜索

google, bing, duckduckgo

## 学术

默认先用 Google。升级时优先：google_scholar, semantic_scholar, openalex, dblp, pubmed。

arxiv 有限流风险，不放进默认学术升级组合；仅在用户明确要求 arXiv、已有 arXiv ID/题名、或需要验证 arXiv 来源时使用。

## 代码 / 包管理

github, github_code, gitlab, huggingface, npm, pypi, crates, lib_rs, pkg_go_dev, sourcehut, microsoft_learn, nvd

## 社区

hackernews, reddit, wikidata

## 图书

annas_archive, zlibrary
