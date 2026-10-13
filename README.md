# 境外后台兼容性测试

仅检查官方固定版本 Linux 程序能否启动。不接受账号、密码，不提交记录。包校验不匹配时不会执行下载的程序。

在现有 access-test 仓库上传本目录的四个文件：server.py、prepare_runtime.py、requirements.txt、README.md。

Render 设置：

- Build Command：`python prepare_runtime.py`
- Start Command：`python server.py`
- 实例：保留 Free。
- 环境变量：无需填写账号数据。

采用公开仓库地址接入的服务需在 Render 点击 Manual Deploy → Deploy latest commit。

部署后打开原链接，查看“后台程序启动检查通过”及 runtime_verified。检查未通过也会显示报告，便于排查。

注意：现有账号会话、防重复提交记录存放在服务器文件中。Render 免费环境休眠或重启会丢失这些文件，因此本阶段不接入真实账号任务。免费 Render 数据库仅保留 30 天，也不适合作为长期状态保存方案。需要在迁移实际账号功能前确定可靠的持久化方案。
