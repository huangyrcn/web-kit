# web-kit backend 认证网关设计

- **日期**: 2026-06-05
- **状态**: 待实现
- **范围**: 给 web-kit backend 的三个对外服务(SearxNG / Chrome CDP / noVNC)加一道统一的认证网关,消除当前 LAN 无认证暴露;同时收敛 backend 内部的密钥管理。

## 1. 背景与问题

web-kit backend 部署在 hnas-root(192.168.1.178),单容器 + supervisord 管 9 个进程。当前状态存在两类问题:

**安全暴露(高优先级)**
- 三个端口 `8082`(SearxNG)、`9223`(Chrome CDP)、`6080`(noVNC)全部 bind 到 `0.0.0.0`,LAN 内任何人可直接访问。
- noVNC 的 x11vnc 以 `-nopw`(无密码)运行。
- 后果:LAN 内任何人可通过 noVNC/CDP 接管那个保留登录态的 Chrome,读取已登录站点的 cookie。
- 这与 `docker-compose.yml` 注释和 `.env.example` 明确写的"默认 127.0.0.1、LAN 暴露必须设密码"约定相反。

**密钥管理割裂(中优先级)**
- backend 侧三套机制并存:`.env`(env 间接,管 bind/VNC)、`settings.yml`(明文内联 4 个真实密钥)、`.example`(占位符模板)。
- 真正敏感的密钥(SearxNG secret_key、Semantic Scholar key、CORE key、GitHub PAT)以明文写死在 `settings.yml`,且文件权限 644(全用户可读)。

**plugin 侧现状(已正确)**
- plugin 脚本只持有连接入口(`SEARXNG_URL` / `CDP_URL`),零硬编码密钥。
- 唯一例外是可选的 `TAVILY_API_KEY`(env 注入,默认不用)。

## 2. 设计目标

1. 三个对外服务统一到一个网关 + 一个 key 后面,无 key 不可访问。
2. plugin 侧保持"瘦客户端 + 入口":新增一个 `WEB_KIT_API_KEY`,脚本带上即可用。
3. fail-closed:网关没配 key 拒绝启动;请求 key 不对返回 401。
4. 不破坏现有 crwlr / ask-search / cdp-download 的使用方式(除了多带一个 key)。
5. 顺带收敛 backend 密钥管理(见 §7,可作为后续独立步骤)。

## 3. 架构

在三个内部服务前面加一个 **Caddy** 反向代理作为唯一对外入口。Caddy 选型理由:WebSocket(CDP、noVNC)零配置透传;`{$VAR}` 原生 env 注入;单静态二进制,贴合现有 supervisord。

```
对外(LAN)只经 Caddy,三个 listener、同一个 WEB_KIT_API_KEY:
  ├── 端口 A (8082) → SearxNG (:8080)        校验 X-API-Key 头     [ask-search]
  ├── 端口 B (9223) → cdp-forward (:9224)     校验 X-API-Key 头     [crwlr / cdp-download]
  └── 端口 C (6080) → noVNC (:6080→内部改名)   校验 HTTP Basic Auth  [浏览器人工]

容器内部(不再直接对外):
  SearxNG :8080 / search-proxy :3100 / Chrome :9222 / cdp-forward / noVNC
```

**一个 key,三个口都认。** 区别只在传输方式:
- 脚本路径(SearxNG、CDP)→ `X-API-Key: <key>` 请求头(代码自动带)
- 浏览器路径(noVNC)→ HTTP Basic Auth,密码 = 同一个 key(人工输一次,浏览器缓存)

## 4. 组件改动

### 4.1 新增:Caddy(supervisord 第 10 个 program)

- 新文件 `backend/Caddyfile`,三个 listener。
- 脚本路径用请求匹配器比对明文 key:`@bad not header X-API-Key "{$WEB_KIT_API_KEY}"` → `respond @bad 401`。
- noVNC 路径用 `basic_auth`,密码存 bcrypt 哈希(见 §6 实现注意点)。
- supervisord 加 `[program:caddy]`,priority 高于其它(最先起,最后倒);启动脚本先校验 `WEB_KIT_API_KEY` 非空,空则 `exit 1`(fail-closed)。

### 4.2 端口重新布线

| 服务 | 现在 | 改后 |
|---|---|---|
| Caddy(新) | — | 监听 8082/9223/6080,唯一对外 |
| SearxNG | :8080→宿主8082 | 只 :8080 容器内 |
| cdp-forward(socat) | 9223→9222 | 改 9224→9222,让出 9223 给 Caddy |
| noVNC(websockify) | :6080→宿主0.0.0.0 | 改内部端口(如 6081),Caddy 转发 |
| Chrome | :9222 内部 | 不变 |

### 4.3 docker-compose.yml

- `ports:` 改为只发布 Caddy 的 8082/9223/6080,仍走 `${WEB_KIT_BIND:-127.0.0.1}`。
- `environment:` 增加 `WEB_KIT_API_KEY: ${WEB_KIT_API_KEY:?set in .env}`(未设则 compose 报错)。

## 5. 认证数据流

### 5.1 ask-search(HTTP)
```
脚本 → urllib 请求加 X-API-Key 头 → Caddy:8082 →(比对明文 key)→ SearxNG:8080
```

