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

要点：
- 搜索用 `-n 5` 少量结果快速筛选
- 阅读用 `-O` 保存到文件，避免 stdout 截断
- 多源交叉验证，不要只依赖一个来源

## 2. 学术文献调研

**优先用 Google `site:`，不要用 `-e arxiv`（arxiv API 有限流）：**

```bash
# 学术搜索（推荐）
ask-search "graph neural network domain adaptation 2024 site:arxiv.org" -n 10

# 多站点
ask-search "hypergraph self-supervised site:arxiv.org OR site:openalex.org" -n 10

# 读论文
crwlr crawl -O /tmp/paper.md "https://arxiv.org/abs/xxxx.xxxxx"
```

要点：
- `site:arxiv.org` 搜 arXiv，`site:openalex.org` 搜 OpenAlex
- arxiv 的 `/abs/` 是摘要，`/html/` 是全文 HTML
- `-e arxiv` 直接打 API 有限流，不推荐作为首选

## 3. 新闻追踪

```bash
ask-search "topic" -c news -n 10
crwlr crawl -O /tmp/news.md "news-url"
```

## 4. 搜索结果批量读取

```bash
ask-search "query" -u -n 5    # 只拿 URL 列表
# 逐个读取保存
for url in $(ask-search "query" -u -n 3); do
  crwlr crawl -O /tmp/page.md "$url"
done
```

## 5. sub-agent prompt 模板

```
目标：[描述要调研什么]

步骤：
1. 用 ask-search "xxx site:arxiv.org" 搜索论文
2. 从结果中选 3-5 个最相关的 URL
3. 用 crwlr crawl -O /tmp/xxx.md 读取页面
4. 综合多源信息，给出分析结论
```
