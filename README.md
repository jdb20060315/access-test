# 多学校手机测试部署包

其他账号第一次打开网页时，会看到指南针使用提示，并填写学校所在城市、北纬、东经。提交时在服务端校验坐标，按 GPS 坐标本地近似换算后请求该账号的校园点位。不使用原账号的学校坐标；无需地图 API 密钥。指南针读数和具体学校是否适用仍须由用户自己核对。

每个人使用本人的账号，明确同意服务器处理密码和位置并提交一条模拟记录。用户可选 2–3 公里，并用实测步频（每分钟步数）和步幅（每步米数）估算用时；振幅不能直接决定速度，页面不读取手机传感器。记录是否有效以手机 App 为准。

可选“记住账号、密码、位置和运动参数”，点击“保存设置（不提交记录）”即可先保存，不会登录或新增运动记录。同一浏览器下次打开时自动恢复设置，密码留空即可复用，也可以输入新密码更新。密码及配置使用已有 STATE_ENCRYPTION_KEY 加密后保存至 Neon，使用独立配置行复用现有表，不新增依赖或更改表结构；密码不返回页面、不进入浏览器本地存储或仓库。每次保存后 30 天内可复用；同一浏览器访问时续期原有 Cookie，保持任务和设置归属不变。清除浏览器数据、更换浏览器、过期或更换密钥后需要重新填写；不要更换现有密钥。使用“清除已保存设置”可移除已存凭据，历史任务、防重复锁和待退出会话不受影响。未勾选记住时不新增或覆盖已存配置。仅在本人设备上使用。

在 GitHub 仓库中用本包的全部代码文件覆盖对应旧文件，并增加 location_input.py。仓库若有旧的 school_location.json，可以留着，新版不读取它。部署使用原来的 DATABASE_URL 和 STATE_ENCRYPTION_KEY；不要更换加密密钥。ACCESS_CODE、ACCESS_EXPIRES、PERSONAL_ACCOUNT_SHA256 已不用于新版。旧个人检测记录仍保留，且不会被新入口读出。

Render 构建命令：`pip install -r requirements.txt && python prepare_runtime.py`
Render 启动命令：`python server.py`
保持 Free 套餐；不需要另建服务。

本轮故障对照期间，当前单进程服务临时固定最多同时处理 1 个运行、诊断或退出任务；服务器可用内存低于总内存的八分之一或 128 MiB（取较大值）时也拒绝新任务。已确认完成并退出的账号可以再次提交，包括当天；仍有未退出会话或结果未知时禁止再次预约。任何超时或未知结果不自动重试。

临时上限由代码固定为 1，旧的 `MAX_ACTIVE_RUNS` 环境变量（包括 `unlimited`）不会覆盖它；恢复多人并发需另行发布。该限制不清除已保存设置、账号预约或待退出会话，仅控制当前单进程的任务数量，不代表整个集群的上限，也不能保证解决上游 HTTP 567。

部署后打开 /health，release 应为 multi-school-v11-serial-diagnosis，multi_account_ready 应为 true，max_active_runs 应为 1。active_tasks 返回当前进程正在处理的任务数量，不包含账号资料。部署状态以上线核验为准。既有账号的 already_reserved 状态未改动，尚未验证新增记录能在官方 App 显示。

