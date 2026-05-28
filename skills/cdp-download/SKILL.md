---
name: cdp-download
description: >
  通过 Chrome DevTools Protocol 下载文件，支持浏览器认证和 Cookie。
  仅当 wget / curl 下载失败时使用（如需要浏览器认证、Cookie、或被反爬拦截的资源）。
  适用场景：下载需要登录态的 PDF、受保护的文件、带 Cookie 的二进制资源。
  中文触发词："下载PDF"、"下载文件"、"下载这个"。
  英文触发词："download PDF", "download file", "download this"。
  常规下载优先用 wget / curl；仅在认证失败或被反爬拦截时使用此 skill。
argument-hint: 'cdp-download <url> [output_file] [--interactive]'
allowed-tools: Bash
---

# cdp-download — CDP 认证下载

通过 Chrome DevTools Protocol 下载文件。三种策略自动降级：

1. **流式下载**（`Network.loadNetworkResource` + `IO.read`）— 快速，适合大文件
2. **Fetch 拦截**（`Fetch.requestPaused` + `Page.navigate`）— 绕过 CSP 限制
3. **交互模式**（`--interactive`）— 通过 noVNC 手动解决 reCAPTCHA 后重试

支持浏览器认证/cookie（`includeCredentials: true`），流式读取适合大文件。

## 调用方式

```bash
# 跨平台（推荐）：
uv run --script ${SKILL_DIR}/scripts/cdp-download <url>
uv run --script ${SKILL_DIR}/scripts/cdp-download <url> output.pdf
uv run --script ${SKILL_DIR}/scripts/cdp-download <url> output.pdf --interactive

# Unix 上也可直接执行：
${SKILL_DIR}/scripts/cdp-download <url> output.pdf
${SKILL_DIR}/scripts/cdp-download <url> output.pdf -i
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CDP_URL` | Chrome DevTools Protocol 端点（必需） | — |
| `NOVNC_URL` | noVNC Web 客户端地址 | 自动从 CDP_URL 推导（同主机 6080 端口） |

需要 `uv`（自动安装 websocket-client）。

## 使用场景

| 场景 | 推荐工具 |
|------|---------|
| 普通文件下载 | `wget <url>` 或 `curl -O <url>` |
| 需要浏览器认证/cookie | cdp-download |
| 被反爬拦截（CSP） | cdp-download（自动使用 Fetch 拦截策略） |
| 被 reCAPTCHA 拦截 | cdp-download `--interactive`（通过 noVNC 手动验证） |
| 大文件流式下载 | cdp-download（策略 1 自动流式） |

## --interactive 模式

当服务端返回 reCAPTCHA 页面时，脚本会检测到并提示：

1. 在浏览器中打开 noVNC 地址（默认 `http://<CDP主机>:6080`）
2. 在 Chrome 窗口中完成 reCAPTCHA 验证
3. 回到终端按 Enter
4. 脚本自动重试下载

## 优势

- 三种策略自动降级，无需手动选择
- 支持认证/cookie（复用浏览器登录态）
- 流式读取，适合大文件
- 下载到临时文件，成功后原子替换（避免残留部分文件）
- 自动检测 reCAPTCHA 并引导用户通过 noVNC 解决

## 注意事项

- 仅在 wget/curl 失败时使用，不要作为默认下载工具
- 需要 CDP 后端运行且 Chrome 已启动
- 如果文件需要特定 cookie，先通过 noVNC（端口 6080）登录对应网站
