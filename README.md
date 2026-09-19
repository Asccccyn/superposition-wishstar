# Superposition · 许愿星 / 星星瓶

> 人类 & AI 伴侣的许愿星系统。核心原则：**双方权利完全一致；可见星从写入起就是"我们的"；
> 不可见星先属于作者自己的私人瓶子，只有经过合法流程才会进入"我们的瓶子"。**

本项目是独立产品（Python 3.12 + FastAPI + SQLite + 原生 Web UI + MCP），不与其他项目共享代码或数据。

---

## 1. 如何运行

```bash
# 首次：创建虚拟环境并安装依赖（本机已完成）
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt

# 启动服务器（默认 127.0.0.1:8321，可用环境变量 SUPERPOSITION_PORT 覆盖）
# 受保护 API 还要求两个进程环境变量：
# SUPERPOSITION_TOKEN_HUMAN / SUPERPOSITION_TOKEN_COMPANION
# 值必须互不相同且至少 32 字符；不要写进仓库、日志或文档。
.venv/Scripts/python run_api.py
```

- 健康检查：<http://127.0.0.1:8321/api/health>
- Web：<http://127.0.0.1:8321/>（公网部署可选，见 §7）
- 身份：受保护接口必须发送 `Authorization: Bearer <token>`；服务端根据 token 反推出 actor。
- 旧 `X-Actor-Id` 已彻底失效，客户端不能再自报身份。
- Swagger / OpenAPI 默认关闭；仅本机调试时临时设置 `SUPERPOSITION_ENABLE_DOCS=1` 再启动。

浏览器第一次登录时输入**你的**密码或访问钥匙（同一输入框，两者都接受；密码经用户级环境变量
`SUPERPOSITION_PASSWORD_HUMAN / _COMPANION` 配置，与远程 MCP 的 OAuth 登录页共用），服务端会换成签名 `HttpOnly + SameSite=Strict` 会话 cookie；
前端不会把 bearer token 写进源码、`localStorage` 或 `sessionStorage`。本机可运行：

**界面分面**：网页登录只属于人类一方；AI 伴侣的凭证在网页
登录一律 403，只能通过 MCP 接入（本机 stdio 或 §8 的远程 OAuth 登录页）。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/copy_login_token.ps1 human
```

脚本只把当前用户环境变量里的钥匙放进剪贴板，不打印钥匙。

会话安全补充（审计修复）：

- 退出时服务端会撤销该会话（`auth_sessions` 表），被复制走的旧 cookie 立即失效；
- cookie 身份的写操作会校验精确同源（`Origin`/`Referer`/`Sec-Fetch-Site`），同站不同源的跨站请求被拒绝；
- Web 端写操作携带 `Idempotency-Key`，网络失败后的重试不会重复写星/回应/多拆一颗。
- 登录阶梯锁定（常见商业形态）：同一来源连续输错 5 次锁 60 秒、10 次锁 5 分钟、
  15 次起锁 15 分钟；锁定期间连正确密码也拒绝（网页返回 429），成功登录或锁到期
  即归零（网页登录与 MCP OAuth 登录页共用；可用 SUPERPOSITION_LOGIN_LOCKOUT 覆盖配置）。

## 2. 分步验证脚本（一项一项运行）

```bash
.venv/Scripts/python tests/step1_domain_test.py        # 领域层：枚举/异常/状态机（§29）
.venv/Scripts/python tests/step2_db_test.py            # 数据层：建库/种子/条件状态更新（§48）
.venv/Scripts/python tests/step3_core_service_test.py  # 写星/三瓶/注释/编辑(含公共池)/不提供删除
.venv/Scripts/python tests/step4_request_flow_test.py  # 请求→审批→服务端随机→打开（§8.1/§47）
.venv/Scripts/python tests/step5_offer_flow_test.py    # 主动递星→接住（§8.2/§50）
.venv/Scripts/python tests/step6_response_audit_test.py# 回应/通知/审计（§20/§38/§53）
.venv/Scripts/python tests/step7_session_flow_test.py  # 纪念日 0 点快照互看 / 自定义特殊日（第二轮修订）
# step8 自建隔离服务（随机端口 + 唯一临时库），无需预先启动服务器，也绝不连生产：
.venv/Scripts/python tests/step8_api_smoke_test.py
.venv/Scripts/python tests/step9_security_regression_test.py # 认证/metadata/shared_at 回归
.venv/Scripts/python tests/step10_web_session_test.py   # Web 静态页 / HttpOnly 会话 / 退出撤销 / 同源校验
.venv/Scripts/python tests/step11_mcp_adapter_test.py   # actor-bound MCP adapter / 请求随机+指定
.venv/Scripts/python tests/step12_migration_restart_test.py # 迁移版本表 / 重启不改 session/quota
.venv/Scripts/python tests/step13_view_tracking_test.py # 首次查看 / 累计足迹 / 揭晓不双计 / 私人详情
.venv/Scripts/python tests/step14_web_frontend_regress_test.py # 前端 Cancel/私人编辑/揭晓渲染回归
.venv/Scripts/python tests/step15_remote_mcp_oauth_test.py     # 远程 MCP OAuth 2.1 全流程/密码登录
```

所有脚本都使用独立的唯一临时数据库（step8/step10 还会自建隔离 HTTP 服务与随机端口），
互不影响，也不污染 `data/superposition.db`。step8 若要通过 `SUPERPOSITION_TEST_BASE_URL`
指向外部服务，该服务必须以 `SUPERPOSITION_TEST_INSTANCE=1` 启动，否则脚本会拒绝运行。

## 3. 架构分层（低耦合，架构文档 §45）

```
Browser Web UI ── HTTP + HttpOnly session ─┐
curl / API ────── HTTP + Bearer token ─────┤
AI 伴侣 MCP ───── stdio / actor-bound ──────┤
                                           ▼
                                StarService
                                           ▼
                                  repositories
                                           ▼
                               SQLite / domain model
