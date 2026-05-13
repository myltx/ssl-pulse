# Alibaba Cloud Linux 3 部署 SSL 证书监控网页

## 0. 本地启动（macOS/Linux）

在项目目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
PORT=2026 DASHBOARD_PASSWORD='你的访问密码' python3 check_ssl.py
```

访问：

```text
http://127.0.0.1:2026
```

说明：

- 如果不设置 `DASHBOARD_PASSWORD`，默认密码是 `admin123456`（不建议用于生产）。
- 每次新开终端后，先执行 `source .venv/bin/activate` 再运行脚本。

## 1. 需要上传哪些文件

建议在服务器目录 `/root/ssl-pulse` 下部署，最少上传这些文件：

- `check_ssl.py`
- `requirements.txt`
- `deploy_alinux3.sh`

可选上传文件：

- `domains.json`（预置域名列表，不传则程序首次启动自动创建）
- `.ssl_pulse.env.example`（环境变量模板）
- `update_python_alinux3.sh`（需要先升级 Python 时使用）
- `DEPLOY_ALIBABA_CLOUD_LINUX3.md`（部署说明）
- `ssl-monitor.service`（仅手工部署时使用，一键脚本不依赖这个文件）
- `ssl-monitor-check.service`（仅手工部署时使用，一键脚本不依赖这个文件）
- `ssl-monitor-check.timer`（仅手工部署时使用，一键脚本不依赖这个文件）

## 2. 推荐方式：一键脚本后台部署

在服务器执行：

```bash
mkdir -p /root/ssl-pulse
cd /root/ssl-pulse
# 先把文件上传到这个目录，再执行
chmod +x deploy_alinux3.sh
sudo bash deploy_alinux3.sh
```

说明：脚本默认把“脚本所在目录”作为 `APP_DIR`，所以 `.sh` 和 `.py` 放同一目录即可直接执行。  
脚本默认使用阿里云 PyPI 镜像，并会按 Python 版本自动安装兼容的 Flask 版本（例如 Python 3.6 会使用 Flask 2.0.3）。
脚本会自动为页面生成访问密码（若未提供 `DASHBOARD_PASSWORD`），并写入 `${APP_DIR}/.ssl_pulse.env`。

如果要自定义端口：

```bash
sudo env PORT=5001 APP_DIR=/root/ssl-pulse bash deploy_alinux3.sh
```

如果要指定访问密码：

```bash
sudo env DASHBOARD_PASSWORD='你的访问密码' bash deploy_alinux3.sh
```

如果你要使用其他 Python 包镜像：

```bash
sudo env PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn bash deploy_alinux3.sh
```

脚本会自动完成：

- 安装 `python3` 与 `python3-pip`
- 创建虚拟环境 `.venv` 并安装依赖
- 生成 systemd 服务 `ssl-monitor`
- 生成定时检测任务 `ssl-monitor-check.timer`
- 设置开机自启并立即启动
- 如果 `firewalld` 已启用，自动放行端口

部署完成后访问：

```text
http://<服务器公网IP>:2026
```

首次部署后请查看脚本输出中的密码信息，登录页地址与仪表盘地址相同。

## 3. 升级 Python（可选）

如果服务器 Python 太老，可先运行：

```bash
chmod +x update_python_alinux3.sh
sudo bash update_python_alinux3.sh
```

指定版本示例（如 3.11）：

```bash
sudo env TARGET_PYTHON=3.11 bash update_python_alinux3.sh
```

随后把输出的 `PYTHON_BIN` 传给部署脚本，例如：

```bash
sudo env PYTHON_BIN=/usr/bin/python3.11 bash deploy_alinux3.sh
```

## 4. 手工方式（备选）

```bash
sudo dnf install python3 python3-pip -y
cd /root/ssl-pulse
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo cp ssl-monitor.service /etc/systemd/system/ssl-monitor.service
sudo cp ssl-monitor-check.service /etc/systemd/system/ssl-monitor-check.service
sudo cp ssl-monitor-check.timer /etc/systemd/system/ssl-monitor-check.timer
sudo tee /root/ssl-pulse/.ssl_pulse.env >/dev/null <<'EOF'
PORT="2026"
PYTHONUNBUFFERED="1"
DASHBOARD_PASSWORD="请改成你的密码"
FLASK_SECRET_KEY="请改成随机长字符串"
SESSION_TTL_MINUTES="720"
ALERT_DAYS="30"
ALERT_MILESTONES="30,15,7,3,1"
ENABLE_DAILY_REMINDER="true"
CHECK_ON_CALENDAR="hourly"
SMTP_SERVER=""
SMTP_PORT="587"
SMTP_USER=""
SMTP_PASSWORD=""
TO_EMAIL=""
DOMAINS=""
EOF
sudo chmod 600 /root/ssl-pulse/.ssl_pulse.env
sudo systemctl daemon-reload
sudo systemctl enable --now ssl-monitor
sudo systemctl enable --now ssl-monitor-check.timer
sudo systemctl start ssl-monitor-check.service
sudo systemctl status ssl-monitor --no-pager
sudo systemctl status ssl-monitor-check.timer --no-pager
```

说明：

- `ssl-monitor-check.timer` 模板默认每小时执行一次。
- 如果你想改频率，请直接编辑 `/etc/systemd/system/ssl-monitor-check.timer` 里的 `OnCalendar=`，然后执行 `sudo systemctl daemon-reload && sudo systemctl restart ssl-monitor-check.timer`。

## 5. 安全组与防火墙

阿里云 ECS 安全组入方向放行：

- 协议：TCP
- 端口范围：`2026/2026`（或你的自定义端口）
- 授权对象：先 `0.0.0.0/0` 验证，再收敛到固定来源 IP

如果服务器启用了 firewalld：

```bash
sudo firewall-cmd --permanent --add-port=2026/tcp
sudo firewall-cmd --reload
```

## 6. 配置项管理（推荐用环境变量）

部署后，统一在环境文件中修改配置：

```bash
sudo cp /root/ssl-pulse/.ssl_pulse.env.example /root/ssl-pulse/.ssl_pulse.env
sudo chmod 600 /root/ssl-pulse/.ssl_pulse.env
sudo vi /root/ssl-pulse/.ssl_pulse.env
```

常用变量：

- `DASHBOARD_PASSWORD`：页面登录密码
- `FLASK_SECRET_KEY`：会话签名密钥
- `SESSION_TTL_MINUTES`：登录会话有效期（分钟）
- `SMTP_SERVER`（例如 `smtp.qq.com`）
- `SMTP_PORT`（通常 `587`）
- `SMTP_USER`
- `SMTP_PASSWORD`
- `TO_EMAIL`
- `ALERT_DAYS`（默认 30）
- `ALERT_MILESTONES`（默认 `30,15,7,3,1`）
- `ENABLE_DAILY_REMINDER`（`true/false`）
- `CHECK_ON_CALENDAR`（供一键部署脚本生成 timer，默认 `hourly`）
- `DOMAINS`（可选，逗号分隔；通常建议用页面管理和 `domains.json`）

修改后重启服务生效：

```bash
sudo systemctl restart ssl-monitor
```

如果你修改了 `CHECK_ON_CALENDAR`，需要重新执行一键部署脚本生成新的 timer 文件，或手动编辑 `/etc/systemd/system/ssl-monitor-check.timer` 后 `daemon-reload`。

说明：

- 仅当 SMTP 相关字段都非空时才会发送邮件。
- 默认提醒策略是“里程碑 + 每日兜底”。
- 后台定时任务负责主动检测和发邮件，打开网页只做展示，不再作为提醒触发条件。
- 提醒状态存储在 `alert_state.json`。

## 7. 常用运维命令

```bash
sudo systemctl status ssl-monitor --no-pager
sudo systemctl restart ssl-monitor
sudo journalctl -u ssl-monitor -f
sudo journalctl -u ssl-monitor-check.service -f
sudo systemctl status ssl-monitor-check.timer --no-pager
sudo systemctl start ssl-monitor-check.service
ss -lntp | grep 2026
```

## 8. 升级发布

上传新版本 `check_ssl.py` 后执行：

```bash
cd /root/ssl-pulse
sudo systemctl restart ssl-monitor
sudo systemctl restart ssl-monitor-check.timer
sudo systemctl start ssl-monitor-check.service
```
