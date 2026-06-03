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

先用默认 Google；只有默认结果质量不足，或用户明确指定来源/引擎时，才用 `-e` 升级。

```bash
# 通用搜索 / 默认第一跳
ask-search "query"

# 学术论文：第一跳仍然用默认 Google
ask-search "graph neural network paper 2025"

# 学术论文：默认结果不足后再升级到稳定学术源
ask-search "graph neural network paper 2025" -e google_scholar,semantic_scholar,openalex

# 学术 + 限定站点（通常比直接打 arXiv engine 稳）
ask-search "hypergraph self-supervised site:arxiv.org"

# 明确需要 arXiv 时才用 arXiv engine
ask-search "known paper title or arXiv id" -e arxiv

# 限定时间
ask-search "LLM agent after:2025"

# IT / 代码：默认结果不足或用户明确要 repo/package 时升级
ask-search "fastapi middleware" -e github,pypi

# 新闻
ask-search "AI regulation" -c news

# 社区讨论
ask-search "k8s ingress" -e reddit,hackernews
```

要点：
- `-e` 是升级/约束参数，不是默认入口。
- 学术搜索第一跳也优先默认 Google；Google 通常能发现 arXiv、ACL、ACM、OpenReview、Semantic Scholar 和项目页。
- 默认学术升级用 `-e google_scholar,semantic_scholar,openalex`。
- 不要把 `arxiv` 放进默认学术组合（有限流）；需要 arXiv 时优先用 `site:arxiv.org`，或在明确需要时单独 `-e arxiv`。
- `site:` 限定域名可以在任何引擎上用。
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
1. 用 ask-search 默认搜索；只有结果不足或用户明确指定来源时才加 -e
2. 选 3-5 个最相关的 URL
3. 用 crwlr crawl -O /tmp/xxx.md 读取页面
4. 综合分析，给出结论
```
