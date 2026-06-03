# 高级工作流

## 1. 搜索 → 阅读 → 综合

```
ask-search "query" -n 5
  ↓ 看摘要，选 2-3 个最相关的 URL
crwlr crawl -O /tmp/page1.md "url1"
crwlr crawl -O /tmp/page2.md "url2"
  ↓ 对比多源内容
综合分析，给出结论
```

## 2. 搜索策略

### 可用的搜索引擎

ask-search 支持多种引擎，默认是 Google。可以按任务需要选择：

| 领域 | 引擎 |
|---|---|
| 通用 | google, bing, duckduckgo |
| 学术 | google_scholar, semantic_scholar, openalex, dblp, pubmed, arxiv |
| 代码 / 包管理 | github, github_code, gitlab, huggingface, npm, pypi, crates, lib_rs, pkg_go_dev, sourcehut, microsoft_learn, nvd |
| 社区 | hackernews, reddit, wikidata |
| 图书 | annas_archive, zlibrary |

### 典型搜索流程

一条实用的搜索流程是：

1. **先用默认搜索**：`ask-search "query"`  
   默认搜索已经覆盖论文聚合站、repo、博客、文档、论坛等广泛来源。
   如果结果已经足够，通常不需要再切换引擎。

2. **观察结果来自哪个领域**：  
   如果 top results 大量来自论文站点（arXiv、ACL、ACM、Semantic Scholar 等），
   说明任务可能偏学术；如果来自 GitHub/npm/PyPI，说明偏代码。

3. **按领域升级引擎**（如果需要）：
   - 学术 metadata（作者、年份、引用）：`-e google_scholar,semantic_scholar,openalex`
   - 代码 / 包管理：`-e github,npm,pypi`
   - 社区经验 / 踩坑 / 对比：`-e reddit,hackernews`
   - 新闻 / 政策 / 发布：`-c news`

4. **进一步定点**（如果领域引擎仍不够明确）：
   - 已知来源：`ask-search "query" -e arxiv`
   - 已知域名：`ask-search "query site:arxiv.org"`
   - 已知 repo / package：`-e github` / `-e npm`

### 一些可能有用的观察

- `-e` 改变的是搜索路径，不只是过滤默认结果。
- 如果默认结果已经有足够的领域来源，通常不需要再切引擎。
- arxiv 有时比较慢或容易 timeout；如果不需要 arXiv 作为明确来源，
  默认搜索或其它学术引擎往往就够了。
- `site:arxiv.org` 可以直接在任何引擎上限定域名。
- Google 时间语法：`after:2025`、`before:2024`、`2024..2025`。

## 3. 读取页面

```bash
crwlr crawl -O /tmp/page.md "url"    # 保存到文件（推荐）
crwlr crawl -o md "url"               # 输出到 stdout
```

## 4. 批量处理

```bash
i=0
for url in $(ask-search "query" -u -n 3); do
  i=$((i + 1))
  crwlr crawl -O "/tmp/page-${i}.md" "$url"
done
```

## 5. sub-agent prompt 模板

```
目标：[描述要调研什么]

步骤：
1. 用 ask-search 默认搜索，观察结果属于哪个领域
2. 如需要，按领域或具体来源再搜一次
3. 选 3-5 个最相关的 URL
4. 用 crwlr crawl -O /tmp/xxx.md 读取页面
5. 综合分析，给出结论
```
