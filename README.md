# 多学校手机测试部署包

其他账号第一次打开网页时，会看到指南针使用提示，并填写学校所在城市、北纬、东经。提交时在服务端校验坐标，按 GPS 坐标本地近似换算后请求该账号的校园点位。不使用原账号的学校坐标；无需地图 API 密钥。指南针读数和具体学校是否适用仍须由用户自己核对。

每个人使用本人的账号，明确同意服务器处理密码和位置并提交一条模拟记录。用户可选 2–3 公里，并用实测步频（每分钟步数）和步幅（每步米数）估算用时；振幅不能直接决定速度，页面不读取手机传感器。记录是否有效以手机 App 为准。用户填写的坐标不公开，只有在需要退出恢复时连同会话加密保存在 Neon。密码只参与本次内存中的操作，不保存到数据库或仓库。

在 GitHub 仓库中用本包的全部代码文件覆盖对应旧文件，并增加 location_input.py。仓库若有旧的 school_location.json，可以留着，新版不读取它。部署使用原来的 DATABASE_URL 和 STATE_ENCRYPTION_KEY；不要更换加密密钥。ACCESS_CODE、ACCESS_EXPIRES、PERSONAL_ACCOUNT_SHA256 已不用于新版。旧个人检测记录仍保留，且不会被新入口读出。

Render 构建命令：`pip install -r requirements.txt && python prepare_runtime.py`
Render 启动命令：`python server.py`
保持 Free 套餐；不需要另建服务。

免费实例一次只处理一个任务，忙时返回提示。每个浏览器入口只提交一次；同一账号当天（北京时间）禁止再次预约，跨天若仍有未退出会话或结果未知也禁止再次预约。任何超时或未知结果不自动重试。

部署后打开 /health，release 应为 multi-school-v4，multi_account_ready 应为 true。尚未用其他学校账号做真实提交验证。
