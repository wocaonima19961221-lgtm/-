# Render 部署步骤

1. 把当前项目上传到 GitHub。
2. 打开 Render，选择 New Web Service。
3. 连接这个 GitHub 仓库。
4. Render 会读取 `render.yaml` 自动配置。
5. 如果手动填写，使用：

```bash
Build Command:
pip install -r requirements.txt

Start Command:
uvicorn web_server:app --host 0.0.0.0 --port $PORT
```

部署完成后，Render 会给你一个公网网址，例如：

```text
https://crypto-intel.onrender.com
```

注意：云服务器所在地区可能影响 Binance、KuCoin、RSS 新闻源的访问。如果某个源被限制，页面会显示可用数据或空状态提示。