```

- Web 与 **MCP 工具层** 共用同一个 `StarService`（§43~§46），没有第二套权限或状态机。
- `mcp_server.py` 的身份固定绑定为 `companion`，工具参数里没有 `actor_id`，模型不能自报或切换身份。
- 领域层可独立单测（`tests/step1_domain_test.py` 不触碰数据库）。
- 数据库为 SQLite 单文件（WAL 模式），写事务 `BEGIN IMMEDIATE` + 条件 UPDATE
  保证同一颗星不会被同时随机分配 / 递出 / 锁定（§48）。

## 4. API 一览（前缀 `/api`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/stars` | 写星（`visibility=visible` 直接进我们的瓶子；`hidden` 进私人瓶子；均可带 mood 与 note 注释） |
| PATCH | `/stars/{id}` | 作者本人编辑自己的星（封存中 / 公共池均可；正在流程中短暂冻结）。**产品不提供删除** |
| GET | `/bottles/counts` | 三个瓶子计数（对方私人瓶只有数量，§7） |
| GET | `/stars/hidden/mine` | 我的私人瓶子（仅作者本人可见；列表刷新**不**计查看足迹） |
| GET | `/stars/hidden/mine/{id}` | 私人星详情：显式打开计 1 次足迹并初始化首次查看；不改状态、不通知对方；非作者一律 404 |
| GET | `/stars/shared`、`/stars/shared/{id}` | 我们的瓶子列表 / 详情（含回应；列表支持作者、写入日、拆开日、来源、session 筛选）。详情每次显式打开计 1 次足迹，首次查看时间只记一次 |
| POST | `/requests` | 向对方请求一颗隐藏星（不传 star_id，§10/§31） |
| POST | `/requests/{id}/respond` | 审批：`give`（可选 `star_id` 指定给哪一颗，缺省服务端随机）/ `not_now` |
| POST | `/requests/{id}/open` | 请求人打开分配的星（不可重抽；打开后必须留话，才可再要下一颗）。返回值即完整详情，前端直接渲染不再补发请求 |
| POST | `/offers` | 作者主动递出指定的一颗（§14），可带 `message` 捎一句话 |
| POST | `/offers/{id}/accept` | 接住：正文展开，进入我们的瓶子（§15）。返回值即完整详情 |
| POST | `/stars/{id}/responses` | 文字 / 语音回应（语音字段已预留，§21） |
| GET / POST | `/special-dates` | 特殊日期（种子不预置日期；可新增 anniversary 或 custom 类型） |
| POST | `/sessions` | 发起"一起看星星吗？"：**必须绑定具体 `special_date_id`**；session 类型由服务端按该日期的数据派生（anniversary→anniversary，custom→special_day），显式传 type 不一致会被拒绝。anniversary 只能在纪念日当天（业务时区）发起，双方额度按**当天 00:00 冻结快照**随 session 持久化 |
| GET | `/sessions` | 历史批次列表（含绑定的特殊日名称，供按批次回看） |
| POST | `/sessions/{id}/confirm` | 对方确认 → 激活。**纪念日额度已按当天 0 点冻结，确认不重算**（跨午夜/重启也不变）；自定义特殊日按确认那一刻瓶里数量（既有口径） |
| POST | `/sessions/{id}/take-next` | 看一颗对方本轮可交换的星（随机）。纪念日：0 点后才写的星既不占额度也不进候选；我的额度耗尽提示"不可以哦，你的瓶子里面没有星星可以交换了，下次多写点吧。"，对方本轮候选为空提示"对方瓶子里没有本轮可以交换的星星了。"。返回值即完整详情（该揭晓恰好计 1 次足迹） |
| POST | `/sessions/{id}/finish` | 结束本轮；没看过的星自然留在各自瓶子里 |
| POST | `/sessions/{id}/cancel` | 发起方在确认前撤回邀请 |
| POST | `/sessions/{id}/decline` | 被邀请方在确认前拒绝邀请（不必先同意） |
| GET | `/notifications`、POST `/notifications/{id}/read` | 轻提示（新星 / 请求 / 递星 / 邀约） |