本地检查：`python -m unittest -v test_login_diagnostics test_saved_settings test_concurrency` 与 `node --test test_form.cjs`。保存检查采用真实 Fernet 加密和本地 SQLite 独立连接（仅对 SQL 占位符及 JSONB 类型标记做测试适配），不连接生产数据库，不登录或提交第三方平台。实现依据：[Fernet 文档](https://cryptography.io/en/50.0.0/fernet/)、[Cookie 属性](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)。用户偏好是复用已确认的账号、密码、位置和参数，仅在有可核实的记录成功及退出结果后提醒去 App 查看；未知结果不自动重交。

## 登录阶段诊断

新增 POST /api/diagnose，沿用原浏览器 Cookie、CSRF、Origin、本人 consent 和账号预约锁，可用 use_saved=true 复用已存配置。该入口只调用既有登录、学期查询和退出核对，不执行运动提交。未确认退出时仍保留加密会话和账号锁。固定设备身份及所有上游验证流程保持原样。

login_diagnostics 只报告已观察到的固定日志阶段和 HTTP 数字状态码；独立区分免验证条件查询与验证码流程中的 HTTP 失败，无法定位的错误仍标记 unknown。v9 实测观察到第三方验证码流程通过、业务请求 HTTP 567；这不证明 App 后端验证码校验通过，也不能认定密码错误。v10 已上线，进一步观察到两次 567 都返回 HTML。格式未知、截断或其他正文不猜测。不会保存原始日志、响应正文、URL、密码或 Token，也不根据格式认定具体厂商或拦截原因。现有 captcha 关键词分类继续保留兼容，不能单凭它确定根因。

## VPS 执行中继（v12）

Render 出口登录运动世界持续被上游边缘安全拦截（HTTP 567 HTML，10/13 起每次均有记录）；国内 VPS 同一代码 10/13 登录、上传受理、退出核对全部成功，用户当时在手机 App 看到进度增加。v12 起支持把单次任务转发到 VPS 执行，页面、预约锁、报告结构均不变。

- 环境变量 `RUNNER_MODE`：`local`（默认，现状不变）或 `ssh`。只有 `ssh` 且下列四项齐备才走中继，缺任何一项任务按结果未知处理，不自动重试。
- `SPORT_RELAY_HOST`（如 103.236.93.62）、`SPORT_RELAY_PORT`（35877）、`SPORT_RELAY_USER`（sportrelay）、`SPORT_RELAY_SSH_KEY`（专用 ed25519 私钥 PEM）、`SPORT_RELAY_HOSTKEY`（VPS ed25519 宿主机公钥行，固定信任，不匹配即失败）、`SPORT_RELAY_SHARED_TOKEN`（与 VPS /opt/sport-relay/token 一致）。
- VPS 侧部署在 `/opt/sport-relay`（relay.py + 原作者 v0.3.0 二进制，SHA-256 cc75af3d…，与 10/13 成功运行所用逐字节一致），以专用户 `sportrelay` 运行；authorized_keys 强制命令 + 共享令牌双重校验；凭据只在内存，不写日志、不回显二进制输出。
- 未确认退出的会话状态由共享令牌派生密钥加密后保存在 VPS `/opt/sport-relay/state/`（0700/0600），供事后"退出云端账号"核对；确认退出即删除，超过 7 天自动清理。Render 侧加密会话仍按原逻辑保存 Neon。
- 限频、预约锁、报告脱敏语义与本地模式相同；`/api/diagnose` 同样经中继执行。部署后 /health 应显示 release=multi-school-v12-vps-relay、runner_mode=ssh。
- 本地检查增加 `python -m unittest test_runner_remote`（假 SSH 通道，不连 VPS 或运动世界）。中继已完成的部署验收：有效 ping、错误令牌拒绝、超长载荷拒绝、强制命令锁死（shell 命令不可达）、二进制以 sportrelay 可执行、状态加密往返。

## 自托管部署（替代 Render）

同一份代码可直接跑在国内机器上，公网入口交给 Cloudflare Tunnel，因此不需要备案、不开放任何入站 Web 端口。

- `BIND_HOST`（默认 `0.0.0.0`）：自托管设 `127.0.0.1`，只允许本机/Tunnel 访问，机房扫描不到任何端口。
- `PUBLIC_ORIGIN`：对外域名（如 `https://你的域名`），用于 Origin 校验；未设时回落到 `RENDER_EXTERNAL_URL`。**域名确定前不要对外开放提交**，否则 POST 会因 Origin 不匹配被拒。
- `DATABASE_URL`：新增允许 `127.0.0.1`/`localhost`/`::1` 的本机 Postgres（自托管）；其它主机仍只接受 Neon，防止连接串被改指他处。
- `MAX_ACTIVE_RUNS`：并发上限，1-8 的整数，默认 2；非法值保守退回 1；`/health` 会显示实际值。排障期的强制单任务已解除，仍受 `resources_available()` 内存水位保护。
- `RUNNER_MODE`：同机执行用 `local`（本机出口即国内出口，无需中继）；跨机时用 `ssh` 走 sport-relay。
- 实测容量（8 核 / 可用 6.7G 机器）：二进制单实例峰值 16.4MB，8 个并发启动无压力。并发上限的真实约束是上游风控与内存水位，不是 CPU。
