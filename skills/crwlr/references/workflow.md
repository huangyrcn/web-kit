# 高级工作流

当任务不只是一次简单搜索/读取时，参考以下模式。

## 1. 搜索 → 阅读 → 综合

最常用的模式。先广搜，再精读，最后综合。

```
ask-search "query" --num 5
  ↓ 看摘要，选 2-3 个最相关的 URL
crwlr crawl -o md "url1" -O /tmp/page1.md
crwlr crawl -o md "url2" -O /tmp/page2.md
  ↓ 对比多源内容
综合分析，给出结论
```

要点：
- 搜索时先用 `--num 5` 少量结果快速筛选
- 阅读时用 `-O` 保存到文件，避免 stdout 截断
- 多源交叉验证，不要只依赖一个来源

## 2. 学术文献调研

搜索学术引擎，定位论文，读取全文。

```bash
# 学术搜索
ask-search "graph neural network domain adaptation 2024" --categories science --num 10

# 找到 arxiv 论文 → 读摘要页
crwlr crawl -o md "https://arxiv.org/abs/xxxx.xxxxx"

# 需要对比多篇 → 并行读
crwlr crawl -o md "https://arxiv.org/abs/xxxx1" -O /tmp/paper1.md
crwlr crawl -o md "https://arxiv.org/abs/xxxx2" -O /tmp/paper2.md
```

要点：
- `--categories science` 限定学术引擎，避免噪音
- `--engines arxiv,semantic_scholar` 可进一步精确到特定引擎
- arxiv 的 `/abs/` 页面是摘要，`/html/` 页面有全文 HTML

## 3. 新闻追踪

追踪某个话题的最新进展。

```bash
# 搜最新新闻
ask-search "topic" --categories news --num 10

# 多个新闻源对比
ask-search "topic" --categories news --engines "reuters,google news,bing news" --num 5

# 读具体报道
crwlr crawl -o md "news-url" -O /tmp/news.md
```

## 4. 深度爬取网站

需要整个站点或一个目录下所有页面时。

```bash
# 广度优先爬 20 页
crwlr crawl --deep-crawl bfs --max-pages 20 -o md "https://docs.example.com/"

# 结果保存到文件
crwlr crawl --deep-crawl bfs --max-pages 10 -o md -O /tmp/site.md "https://example.com/docs/"
```

要点：
- `bfs` 适合文档站（先广后深），`dfs` 适合纵深内容
- 大量页面用 `-O` 保存，不要 stdout
- 加 `-bc` 绕过缓存确保内容最新

## 5. 结构化数据提取

从页面提取特定格式的数据。

```bash
# 提取表格/列表数据
crwlr crawl -j "提取页面中所有论文标题、作者、年份，返回 JSON 数组" -o json "https://example.com/papers"

# 用 schema 精确控制输出格式
crwlr crawl -s schema.json -o json "https://example.com/products"
```

## 6. 搜索结果的二次处理

拿到搜索结果后批量读取。

```bash
# 只拿 URL 列表
ask-search "query" --urls-only --num 5

# 逐个读取（或分给 sub-agent 并行）
for url in $(ask-search "query" --urls-only --num 3); do
  crwlr crawl -bc -o md "$url"
done
```

## 7. 交给 sub-agent 的 prompt 模板

当主 agent 需要分派搜索+阅读任务给 sub-agent 时：

```
请完成以下调研任务：

目标：[描述要调研什么]

步骤建议：
1. 用 ask-search skill 搜索相关信息
2. 从结果中选取最相关的 3-5 个 URL
3. 用 crwlr skill 读取这些页面的全文
4. 综合多源信息，给出分析结论

注意：
- 使用 -c science 限定学术搜索
- 多源交叉验证，不要只依赖一个来源
- 关键信息标注来源 URL
```