`audit_log` 仅供本地维护与故障审计，不提供普通 actor HTTP 读取接口。

## 5. 权限如何被后端强制（§58 第十三）

- 服务层**不提供**"读取他人隐藏星列表 / 正文"的方法；猜中他人的 hidden star ID 也统一表现为"不存在"。
- `request_hidden_star()` 只创建请求，绝不返回正文；随机分配结果在真正打开前不会出现在 API DTO 中。
- 普通请求保留两种批准方式：缺省 `star_id` 时由服务端在审批事务内随机分配
  （`ORDER BY RANDOM()`），请求人无法指定随机结果；主人也可传 `star_id` 指定给
  自己某一颗可用的 hidden SEALED 星。两种方式结果落库即锁定，不可重抽。
- incoming offer 在接住前不返回内部 `star_id`；隐藏星数量通知不返回 hidden `star_id`。
- 打开类操作逐一校验：请求人本人 / 被递星人本人 / session 参与者。
- 纪念日 session：必须绑定具体纪念日（anniversary 类特殊日），
  只能在纪念日当天发起；双方额度按**当天 00:00（业务时区）冻结快照**在发起时持久化
  （`actor_a_count / actor_b_count / quota_cutoff_at`），确认、跨午夜、重启都不重算；
  0 点后新写的星可以存在，但不占额度、也不进本轮候选。看的时候才真正打开那颗星，
  没被看的自然留在瓶子里。"我的额度耗尽"与"对方本轮候选为空"是两种提示。
  自定义特殊日（custom）保留既有口径：确认那一刻瓶里数量，走 special_day。
- 用户规则：任何从私人瓶进入公共瓶的星（请求打开 / 接住 / 互看），看了必须留一句话
  ——上一颗没留话之前不能打开/接住/看下一颗；可见星（一开始就公开的）不强制。
