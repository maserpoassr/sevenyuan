# 单商品库存监控（LDXP + WxPusher）

本项目用于监控 `https://pay.ldxp.cn/shop/GPT` 的单个商品库存，检测到有货后通过 WxPusher 发送通知。

## 功能

- 仅监控 1 个目标商品（推荐用 `goods_key` 锁定）
- 库存字段直接读取接口中的 `extend.stock_count`
- 缺货到有货立刻推送
- 冷却时间防止重复推送
- 本地 `state.json` 做状态持久化
- 日志输出到控制台和 `monitor.log`
- 内置反爬兜底（`curl_cffi` 浏览器指纹请求）

## 接口方案

脚本使用下列接口（比页面解析更稳定）：

- `POST /shopApi/Shop/goodsList`

请求示例：

```json
{
  "token": "GPT",
  "goods_type": "card",
  "current": 1,
  "pageSize": 200
}
```

## 本地运行

1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. 配置

- 编辑 `config.yaml`
- 至少填写：
  - `wxpusher.app_token`
  - `wxpusher.uids`（或 `topic_ids`）

3. 启动

```bash
python monitor_stock.py -c config.yaml
```

单次检查（用于联调验证）：

```bash
python monitor_stock.py -c config.yaml --once
```

## 配置说明（config.yaml）

- `shop.token`: 店铺 token（当前为 `GPT`）
- `shop.goods_type`: 商品类型（当前目标是 `card`）
- `shop.target_goods_key`: 目标商品 key（当前示例 `q8za45`）
- `shop.target_goods_name`: 名称兜底匹配
- `poll.interval_sec`: 轮询间隔
- `notify.cooldown_sec`: 通知冷却时间（秒）
- `notify.send_on_start_if_in_stock`: 启动时若有货是否立即通知
- `stock_display.few_max`: "库存少量" 的数量上限（默认 3）
- `stock_display.normal_max`: "库存一般" 的数量上限（默认 20）
- `runtime.state_file`: 状态文件路径
- `runtime.log_file`: 日志文件路径

库存状态显示规则：

- `stock_count <= 0` -> `缺货`
- `1 ~ few_max` -> `库存少量`
- `few_max+1 ~ normal_max` -> `库存一般`
- `> normal_max` -> `库存充足`

## 爬云/爪云服务器部署（systemd）

以下以 Linux 为例：

1. 上传项目到服务器，例如 `/opt/stock-monitor`
2. 安装 Python 依赖（同本地运行）
3. 修改 `stock-monitor.service` 中路径（若你目录不同）
4. 安装服务

```bash
sudo cp stock-monitor.service /etc/systemd/system/stock-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now stock-monitor
```

5. 查看运行状态

```bash
sudo systemctl status stock-monitor
sudo journalctl -u stock-monitor -f
```

## 常见问题

- 找不到商品：优先确认 `target_goods_key` 是否正确
- 一直不通知：检查 `stock_count` 是否大于 0，以及冷却时间是否生效
- WxPusher 失败：确认 `app_token`、`uids/topic_ids` 是否有效

## GitHub Actions 构建公共镜像（推荐）

你可以把本项目推到 GitHub，然后让 Actions 自动构建并推送到 GHCR：

- 工作流文件：`.github/workflows/docker-image.yml`
- 镜像地址格式：`ghcr.io/<你的用户名>/<仓库名>:latest`

### 使用步骤

1. 创建 GitHub 仓库并推送代码（分支建议 `main`）
2. 在仓库 `Settings -> Actions -> General` 确认 Actions 可运行
3. push 到 `main` 后会自动构建并推送镜像
4. 到仓库 `Packages` 把镜像包可见性改为 `Public`

### 爪云部署镜像

在爪云中填写镜像：

`ghcr.io/<你的用户名>/<仓库名>:latest`

容器启动命令：

`python monitor_stock.py -c /app/config.yaml`

建议挂载持久化文件（避免重启丢状态）：

- `/app/config.yaml`
- `/app/state.json`
- `/app/monitor.log`

最小环境要求：

- 网络可访问 `pay.ldxp.cn` 和 `wxpusher.zjiecode.com`
