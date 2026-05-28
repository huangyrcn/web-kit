# SearxNG 引擎分类表

当前 SearxNG 实例启用的引擎，按实现方式分类。

## Chrome 依赖 (经 search-proxy)

这些引擎需要 Chrome 渲染，延迟较高。

| 引擎 | shortcut | search-proxy 端点 | 说明 |
|------|----------|-------------------|------|
| google | go | `/google` | Google 通用搜索 |
| bing | bi | `/bing` | Bing 通用搜索 |
| duckduckgo | dd | `/ddg` | DuckDuckGo |
| arxiv | arx | `/arxiv` | arXiv 预印本 |
| google scholar | gos | `/google_scholar` | Google Scholar |

## API 直连 (无 Chrome 依赖)

这些引擎直接调用外部 API，延迟低，推荐用于快速搜索。

| 引擎 | shortcut | 说明 |
|------|----------|------|
| semantic scholar | se | Semantic Scholar (带 API key) |
| openalex | - | OpenAlex 学术图谱 |
| dblp | - | 计算机科学文献 |
| pubmed | pm | PubMed 生物医学 |
| github | gh | GitHub 仓库 |
| github code | ghc | GitHub 代码搜索 (带 token) |
| gitlab | gl | GitLab 仓库 |
| hackernews | hn | Hacker News |
| huggingface | hf | HuggingFace 模型/数据集 |
| npm | - | npm 包 |
| crates.io | - | Rust crates |
| lib.rs | - | Rust 包 (lib.rs) |
| pkg.go.dev | - | Go 包 |
| sourcehut | - | SourceHut |
| microsoft learn | - | 微软文档 |
| nvd | - | NVD 漏洞数据库 |
| pypi | - | PyPI 包 |
| wikidata | wd | Wikidata |
| reddit | re | Reddit (当前 access denied) |

## 页面抓取 (原生，无 Chrome)

| 引擎 | shortcut | 说明 |
|------|----------|------|
| annas archive | aa | Anna's Archive |
| zlibrary | zlib | Z-Library |

## 已禁用

base, crossref, core, doaj, pdbe, openairedatasets, openairepublications,
brave, startpage, qwant, mojeek, yahoo, yandex, wikipedia, openlibrary, stackexchange

---

使用示例：

```bash
# 默认 (Google)
ask-search "query"

# 快速学术搜索 (无 Chrome)
ask-search "query" -e openalex,semantic_scholar,dblp

# 多引擎通用搜索 (Chrome, 较慢)
ask-search "query" -e google,bing,duckduckgo

# 指定分类
ask-search "query" -c science
```