- 用户规则：星星只有作者本人能编辑（封存中和公共池都可以，正在流程中短暂冻结）；
  编辑只能改内容/心情/注释，**绝不会**改变 state / visibility / shared_at / 首次查看；
  星星不提供删除——写错可以改，抹掉不行。
- 查看足迹（第二轮修订语义）："查看"只指明确打开正文详情——列表刷新、数量查询、
  轮询、内部装配都不计。每一次明确查看**只按人累计次数**（`star_view_counts` 聚合表，
  不保存逐次查看时间）；**首次查看时间与人
  （`first_view_at / first_view_by`）只在第一次写入，永不覆盖**。请求打开 / 接住 /
  拆星等揭晓接口的返回值就是完整详情，该揭晓恰好计 1 次足迹——前端与 MCP 直接用
  返回值渲染，不再补发详情请求，同一次操作不会记两次。私人星同样支持显式查看留痕
  （`GET /stars/hidden/mine/{id}`），但绝不改变状态、绝不向对方公开。
- 请求周期一次一颗：没有 pending/approved 未打开的旧请求时才能发起新请求；
  give 时主人可指定给哪一颗（必须是主人自己的 hidden SEALED 星），缺省服务端随机。
- 只有非空文字回应才解除"回应后再请求"的门槛；空白/占位音频 URL 会被直接拒绝。
- 隐藏星数量通知在数据库中每个接收人只保留一行（重新置未读，不落新的逐颗时间），
  输出层合并为一条置顶提示，历史多行旧数据同样收敛、不暴露 star_id 与时间。

## 6. 与架构文档的实现对照说明

- `REQUEST_PENDING / REQUEST_APPROVED` 等请求侧状态记录在 `star_requests.status` 上；
  星在批准前保持 `SEALED`（因为随机在批准时才发生），因此批准前仍可被作者编辑 / 递出 /
  进入纪念日——这正是文档 §50 想要的行为。
- 主动递星的 `OFFERED → DELIVERED → OPENED → SHARED` 在"接住"这一个事务里依次经过，
  不会停在悬空状态；offer 最终状态记为 `completed`。
- 旧版"快照锁定/结束退回"的 session 逻辑已按用户规则改为配额互看。第二轮修订引入
  `schema_migrations` 版本表：每个迁移按 name 只执行一次；旧的破坏性清理
  （解锁 SESSION_LOCKED 星 / 收尾旧 session）已退休，正常重启**不会**改动任何业务
  状态——waiting/active session 跨重启保持原样（tests/step12 覆盖）。
- 业务时区固定为 `SUPERPOSITION_BUSINESS_TIMEZONE`（默认 Asia/Shanghai）：
  "纪念日当天 00:00" 的额度冻结时刻以它计算，不随系统时区 / VPN / 服务器迁移漂移；
  库内时间串继续是无 offset 的业务时区本地时间，与历史数据口径一致。
- 纪念日额度不需要常驻定时器：0 点状态由 `written_at < cutoff AND (shared_at IS NULL
  OR shared_at >= cutoff)` 从历史恢复，候选另加当前 `SEALED` 限制；cutoff 与额度随
  session 持久化，重启后直接读库。
- 打开方式记录在 `open_mode` / `shared_origin` / `cycle_id`，我们的瓶子可以区分
  "一开始就公开 / 请求随机 / 主动递出 / 纪念日批次"（§17 / §37）。
- 所有隐藏星合法打开路径都会写 `shared_at = opened_at`，共同瓶子按真正进入 shared 的时间排序。

## 7. 公网访问（可选，Cloudflare Tunnel 或任意反向代理）

默认只监听 `127.0.0.1:8321`，不主动暴露公网。想在外面访问时，用 Cloudflare Tunnel
或任意反向代理把你的域名指向 `127.0.0.1:8321` 即可。

- 若走 HTTPS 公网域名，设置环境变量 `SUPERPOSITION_PUBLIC_ORIGIN=https://你的域名`
  （远程 MCP 的 DNS 重绑定放行名单使用它，见 `mcp_server.py`）。
