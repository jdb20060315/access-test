# 免费境外访问测试

此目录只含无账号数据的连通性测试。不要上传原项目、密码、证书私钥或服务器配置。

1. 注册 GitHub 和 Render 账号；如平台要求支付方式，先停止，不开通付费实例。
2. 新建 GitHub 仓库，仅上传本目录的 server.py、README.md、requirements.txt。
3. Render 控制台选择 New → Web Service，连接该仓库。
4. 运行环境选 Python 3，Build Command 填 `pip install -r requirements.txt`，Start Command 填 `python server.py`。
5. 实例类型选择 Free，Health Check Path 填 `/health`。不创建数据库、磁盘或付费资源。
6. 部署成功后用手机直接打开生成的 HTTPS 地址，分别测试移动网络和 Wi-Fi。

本测试不需要自定义域名。免费服务休眠后的首次打开可能需要约一分钟；免费额度耗尽或账户验证也可能影响服务。尚未验证国内可达性，不代表生产部署完成。
