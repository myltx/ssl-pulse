# SSL Pulse

一个基于 Flask 的 SSL 证书监控面板，支持：

- 多域名证书到期检测
- 到期预警邮件（里程碑 + 每日兜底）
- 独立定时检测，不依赖手动打开网页
- Web 页面登录保护
- 页面内新增/删除域名
- Alibaba Cloud Linux 3 一键部署与 systemd 后台运行

## 功能特性

- 证书状态展示：正常 / 即将过期 / 已过期 / 连接失败
- 按剩余天数排序：越快到期越靠前，失败项排最后
- 错误信息折叠显示：需要时点击展开
- 提醒策略可配置：
  - 里程碑提醒（默认 `30,15,7,3,1`）
  - 临期区间内每日最多 1 封
- 仪表盘只负责展示，后台 timer 负责定时检测和发邮件
- 页面可直接查看后台定时检测状态和最近一次邮件发送状态
- 登录会话有效期可配置

## 项目结构

- `check_ssl.py`：主程序（Web + 检测 + 邮件提醒）
- `requirements.txt`：依赖
- `deploy_alinux3.sh`：一键部署脚本（推荐）
- `update_python_alinux3.sh`：Python 升级脚本（可选）
- `ssl-monitor.service`：手工部署时可用的 systemd 模板
- `ssl-monitor-check.service`：手工部署时可用的定时检测 service 模板
- `ssl-monitor-check.timer`：手工部署时可用的定时检测 timer 模板
- `.ssl_pulse.env.example`：环境变量模板
- `DEPLOY_ALIBABA_CLOUD_LINUX3.md`：详细部署文档

## 本地启动

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .ssl_pulse.env.example .ssl_pulse.env
# 编辑 .ssl_pulse.env，至少设置 DASHBOARD_PASSWORD
set -a; source ./.ssl_pulse.env; set +a
python3 check_ssl.py
```

访问：`http://127.0.0.1:2026`

## 服务器部署（Alibaba Cloud Linux 3）

### 1) 推荐：一键部署

```bash
chmod +x deploy_alinux3.sh
sudo bash deploy_alinux3.sh
```

部署完成后：

- 服务名：`ssl-monitor`
- 定时任务：`ssl-monitor-check.timer`
- 环境文件：`/root/ssl-pulse/.ssl_pulse.env`
- 日志查看：`sudo journalctl -u ssl-monitor -f`

### 2) Python 太老时先升级（可选）

```bash
chmod +x update_python_alinux3.sh
sudo bash update_python_alinux3.sh
```

## 配置说明（.ssl_pulse.env）

可参考 `.ssl_pulse.env.example`，常用项：

- `PORT`：监听端口（默认 `2026`）
- `DASHBOARD_PASSWORD`：登录密码
- `FLASK_SECRET_KEY`：会话签名密钥
- `SESSION_TTL_MINUTES`：登录有效期（分钟）
- `ALERT_DAYS`：临期阈值
- `ALERT_MILESTONES`：里程碑天数，逗号分隔
- `ENABLE_DAILY_REMINDER`：是否启用每日提醒（`true/false`）
- `CHECK_ON_CALENDAR`：systemd timer 调度表达式（供部署脚本生成 timer，默认 `hourly`）
- `SMTP_SERVER/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/TO_EMAIL`：邮件配置
- `DOMAINS`：可选，逗号分隔（通常建议通过页面管理）
- `runtime_status.json`：运行时状态文件，记录后台定时检测和最近邮件发送结果

修改配置后重启服务：

```bash
sudo systemctl restart ssl-monitor
```

如果你修改了 `CHECK_ON_CALENDAR`，需要重新生成 timer：

```bash
sudo bash deploy_alinux3.sh
```

手动执行一次后台检测：

```bash
source .venv/bin/activate
python3 check_ssl.py --check-only
```

## 更新应用

```bash
cd /root/ssl-pulse
# 上传覆盖新版本文件后
sudo bash deploy_alinux3.sh
```

## 安全建议

- 不要提交 `.ssl_pulse.env`（包含密码/密钥/SMTP 凭据）
- 使用强密码和随机 `FLASK_SECRET_KEY`
- 建议通过安全组限制访问源 IP
- 公网环境建议配 Nginx + HTTPS 反向代理

## 许可证

可按你的项目需求自行补充（例如 MIT）。
