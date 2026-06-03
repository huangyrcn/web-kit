# SearxNG 引擎列表

ask-search 支持以下引擎，默认是 Google。

```bash
ask-search "query"                                      # 默认 Google
ask-search "query" -e google_scholar,semantic_scholar,openalex  # 学术引擎组合
ask-search "query" -e arxiv                             # arXiv 单独使用
```

## 通用

google, bing, duckduckgo

## 学术

google_scholar, semantic_scholar, openalex, dblp, pubmed, arxiv

- `google_scholar`, `semantic_scholar`, `openalex` 常用于学术 metadata（作者、年份、引用、 venue）。
- `arxiv` 有时会较慢或 timeout，如果不需要 arXiv 作为明确来源，其它学术引擎或默认搜索通常足够。

## 代码 / 包管理

github, github_code, gitlab, huggingface, npm, pypi, crates, lib_rs, pkg_go_dev, sourcehut, microsoft_learn, nvd

## 社区

hackernews, reddit, wikidata

## 图书

annas_archive, zlibrary
