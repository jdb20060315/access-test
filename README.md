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
