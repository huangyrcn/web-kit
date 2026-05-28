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

按任务选引擎，不要只用 Google：

```bash
# 通用搜索
ask-search "query"

# 学术论文（多个学术引擎一起搜）
ask-search "graph neural network" -e google_scholar,semantic_scholar,openalex

# 学术 + 限定站点
ask-search "hypergraph self-supervised site:arxiv.org"

# 限定时间
ask-search "LLM agent after:2025" -e google_scholar

# IT / 代码
ask-search "fastapi middleware" -e github,stackoverflow

# 新闻
ask-search "AI regulation" -c news

# 社区讨论
ask-search "k8s ingress" -e reddit,hackernews
```

要点：
- 学术搜索用 `-e google_scholar,semantic_scholar,openalex`，不要用 `-e arxiv`（有限流）
- `site:` 限定域名可以在任何引擎上用
- Google 时间语法：`after:2025`、`before:2024`、`2024..2025`

## 3. 读取页面

```bash
crwlr crawl -O /tmp/page.md "url"    # 保存到文件（推荐）
crwlr crawl -o md "url"               # 输出到 stdout
```

## 4. 批量处理

```bash
for url in $(ask-search "query" -u -n 3); do
  crwlr crawl -O /tmp/page.md "$url"
done
```

## 5. sub-agent prompt 模板

```
目标：[描述要调研什么]

步骤：
1. 用 ask-search 搜索（学术用 -e google_scholar,semantic_scholar）
2. 选 3-5 个最相关的 URL
3. 用 crwlr crawl -O /tmp/xxx.md 读取页面
4. 综合分析，给出结论
```
