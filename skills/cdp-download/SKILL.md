---
name: cdp-download
description: >
  用 Chrome 浏览器下载文件（支持认证/Cookie）。当 wget/curl 下载失败时使用。
  中文触发词："下载PDF"、"下载文件"、"下载这个"。
  英文触发词："download PDF", "download file", "download this"。
argument-hint: 'cdp-download <url> [output_file] [--interactive]'
allowed-tools: Bash
---

# cdp-download

用 Chrome 浏览器下载文件，支持认证/Cookie。当 wget/curl 失败时使用。

```bash
uv run --script ${SKILL_DIR}/scripts/cdp-download <url> output.pdf
uv run --script ${SKILL_DIR}/scripts/cdp-download <url> output.pdf --interactive
```

环境变量：`CDP_URL`（必需），`NOVNC_URL`（可选，默认从 CDP_URL 推导）。
