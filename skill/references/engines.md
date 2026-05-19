# SearxNG 引擎分类表

当前 SearxNG 实例启用的引擎，按用途分类。

搜索时用 `--engines` 指定引擎，用 `--categories` 指定分类。

示例：
```bash
ask-search "query" --categories science    # 只搜学术引擎
ask-search "query" --engines google,bing   # 只用指定引擎
ask-search "query" --categories news       # 只搜新闻
```

## 通用搜索 (general/web)

| 引擎 | shortcut | 说明 |
|---|---|---|
| bing | bi | 微软 Bing |
| brave | br | Brave Search |
| duckduckgo | ddg | DuckDuckGo |
| google | go | Google |
| mojeek | mj | 英国独立搜索引擎，无追踪 |
| qwant | qw | 法国搜索引擎，注重隐私 |
| startpage | sp | Google 结果代理，匿名 |
| yahoo | yh | Yahoo 搜索 |
| wikipedia | wp | 维基百科 |
| wikidata | wd | 维基数据 |
| stackexchange | st | Stack Exchange 全站 |

## 学术 (science / scientific publications)

| 引擎 | shortcut | 说明 |
|---|---|---|
| arxiv | ax | arXiv 预印本 |
| base | - | Bielefeld 学术搜索引擎 |
| core | - | 开放获取论文 |
| crossref | - | DOI 元数据 |
| dblp | - | 计算机科学文献 |
| doaj | - | 开放获取期刊 |
| google scholar | gsc | Google Scholar |
| openairedatasets | - | OpenAIRE 数据集 |
| openairepublications | - | OpenAIRE 论文 |
| openalex | - | OpenAlex 学术图谱 |
| pubmed | pm | PubMed 生物医学 |
| semantic scholar | se | Semantic Scholar（API 版本） |

## 新闻 (news)

| 引擎 | shortcut |
|---|---|
| bing news | bin |
| brave.news | brn |
| duckduckgo news | ddn |
| google news | gon |
| qwant news | qwn |
| reuters | re |
| wikinews | wn |
| yahoo news | yhn |

## IT / 代码 (it)

| 引擎 | shortcut | 说明 |
|---|---|---|
| github | gh | GitHub 仓库 |
| gitlab | gl | GitLab 仓库 |
| huggingface | hf | HuggingFace 模型 |
| stackoverflow | st | Stack Overflow |
| askubuntu | - | Ask Ubuntu |
| superuser | - | Super User |
| mdn | - | MDN Web Docs |
| microsoft learn | - | 微软文档 |
| npm | - | npm 包 |
| pypi | - | PyPI 包 |
| crates.io | - | Rust crates |
| docker hub | - | Docker 镜像 |
| pkg.go.dev | - | Go 包 |
| lib.rs | - | Rust 包 |

## 视频 (videos)

| 引擎 | shortcut |
|---|---|
| youtube | yt |
| dailymotion | dm |
| vimeo | vm |
| bing videos | biv |
| brave.videos | brv |
| duckduckgo videos | ddv |
| google videos | gov |
| qwant videos | qwv |

## 图片 (images)

| 引擎 | shortcut |
|---|---|
| bing images | bii |
| brave.images | bri |
| duckduckgo images | ddi |
| google images | goi |
| qwant images | qwi |
| startpage images | spi |
| deviantart | da |
| flickr | fl |
| unsplash | us |
| pexels | px |
| pinterest | pi |

## 社交媒体 (social media)

| 引擎 | shortcut |
|---|---|
| reddit | re |
| hackernews | hn |
| lemmy posts | - | Lemmy 帖子 |
| lemmy comments | - | Lemmy 评论 |
| mastodon hashtags | - | Mastodon 话题 |
| tootfinder | - | Mastodon 用户搜索 |

## 文件/书籍 (files/books)

| 引擎 | shortcut | 说明 |
|---|---|---|
| annas archive | aa | Anna's Archive |
| zlibrary | zl | Z-Library |
| openlibrary | ol | Open Library |
