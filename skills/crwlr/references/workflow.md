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

**优先级：Google → Bing → 其他**

```bash
# 默认走 Google（最可靠）
ask-search "query"

# 限定站点（Google site: 语法）
ask-search "transformer attention site:arxiv.org"
ask-search "react hooks site:github.com"
ask-search "fastapi site:pypi.org"

# 限定时间（Google 查询语法）
ask-search "LLM agent after:2025"
ask-search "RAG 2024 site:arxiv.org"

# 用 Bing 等其他引擎（备选）
ask-search "query" -e bing
ask-search "query" -e bing,semantic_scholar
```

要点：
- 默认 Google，够用就不要换
- `site:` 限定域名比 `-e` 指定 API 引擎更可靠
- Google 时间语法：`after:2025`、`before:2024`、`2024..2025`
- `-e arxiv` 直接打 API 有限流，不推荐

## 3. 读取页面

```bash
crwlr crawl -O /tmp/page.md "url"    # 保存到文件（推荐）
crwlr crawl -o md "url"               # 输出到 stdout
```

## 4. 批量处理

```bash
# 搜索 → 拿 URL 列表 → 逐个读取
for url in $(ask-search "query" -u -n 3); do
  crwlr crawl -O /tmp/page.md "$url"
done
```

## 5. sub-agent prompt 模板

```
目标：[描述要调研什么]

步骤：
1. 用 ask-search "xxx site:arxiv.org" 搜索
2. 选 3-5 个最相关的 URL
3. 用 crwlr crawl -O /tmp/xxx.md 读取页面
4. 综合分析，给出结论
```
