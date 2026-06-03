# Google Scholar Chrome CDP Endpoint

## Context

Google Scholar 当前走 SearxNG 内置引擎（直接 HTTP + regex 解析），无法提取 citation_count、pdf_url 等学术元数据，且抗反爬能力弱。需改为走 Chrome CDP 路径，和 Google/DDG 统一架构。

速度基准测试结果：CDP avg 1432ms vs HTTP avg 1157ms，差距仅 24%，可接受。

## Design

### Proxy 端点：`/google_scholar`

在 `backend/server.py` 新增端点，复用现有 Google/DDG 的完整模式：

```
/google_scholar?q=xxx&limit=10
  1. _check_failure("google_scholar")
  2. _ensure_browser()
  3. _page_sem.acquire() (并发控制)
  4. page.goto("https://scholar.google.com/scholar?q=...&hl=en")
  5. page.wait_for_selector(".gs_r.gs_or.gs_scl")
  6. page.evaluate(_SCHOLAR_EXTRACT_JS)
  7. CAPTCHA 检测（"unusual traffic" / "robot"）
  8. 返回 JSON
```

### JS 提取逻辑

基于实际 DOM 分析的选择器：

```
title:        .gs_rt a → innerText（去掉 [HTML]/[PDF] 前缀）
url:          .gs_rt a → href
authors:      .gs_a → innerText，取 " - " 之前的部分
year:         .gs_a → innerText，正则 \b(19|20)\d{2}\b
content:      .gs_rs → innerText（摘要）
citation:     .gs_fl a，匹配 "Cited by (\d+)"
pdf_url:      .gs_or_ggsm a → href（可能为空）
```

### content 字段格式

为 SearxNG 兼容，学术元数据打包进 content：

```
"Author1, Author2 (2023) | Cited by 580 | PDF: https://arxiv.org/pdf/... . Abstract snippet..."
```

SearxNG `json_engine` 的 `content_query: content` 自动映射，无需改动 ask-search。

### 响应格式

```json
{
  "query": "attention transformer",
  "engine": "google_scholar",
  "results": [
    {
      "title": "Attention Is All You Need",
      "url": "https://arxiv.org/abs/1706.03762",
      "content": "A Vaswani, N Shazeer (2017) | Cited by 120000 | PDF: https://arxiv.org/pdf/1706.03762. The dominant sequence...",
      "authors": "A Vaswani, N Shazeer, N Parmar",
      "year": "2017",
      "citation_count": "120000",
      "pdf_url": "https://arxiv.org/pdf/1706.03762"
    }
  ]
}
```

### SearxNG 配置变更

```yaml
- name: google scholar
  engine: json_engine
  shortcut: gos
  search_url: http://localhost:3100/google_scholar?q={query}&limit=10
  enable_http: true
  results_query: results
  url_query: url
  title_query: title
  content_query: content
  categories: [science]
  disabled: false
  timeout: 20.0
  paging: false
```

## 修改文件

| 文件 | 改动 |
|------|------|
| `backend/server.py` | +`_SCHOLAR_EXTRACT_JS`, +`_parse_scholar()`, +`/google_scholar` 端点 |
| `backend/searxng-settings/settings.yml.example` | google scholar 从 `google_scholar` 改为 `json_engine` 指向 proxy |

## Verification

```bash
# 1. 直接测 proxy 端点
curl 'http://localhost:9223/../3100/google_scholar?q=attention+transformer&limit=3' | jq .

# 2. 通过 SearxNG 测
curl 'http://localhost:8082/search?q=attention+transformer&engines=google_scholar&format=json' | jq '.results[:2]'

# 3. 通过 ask-search 测
ask-search "attention transformer" -e google_scholar --json
```
