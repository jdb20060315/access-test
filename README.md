# 个人手机账号检测

此版本提供访问口令、个人账号检测、自动退出及退出核对。不生成或提交运动记录。私有环境变量未配置时不会接受账号请求。

可上传的九个文件：server.py、prepare_runtime.py、check_database.py、personal_service.py、personal_store.py、personal_test.html、neko_account_check.py、requirements.txt、README.md。不要上传 __pycache__、报告、二进制或任何私有配置。

Render 构建命令保持：`pip install -r requirements.txt && python prepare_runtime.py`
启动命令保持：`python server.py`。保持 Free 套餐。

DATABASE_URL 保持不动。另需 ACCESS_CODE（至少24字符）、ACCESS_EXPIRES（Unix秒数）、PERSONAL_ACCOUNT_SHA256（允许账号的SHA256）、STATE_ENCRYPTION_KEY（Fernet密钥）。仅在 Render 控制台设置，不提交仓库。单独生成的 Render私有配置.env 不属于上传包。

只配置自己的账号。访问口令不要分享给他人。加密密钥不能随意更换，否则未确认退出的加密会话无法恢复。

部署后 /health 的 personal_test_ready=true 才能进行账号检测。主页输入访问口令后显示账号表单。账号密码由 Render 上的程序处理，登录、学期查询和退出请求发往运动世界；用户需在表单中自行勾选同意。Neon 保存脱敏结果及必要的加密会话，不保存账号密码。

检测前先向数据库写入单次预约，同一入口不重复检测，不自动重试。服务中断或结果不明时保留预约。进程结束不等于远端账号退出；只有旧令牌被上游明确拒绝才报告退出已确认。未确认的临时会话经独立密钥加密后保存，页面提供再次核对退出的操作。

公开仓库接入需手动部署最新提交。部署成功不代表实际账号检测成功。本版本尚未接入运动提交，原服务器上的记录和防重复状态保持不动。
