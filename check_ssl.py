from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
import re
import smtplib
import socket
import ssl
import threading
from email.mime.text import MIMEText
from urllib.parse import urlparse

from flask import Flask, flash, redirect, render_template_string, request, session, url_for


def get_env_int(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def get_env_bool(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_env_list(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


# ================= 配置区 =================
DOMAINS = get_env_list("DOMAINS", ["www.baidu.com"])

# 邮件配置（如果需要开启自动发邮件，请填写以下信息）
ALERT_DAYS = get_env_int("ALERT_DAYS", 30)
ALERT_MILESTONES = get_env_list("ALERT_MILESTONES", ["30", "15", "7", "3", "1"])
ENABLE_DAILY_REMINDER = get_env_bool("ENABLE_DAILY_REMINDER", True)
SMTP_SERVER = os.getenv("SMTP_SERVER", "")  # 例如 smtp.qq.com
SMTP_PORT = get_env_int("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
TO_EMAIL = os.getenv("TO_EMAIL", "")

# 页面访问认证配置
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin123456")
SESSION_TTL_MINUTES = get_env_int("SESSION_TTL_MINUTES", 720)
# ===========================================

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "ssl-pulse-dev-secret")
app.permanent_session_lifetime = timedelta(minutes=SESSION_TTL_MINUTES)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOMAINS_FILE = os.path.join(BASE_DIR, "domains.json")
ALERT_STATE_FILE = os.path.join(BASE_DIR, "alert_state.json")
DOMAINS_LOCK = threading.Lock()
ALERT_STATE_LOCK = threading.Lock()


def normalize_domain(raw_value):
    domain = (raw_value or "").strip().lower()
    if not domain:
        return ""

    # 兼容用户粘贴 http:// 或 https://
    if "://" in domain:
        domain = urlparse(domain).hostname or ""
    else:
        domain = domain.split("/", 1)[0]

    if not domain:
        return ""

    # 兼容 host:port 输入，仅保留 host
    if ":" in domain and domain.count(":") == 1:
        domain = domain.split(":", 1)[0]

    if not re.fullmatch(r"[a-z0-9.-]{1,253}", domain):
        return ""

    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return ""

    return domain


def save_domains(domains):
    with open(DOMAINS_FILE, "w", encoding="utf-8") as fp:
        json.dump(domains, fp, ensure_ascii=False, indent=2)


def load_domains():
    if not os.path.exists(DOMAINS_FILE):
        default_domains = sorted({normalize_domain(d) for d in DOMAINS if normalize_domain(d)})
        save_domains(default_domains)
        return default_domains

    try:
        with open(DOMAINS_FILE, "r", encoding="utf-8") as fp:
            raw_domains = json.load(fp)
        if not isinstance(raw_domains, list):
            raise ValueError("domains.json 内容必须是数组")
        domains = sorted(
            {
                normalize_domain(item)
                for item in raw_domains
                if isinstance(item, str) and normalize_domain(item)
            }
        )
        return domains
    except Exception:
        fallback_domains = sorted({normalize_domain(d) for d in DOMAINS if normalize_domain(d)})
        save_domains(fallback_domains)
        return fallback_domains


def get_domains():
    with DOMAINS_LOCK:
        return load_domains()


def is_authenticated():
    return session.get("authenticated") is True


def is_safe_next_url(target):
    return isinstance(target, str) and target.startswith("/") and not target.startswith("//")


@app.before_request
def require_login():
    endpoint = request.endpoint
    public_endpoints = {"login", "static"}
    if endpoint in public_endpoints:
        return None

    if not is_authenticated():
        next_path = request.path if request.path else "/"
        return redirect(url_for("login", next=next_path))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_authenticated():
        return redirect(url_for("index"))

    if request.method == "POST":
        password = (request.form.get("password") or "").strip()
        next_target = request.form.get("next") or request.args.get("next") or "/"
        if password == DASHBOARD_PASSWORD:
            session["authenticated"] = True
            session.permanent = True
            target = next_target if is_safe_next_url(next_target) else url_for("index")
            return redirect(target)
        flash("密码错误，请重试")

    next_target = request.args.get("next") or "/"
    login_template = """
    <!DOCTYPE html>
    <html>
    <head>
    <title>登录 - SSL 证书监控</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            --bg-0: #060b18;
            --bg-1: #0d1530;
            --bg-2: #1b2a52;
            --panel: rgba(245, 250, 255, 0.82);
            --text: #10203c;
            --muted: #5e7197;
            --primary: #2563eb;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            padding: 16px;
            font-family: "SF Pro Display", "Manrope", "PingFang SC", "Microsoft YaHei", sans-serif;
            background:
                radial-gradient(circle at 12% 10%, rgba(89, 165, 255, 0.35), transparent 42%),
                radial-gradient(circle at 88% 90%, rgba(68, 202, 186, 0.28), transparent 45%),
                linear-gradient(145deg, var(--bg-0), var(--bg-1) 52%, var(--bg-2));
        }
        .card {
            width: min(420px, 100%);
            background: var(--panel);
            border: 1px solid rgba(188, 209, 244, 0.45);
            border-radius: 18px;
            padding: 22px;
            box-shadow: 0 18px 40px rgba(6, 11, 24, 0.22);
            backdrop-filter: blur(12px);
        }
        h1 { margin: 0; font-size: 25px; color: var(--text); letter-spacing: 0.2px; }
        p { margin: 8px 0 16px; color: var(--muted); font-size: 14px; }
        input[type="password"] {
            width: 100%;
            border: 1px solid #bfd0ec;
            border-radius: 10px;
            padding: 10px 12px;
            font-size: 15px;
            margin-bottom: 12px;
            background: #fff;
        }
        button {
            width: 100%;
            border: 0;
            border-radius: 10px;
            padding: 10px 12px;
            font-size: 15px;
            cursor: pointer;
            background: linear-gradient(135deg, #2563eb, #1d4ed8);
            color: #fff;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        button:hover {
            transform: translateY(-1px);
            box-shadow: 0 10px 20px rgba(37, 99, 235, 0.24);
        }
        .msg {
            margin-bottom: 10px;
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid #f5c6c2;
            background: #fff0ef;
            color: #b42318;
            font-size: 14px;
        }
    </style>
    </head>
    <body>
        <div class="card">
            <h1>SSL 证书监控登录</h1>
            <p>请输入访问密码后进入仪表盘。</p>
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    {% for msg in messages %}
                        <div class="msg">{{ msg }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            <form method="post">
                <input type="hidden" name="next" value="{{ next_target }}">
                <input type="password" name="password" placeholder="访问密码" autocomplete="current-password" required>
                <button type="submit">登录</button>
            </form>
        </div>
    </body>
    </html>
    """
    return render_template_string(login_template, next_target=next_target)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def load_alert_state():
    if not os.path.exists(ALERT_STATE_FILE):
        return {}
    try:
        with open(ALERT_STATE_FILE, "r", encoding="utf-8") as fp:
            raw_state = json.load(fp)
        return raw_state if isinstance(raw_state, dict) else {}
    except Exception:
        return {}


def save_alert_state(state):
    with open(ALERT_STATE_FILE, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2)


def remove_alert_state(domain):
    with ALERT_STATE_LOCK:
        state = load_alert_state()
        if domain in state:
            state.pop(domain, None)
            save_alert_state(state)


def parse_milestones():
    milestones = []
    for value in ALERT_MILESTONES:
        try:
            day = int(value)
            if day > 0:
                milestones.append(day)
        except Exception:
            continue
    return sorted(set(milestones), reverse=True)


def send_alert(domain, expiry_date, days_left, reason):
    try:
        msg = MIMEText(
            "\n".join(
                [
                    f"域名: {domain}",
                    f"证书过期时间: {expiry_date}",
                    f"当前剩余天数: {days_left}",
                    f"提醒类型: {reason}",
                    "请及时处理。",
                ]
            )
        )
        msg["Subject"] = f"SSL 证书预警: {domain}（{reason}）"
        msg["From"] = SMTP_USER
        msg["To"] = TO_EMAIL

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [TO_EMAIL], msg.as_string())
        server.quit()
        print(f"[邮件] 已发送预警: {domain} | {reason} | 剩余{days_left}天")
        return True
    except Exception as exc:
        print(f"[邮件错误]: {exc}")
        return False


def maybe_send_alert(domain, expiry_date, days_left):
    if days_left <= 0:
        return

    email_ready = all([SMTP_SERVER, SMTP_USER, SMTP_PASSWORD, TO_EMAIL])
    if not email_ready:
        return

    milestones = parse_milestones()
    today = datetime.now().strftime("%Y-%m-%d")
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with ALERT_STATE_LOCK:
        state = load_alert_state()
        domain_state = state.get(domain, {})
        raw_sent = domain_state.get("sent_milestones", [])
        sent_milestones = set()
        for value in raw_sent:
            try:
                sent_milestones.add(int(value))
            except Exception:
                continue

        # 同一域名同一天最多发送一封，避免多次刷新导致重复提醒
        if domain_state.get("last_sent_date") == today:
            return

        due_milestone = None
        for day in milestones:
            if days_left <= day and day not in sent_milestones:
                due_milestone = day
                break

        due_daily = ENABLE_DAILY_REMINDER and days_left <= ALERT_DAYS
        if due_milestone is None and not due_daily:
            return

        reason = (
            f"里程碑提醒（<= {due_milestone} 天）"
            if due_milestone is not None
            else "每日提醒"
        )
        if not send_alert(domain, expiry_date, days_left, reason):
            return

        if due_milestone is not None:
            sent_milestones.add(due_milestone)

        domain_state["sent_milestones"] = sorted(sent_milestones, reverse=True)
        domain_state["last_sent_date"] = today
        domain_state["last_sent_at"] = now_text
        domain_state["last_sent_reason"] = reason
        domain_state["last_sent_days_left"] = days_left
        state[domain] = domain_state
        save_alert_state(state)


def get_cert_expiry(domain):
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                expiry_str = cert["notAfter"]
                expiry_date = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
                days_left = (expiry_date - datetime.now()).days

                maybe_send_alert(domain, expiry_date, days_left)

                return {
                    "domain": domain,
                    "expiry": expiry_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "days_left": days_left,
                    "status": (
                        "正常"
                        if days_left > ALERT_DAYS
                        else "即将过期"
                        if days_left > 0
                        else "已过期"
                    ),
                    "error": None,
                }
    except Exception as exc:
        return {
            "domain": domain,
            "expiry": "N/A",
            "days_left": "N/A",
            "status": "连接失败",
            "error": str(exc),
        }


def check_all_domains(domains):
    if not domains:
        return []
    max_workers = min(8, len(domains))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(get_cert_expiry, domains))


@app.post("/domains")
def add_domain():
    domain = normalize_domain(request.form.get("domain"))
    if not domain:
        flash("域名格式不正确，请输入例如 example.com")
        return redirect(url_for("index"))

    with DOMAINS_LOCK:
        domains = load_domains()
        if domain in domains:
            flash(f"域名已存在：{domain}")
            return redirect(url_for("index"))
        domains.append(domain)
        domains = sorted(domains)
        save_domains(domains)

    flash(f"已添加域名：{domain}")
    return redirect(url_for("index"))


@app.post("/domains/delete")
def delete_domain():
    domain = normalize_domain(request.form.get("domain"))
    if not domain:
        flash("删除失败：域名参数为空")
        return redirect(url_for("index"))

    with DOMAINS_LOCK:
        domains = load_domains()
        if domain not in domains:
            flash(f"未找到域名：{domain}")
            return redirect(url_for("index"))
        domains = [item for item in domains if item != domain]
        save_domains(domains)

    remove_alert_state(domain)
    flash(f"已删除域名：{domain}")
    return redirect(url_for("index"))


@app.route("/")
def index():
    domains = get_domains()
    results = check_all_domains(domains)

    def result_sort_key(item):
        days_left = item.get("days_left")
        if isinstance(days_left, int):
            return (0, days_left)
        return (1, 10**9)

    results = sorted(results, key=result_sort_key)
    milestones = parse_milestones()
    stats = {
        "total": len(results),
        "normal": sum(1 for r in results if r["status"] == "正常"),
        "warning": sum(1 for r in results if r["status"] == "即将过期"),
        "expired": sum(1 for r in results if r["status"] == "已过期"),
        "failed": sum(1 for r in results if r["status"] == "连接失败"),
    }

    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
    <title>SSL 证书监控仪表盘</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            --bg-0: #060b18;
            --bg-1: #0d1530;
            --bg-2: #1b2a52;
            --text: #0f1c37;
            --muted: #5f7094;
            --line: rgba(95, 122, 170, 0.26);
            --panel: rgba(245, 250, 255, 0.74);
            --panel-strong: rgba(255, 255, 255, 0.88);
            --brand: #2563eb;
            --brand-soft: rgba(37, 99, 235, 0.12);
            --ok: #138a36;
            --warn: #b06f00;
            --danger: #b42318;
            --shadow: 0 18px 40px rgba(6, 11, 24, 0.24);
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            font-family: "SF Pro Display", "Manrope", "PingFang SC", "Microsoft YaHei", sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at 7% 8%, rgba(92, 180, 255, 0.30), transparent 42%),
                radial-gradient(circle at 90% 92%, rgba(77, 212, 166, 0.28), transparent 44%),
                linear-gradient(145deg, var(--bg-0), var(--bg-1) 52%, var(--bg-2));
            padding: 18px 12px;
        }
        .container {
            width: min(1240px, 100%);
            margin: 0 auto;
        }
        .header {
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            margin-bottom: 14px;
            padding: 12px 14px;
            border-radius: 16px;
            border: 1px solid rgba(182, 209, 255, 0.24);
            background: rgba(255, 255, 255, 0.10);
            backdrop-filter: blur(12px);
            color: #eff5ff;
        }
        .logout-form { margin: 0; }
        .btn-logout {
            border: 1px solid rgba(255, 255, 255, 0.5);
            background: rgba(255, 255, 255, 0.14);
            color: #f3f7ff;
            border-radius: 10px;
            padding: 7px 11px;
            cursor: pointer;
            font-size: 13px;
            transition: background 0.2s ease, transform 0.2s ease;
        }
        .btn-logout:hover { background: rgba(255, 255, 255, 0.24); transform: translateY(-1px); }
        .title {
            margin: 0;
            font-size: 27px;
            letter-spacing: 0.2px;
            font-weight: 700;
        }
        .sub {
            margin: 4px 0 0;
            color: #cfddfa;
            font-size: 13px;
        }
        .panel {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 18px;
            box-shadow: var(--shadow);
            backdrop-filter: blur(12px);
            padding: 14px;
            animation: rise 0.35s ease both;
        }
        .stats {
            margin-top: 12px;
            display: grid;
            grid-template-columns: repeat(5, minmax(110px, 1fr));
            gap: 8px;
        }
        .stat {
            border-radius: 12px;
            padding: 10px;
            background: linear-gradient(180deg, #ffffff, #f8fbff);
            border: 1px solid #d9e6fb;
            position: relative;
            overflow: hidden;
        }
        .stat::before {
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, #3b82f6, #0ea5e9, #10b981);
            opacity: 0.65;
        }
        .stat b {
            display: block;
            font-size: 20px;
            margin-top: 4px;
            letter-spacing: -0.2px;
        }
        .actions {
            margin-top: 14px;
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        input[type="text"] {
            flex: 1;
            min-width: 220px;
            border: 1px solid #bfd0ec;
            border-radius: 10px;
            padding: 10px 12px;
            font-size: 14px;
            background: #fff;
        }
        button {
            border: 0;
            border-radius: 10px;
            padding: 9px 13px;
            font-size: 13px;
            cursor: pointer;
            background: linear-gradient(135deg, #2563eb, #1d4ed8);
            color: #fff;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        button:hover {
            transform: translateY(-1px);
            box-shadow: 0 8px 18px rgba(37, 99, 235, 0.24);
        }
        .btn-del {
            background: #fff;
            color: var(--danger);
            border: 1px solid #f4c9c6;
            padding: 6px 10px;
        }
        .btn-del:hover {
            box-shadow: 0 8px 16px rgba(180, 35, 24, 0.16);
        }
        .msg {
            margin-top: 10px;
            padding: 9px 11px;
            border-radius: 10px;
            background: #e9f2ff;
            color: #1c427b;
            font-size: 13px;
            border: 1px solid #cde0ff;
        }
        .cards {
            margin-top: 12px;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(236px, 1fr));
            gap: 9px;
        }
        .card {
            background: var(--panel-strong);
            border: 1px solid #d7e4fb;
            border-radius: 12px;
            padding: 10px;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            animation: rise 0.35s ease both;
            position: relative;
            overflow: hidden;
        }
        .card::before {
            content: "";
            position: absolute;
            left: 0;
            right: 0;
            top: 0;
            height: 2px;
            background: linear-gradient(90deg, #60a5fa, #2dd4bf);
            opacity: 0.7;
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 24px rgba(35, 62, 111, 0.14);
        }
        .card-head {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            margin-bottom: 6px;
        }
        .domain {
            margin: 0;
            font-size: 15px;
            font-weight: 700;
            overflow-wrap: anywhere;
        }
        .meta {
            margin-top: 6px;
            display: grid;
            grid-template-columns: repeat(2, minmax(100px, 1fr));
            gap: 6px;
        }
        .kv {
            background: var(--brand-soft);
            border: 1px solid #dce8fc;
            border-radius: 8px;
            padding: 6px 8px;
        }
        .kv-label {
            display: block;
            font-size: 11px;
            color: var(--muted);
            margin-bottom: 2px;
        }
        .kv-value {
            font-size: 13px;
            font-weight: 600;
            word-break: break-word;
        }
        .error-detail {
            margin-top: 6px;
            border: 1px solid #e3ebf8;
            background: #fbfdff;
            border-radius: 8px;
            padding: 4px 8px;
        }
        .error-detail summary {
            cursor: pointer;
            color: #3f5883;
            font-size: 12px;
            user-select: none;
        }
        .error-text {
            margin: 6px 0 0;
            white-space: pre-wrap;
            word-break: break-word;
            color: #6a1d17;
            font-size: 12px;
            line-height: 1.45;
        }
        .card-actions {
            margin-top: 8px;
            display: flex;
            justify-content: flex-end;
        }
        .status {
            display: inline-block;
            border-radius: 999px;
            padding: 3px 8px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.2px;
        }
        .ok { color: var(--ok); background: #e9f8ee; }
        .warn { color: var(--warn); background: #fff7e1; }
        .danger { color: var(--danger); background: #fdeceb; }
        .empty {
            text-align: center;
            color: var(--muted);
            padding: 32px 10px;
            background: #fff;
            border-radius: 12px;
            border: 1px dashed #bfd3f3;
        }
        .tiny {
            color: var(--muted);
            font-size: 12px;
        }
        .inline-form { margin: 0; }
        @keyframes rise {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @media (max-width: 900px) {
            .stats { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
            .title { font-size: 22px; }
            .cards { grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); }
        }
        @media (max-width: 560px) {
            .panel { padding: 12px; }
            .stats { grid-template-columns: repeat(2, minmax(94px, 1fr)); }
            .stat b { font-size: 18px; }
            .cards { grid-template-columns: 1fr; }
            .meta { grid-template-columns: 1fr; }
        }
    </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div>
                    <h1 class="title">SSL 证书监控仪表盘</h1>
                    <p class="sub">最后刷新时间：{{ now }}</p>
                </div>
                <form method="post" action="{{ url_for('logout') }}" class="logout-form">
                    <button type="submit" class="btn-logout">退出登录</button>
                </form>
            </div>

            <div class="panel">
                <form method="post" action="{{ url_for('add_domain') }}" class="actions">
                    <input type="text" name="domain" placeholder="输入域名，例如 example.com 或 https://example.com" required>
                    <button type="submit">添加检测域名</button>
                </form>
                <p class="tiny">域名会保存到 {{ domains_file }}，服务重启后仍保留。</p>
                <p class="tiny">
                    当前提醒策略：
                    里程碑 {{ milestones_text }} 天；
                    {% if enable_daily_reminder %}
                    临期（<= {{ alert_days }} 天）每日最多 1 封。
                    {% else %}
                    已关闭每日提醒。
                    {% endif %}
                </p>
                <p class="tiny">排序规则：按剩余天数从小到大，连接失败项排在最后。</p>

                {% with messages = get_flashed_messages() %}
                    {% if messages %}
                        {% for msg in messages %}
                            <div class="msg">{{ msg }}</div>
                        {% endfor %}
                    {% endif %}
                {% endwith %}

                <div class="stats">
                    <div class="stat">总域名<b>{{ stats.total }}</b></div>
                    <div class="stat">正常<b>{{ stats.normal }}</b></div>
                    <div class="stat">即将过期<b>{{ stats.warning }}</b></div>
                    <div class="stat">已过期<b>{{ stats.expired }}</b></div>
                    <div class="stat">连接失败<b>{{ stats.failed }}</b></div>
                </div>

                <div class="cards">
                    {% if results %}
                        {% for res in results %}
                        <article class="card">
                            <div class="card-head">
                                <h3 class="domain">{{ res.domain }}</h3>
                                <span class="status {% if res.status == '正常' %}ok{% elif res.status == '即将过期' %}warn{% else %}danger{% endif %}">
                                    {{ res.status }}
                                </span>
                            </div>
                            <div class="meta">
                                <div class="kv">
                                    <span class="kv-label">过期时间</span>
                                    <span class="kv-value">{{ res.expiry }}</span>
                                </div>
                                <div class="kv">
                                    <span class="kv-label">剩余天数</span>
                                    <span class="kv-value">{{ res.days_left }}</span>
                                </div>
                                <div class="kv">
                                    <span class="kv-label">检测结果</span>
                                    <span class="kv-value">{{ '成功' if not res.error else '异常' }}</span>
                                </div>
                            </div>
                            {% if res.error %}
                            <details class="error-detail">
                                <summary>查看错误信息</summary>
                                <pre class="error-text">{{ res.error }}</pre>
                            </details>
                            {% endif %}
                            <div class="card-actions">
                                <form method="post" action="{{ url_for('delete_domain') }}" class="inline-form">
                                    <input type="hidden" name="domain" value="{{ res.domain }}">
                                    <button class="btn-del" type="submit">删除</button>
                                </form>
                            </div>
                        </article>
                        {% endfor %}
                    {% else %}
                    <div class="empty">当前没有检测域名，请先添加一个域名。</div>
                    {% endif %}
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(
        html_template,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        results=results,
        stats=stats,
        domains_file=DOMAINS_FILE,
        milestones_text="/".join(str(day) for day in milestones) if milestones else "-",
        enable_daily_reminder=ENABLE_DAILY_REMINDER,
        alert_days=ALERT_DAYS,
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "2026"))
    if DASHBOARD_PASSWORD == "admin123456":
        print("[安全提示] 正在使用默认访问密码，请尽快通过环境变量 DASHBOARD_PASSWORD 修改。")
    app.run(host="0.0.0.0", port=port)