### 5.2 crwlr / cdp-download(CDP,两段式)
CDP 连接是两段:先 HTTP 取 `webSocketDebuggerUrl`,再连 WebSocket。两段都过 Caddy、都要带 key。
```
脚本 → GET /json/version (带 X-API-Key) → Caddy:9223 →(校验)→ cdp-forward:9224 → chrome:9222
脚本 → WS /devtools/... (带 X-API-Key) → Caddy:9223 →(校验)→ ...
```
- `cdp-download`(websocket-client):HTTP 用 `urllib` add_header;WS 用 `create_connection(url, header=["X-API-Key: <key>"])`。
- `crwlr`(crawl4ai→playwright):monkeypatch `connect_over_cdp`,注入 `headers={"X-API-Key": key}`。playwright 的 `connect_over_cdp(headers=...)` 签名已确认支持(见 §6 待验证项:headers 是否同时覆盖 HTTP+WS 握手)。

### 5.3 noVNC(浏览器,Basic Auth)
```
浏览器开 http://host:6080 → Caddy 要求 Basic Auth → 弹框 → 用户名任意/密码=同一个 key
→ Caddy 比对 bcrypt 哈希 → 放行 → noVNC 网页 + 内部 ws 升级(浏览器自动复用 Authorization 头)
```

## 6. 错误处理(fail-closed)

| 场景 | 行为 |
|---|---|
| 网关未配 `WEB_KIT_API_KEY` | Caddy 启动脚本 `exit 1`;compose `${WEB_KIT_API_KEY:?}` 也会拦截。容器不会无认证启动 |
| 脚本未设 `WEB_KIT_API_KEY` | 脚本启动即报清晰错误退出,不发请求吃 401 |
| key 不匹配(脚本) | Caddy 返回 401;脚本把 401 翻译成"key 无效/未配置"的可读提示 |
| key 不匹配(noVNC) | 浏览器 Basic Auth 框反复弹出 / 显示 401 |

### 实现注意点(诚实标注,实现阶段确认)
1. **crwlr 的 header 覆盖范围**:`connect_over_cdp(headers=...)` 是否同时给初始 `/json/version` HTTP 和 WS 握手都加头,需用真实连接测一次。若只覆盖其一,fallback:让 Caddy 对 CDP 端口的 `/json*` 路径放宽、仅校验 WS 握手。默认按"两步都带头"实现。
2. **noVNC 的 bcrypt**:Caddy `basic_auth` 要求密码为 bcrypt 哈希,而脚本路径要明文比对。方案:`.env` 放明文 key(脚本用)+ 部署时 `caddy hash-password` 生成的哈希(noVNC 用)。一个逻辑 key,两种编码。

## 7. 密钥管理收敛(可作为后续独立步骤)

网关解决"对外认证";本节解决"backend 内部密钥归口"。两者独立,可分两个 PR。

- **统一入口**:给 `start-searxng.sh` 加 `envsubst`,让 `settings.yml` 用 `${SEMANTIC_SCHOLAR_KEY}` 等占位符,真实值统一进 gitignored 的 `.env`。这样 backend 所有密钥(含 `WEB_KIT_API_KEY`)归口到同一个 `.env`,与现有 `WEB_KIT_*` 机制合并成一套。
- **`.env.example`** 补上所有密钥项(占位说明,非真实值)。
- **`settings.yml.example`** 的假 `ghp_` 之类占位符换成无歧义的 `<set-in-env>`。
- **plugin 文档**:SKILL.md 补上可选的 `TAVILY_API_KEY` 说明(目前功能存在但未文档化)。

### 运维动作(需用户授权,不在代码范围)
- 轮换已明文暴露的凭据:GitHub PAT、Semantic Scholar key、CORE key。
- `settings.yml` 权限收到 600。
- 宿主 `.env` 设 `WEB_KIT_API_KEY`(`openssl rand -hex 32`)及其 bcrypt 哈希。

## 8. 测试计划

### 8.1 脚本单元行为(本地,mock 或对真实 backend)
| 脚本 | 设了 key | 没设 key | key 错 |
|---|---|---|---|
| ask-search | 通过 | 启动报错退出 | 401→可读提示 |
| crwlr | 通过 | 启动报错退出 | 401→可读提示 |
| cdp-download | 通过 | 启动报错退出 | 401→可读提示 |

### 8.2 集成(对真实 hnas-root backend)
- 带正确 key:ask-search 返回结果、crwlr 抓到页面、cdp-download 下载成功。
- 不带 key / 错 key:三者均被 Caddy 401 拦截。
- noVNC:浏览器开 6080,Basic Auth 框出现;填对 key 进入、填错被拒。
- **crwlr header 覆盖验证**(§6 待验证项):确认 CDP 两段(HTTP + WS)都带上了 key。

### 8.3 回归
- 现有 3 个 eval 用例(search-news / academic-search / read-page)在加 key 后仍全部通过。

## 9. 非目标(YAGNI)

- 不做多 key / key 轮换接口 / 吊销列表——单静态 key 够用。
- 不做 TLS/HTTPS——LAN 内 + 后续可叠加,不在本设计。
- 不重写 crwlr 的 crawl4ai 依赖——monkeypatch 注入 header 即可。
- 不做与本目标无关的 backend 重构。


