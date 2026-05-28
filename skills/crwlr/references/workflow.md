# 高级工作流

当任务不只是一次简单搜索/读取时，参考以下模式。

## 1. 搜索 → 阅读 → 综合

最常用的模式。先广搜，再精读，最后综合。

```
ask-search "query" --num 5
  ↓ 看摘要，选 2-3 个最相关的 URL
crwlr crawl -O /tmp/page1.md "url1"
crwlr crawl -O /tmp/page2.md "url2"
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
crwlr crawl -O /tmp/paper.md "https://arxiv.org/abs/xxxx.xxxxx"

# 需要对比多篇 → 并行读
crwlr crawl -O /tmp/paper1.md "https://arxiv.org/abs/xxxx1"
crwlr crawl -O /tmp/paper2.md "https://arxiv.org/abs/xxxx2"
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
crwlr crawl -O /tmp/news.md "news-url"
```

## 4. 搜索结果的二次处理

拿到搜索结果后批量读取。

```bash
# 只拿 URL 列表
ask-search "query" --urls-only --num 5

# 逐个读取保存
for url in $(ask-search "query" --urls-only --num 3); do
  crwlr crawl -O /tmp/page.md "$url"
done
```

## 5. 交给 sub-agent 的 prompt 模板

当主 agent 需要分派搜索+阅读任务给 sub-agent 时：

```
请完成以下调研任务：

目标：[描述要调研什么]

步骤建议：
1. 用 ask-search skill 搜索相关信息
2. 从结果中选取最相关的 3-5 个 URL
3. 用 crwlr skill 读取这些页面，保存到文件
4. 综合多源信息，给出分析结论

注意：
- 使用 -c science 限定学术搜索
- 多源交叉验证，不要只依赖一个来源
- 关键信息标注来源 URL
```