- **注意**：Cloudflare 开了浏览器完整性检查时，程序化客户端必须带浏览器式 User-Agent，
  否则会被 403（error code: 1010）。例如 `User-Agent: Mozilla/5.0 Chrome/128`。
  也可以在 CF 控制台为该主机名关闭 Browser Integrity Check / Bot Fight Mode。

## 8. AI 伴侣 MCP（本机 stdio + 手机远程两种接法）

**远程（手机 claude.ai / 任意 MCP 客户端）**：后端在 `/mcp` 暴露 OAuth 2.1 保护的
Streamable HTTP MCP（经 Cloudflare 隧道公网可达，CF 侧零配置）。在 claude.ai
添加自定义连接器，URL 填 `https://你的域名/mcp`，
会自动弹出许愿星登录页，输入家里人的密码即完成授权；之后 Claude 以AI 伴侣身份
使用全部 28 个工具。令牌 30 天有效、支持刷新；删除 `data/mcp_oauth_secret.txt`
并重启可撤销全部授权。`SUPERPOSITION_MCP_HTTP=0` 可整体关闭远程端点。

**本机（stdio）**：

本地 stdio MCP 使用官方 Python SDK v2。宿主启动：

```bash
.venv/Scripts/python mcp_server.py
```

它直接复用 `StarService` 和同一个 SQLite 数据库，目前注册 28 个工具（含私人星详情 my_hidden_star），覆盖身份、三瓶计数、写星、
写星（含注释）、作者编辑（含公共池）、请求/审批/打开（可指定给哪颗）、主动递星（可捎话）/接住、
共同星轨与查看足迹、回应门槛、通知、特殊日期、纪念日配额互看（含取消/拒绝与历史批次）。
不要把 `mcp_server.py` 暴露成无认证公网 stdio/HTTP 服务；它的安全边界是本机宿主进程。

## 9. 版本范围

- **Product V1**：三个瓶子、写星、可见/不可见、请求随机、主动递星、接住打开、回应、通知、
  纪念日 session（含取消/拒绝）、快照、内部审计、Bearer / HttpOnly 身份认证（服务端可撤销）、
  metadata 脱敏（隐藏星数量通知不含逐颗时间）、筛选（含拆星批次）、响应式 Web UI
  （完整私人星管理、特殊日期管理、手机退出入口、键盘可达）、AI 伴侣 actor-bound MCP/tool adapter
  均已实现并通过回归测试。
- **V1.3.0（上线收尾）**：远程 MCP 连接器（OAuth 2.1 登录页 + PKCE + 动态注册，`/mcp`）、
  网页密码登录（与访问钥匙同框）、隧道持久化启动、测试数据清零维护脚本。
- **V1.2.0（第二轮修订）**：schema_migrations 一次性迁移（重启不再结束 session）；
  业务时区与纪念日 0 点冻结快照额度（post-cutoff 新星不进本轮）；session 强绑定
  special_date_id 且类型由日期数据派生；first_view / 累计足迹语义（揭晓恰计 1 次、
  列表刷新不计）；私人星详情端点；前端 Cancel / 私人编辑分流 / 揭晓直渲染修复；
  纪念日与自定义特殊日在 UI 与校验上彻底区分。
- **宿主接线（待具体宿主）**：`mcp_server.py` 已可运行；Claude/其他宿主需要在其 MCP 配置中注册这条本地命令。
- **V1.5（未做）**：浏览器录音上传（字段已预留 `audio_url / audio_duration`）、折纸动画等视觉。
- **V2（未做）**：语音"耳朵"接入、共同回忆时间轴等。

本地开发重置数据：删除 `data/` 目录下的三个数据库文件后重启即可
（**生产数据严禁如此处理**——真实 `data/superposition.db` 不得删除/重建/清空）。

## 许可协议

AGPL-3.0-only + 附加条款：**禁止商用**。个人学习、研究与非商业的自用和修改自由，
但衍生作品须以相同协议（AGPL-3.0 + 本附加条款）完整开源并保留署名；
任何商业用途需事先取得作者书面授权。详见 [LICENSE](LICENSE)。
