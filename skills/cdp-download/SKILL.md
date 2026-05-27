---
name: cdp-download
description: >
  通过 Chrome DevTools Protocol 下载文件，支持浏览器认证和 Cookie。
  仅当 wget / curl 下载失败时使用（如需要浏览器认证、Cookie、或被反爬拦截的资源）。
  适用场景：下载需要登录态的 PDF、受保护的文件、带 Cookie 的二进制资源。
  中文触发词："下载PDF"、"下载文件"、"下载这个"。
  英文触发词："download PDF", "download file", "download this"。
  常规下载优先用 wget / curl；仅在认证失败或被反爬拦截时使用此 skill。
argument-hint: 'cdp-download <url> [output_file]'
allowed-tools: Bash
---

# cdp-download — CDP 认证下载

通过 Chrome DevTools Protocol 下载文件，使用 `Network.loadNetworkResource` + `IO.read`。
支持浏览器认证/cookie（`includeCredentials: true`），流式读取适合大文件。

## 调用方式

```bash
# 跨平台（推荐）：
uv run --script ${SKILL_DIR}/scripts/cdp-download <url>
uv run --script ${SKILL_DIR}/scripts/cdp-download <url> output.pdf

# Unix 上也可直接执行：
${SKILL_DIR}/scripts/cdp-download <url> output.pdf
```

需要 `uv`（自动安装 websocket-client）。环境变量 `CDP_URL` 必须配置（如 `http://localhost:9223`）。

## 使用场景

| 场景 | 推荐工具 |
|------|---------|
| 普通文件下载 | `wget <url>` 或 `curl -O <url>` |
| 需要浏览器认证/cookie | cdp-download |
| 被反爬拦截 | cdp-download |
| 大文件流式下载 | cdp-download |

## 优势

- 支持认证/cookie（复用浏览器登录态）
- 流式读取，适合大文件
- 下载到临时文件，成功后原子替换（避免残留部分文件）

## 注意事项

- 仅在 wget/curl 失败时使用，不要作为默认下载工具
- 需要 CDP 后端运行且 Chrome 已启动
- 如果文件需要特定 cookie，先通过 noVNC（端口 6080）登录对应网站
