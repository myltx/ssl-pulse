import argparse
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
CHECK_ON_CALENDAR = (os.getenv("CHECK_ON_CALENDAR", "hourly") or "hourly").strip()

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
RUNTIME_STATE_FILE = os.path.join(BASE_DIR, "runtime_status.json")
DOMAINS_LOCK = threading.Lock()
ALERT_STATE_LOCK = threading.Lock()
RUNTIME_STATE_LOCK = threading.Lock()


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


def load_runtime_state():
    if not os.path.exists(RUNTIME_STATE_FILE):
        return {}
    try:
        with open(RUNTIME_STATE_FILE, "r", encoding="utf-8") as fp:
            raw_state = json.load(fp)
        return raw_state if isinstance(raw_state, dict) else {}
    except Exception:
        return {}


def save_runtime_state(state):
    with open(RUNTIME_STATE_FILE, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2)


def update_runtime_state(mutator):
    with RUNTIME_STATE_LOCK:
        state = load_runtime_state()
        mutator(state)
        save_runtime_state(state)


def record_email_status(status, domain, days_left, reason, error=None):
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def apply_update(state):
        email_state = state.get("email", {})
        email_state["last_attempt_status"] = status
        email_state["last_attempt_at"] = now_text
        email_state["last_attempt_domain"] = domain
        email_state["last_attempt_reason"] = reason
        email_state["last_attempt_days_left"] = days_left
        email_state["last_attempt_error"] = error
        email_state["smtp_server"] = SMTP_SERVER
        email_state["to_email"] = TO_EMAIL

        if status == "success":
            email_state["last_success_at"] = now_text
            email_state["last_success_domain"] = domain
            email_state["last_success_reason"] = reason
            email_state["last_success_days_left"] = days_left
        elif error:
            email_state["last_failure_at"] = now_text
            email_state["last_failure_domain"] = domain
            email_state["last_failure_reason"] = reason
            email_state["last_failure_days_left"] = days_left
            email_state["last_failure_error"] = error

        state["email"] = email_state

    update_runtime_state(apply_update)


def infer_schedule_seconds(schedule_text):
    mapping = {
        "minutely": 60,
        "hourly": 3600,
        "daily": 86400,
        "weekly": 7 * 86400,
    }
    return mapping.get((schedule_text or "").strip().lower())


def build_timer_panel(runtime_state):
    check_state = runtime_state.get("scheduled_check", {})
    schedule_text = CHECK_ON_CALENDAR or "hourly"
    expected_seconds = infer_schedule_seconds(schedule_text)
    last_completed_at = check_state.get("last_completed_at")
    last_started_at = check_state.get("last_started_at")
    last_status = check_state.get("last_status")
    last_error = check_state.get("last_error")
    summary_text = check_state.get("summary_text") or "暂无记录"

    health_text = "等待首次执行"
    health_level = "warn"

    if last_completed_at:
        health_text = "最近执行正常"
        health_level = "ok"
        if expected_seconds:
            try:
                completed_at = datetime.strptime(last_completed_at, "%Y-%m-%d %H:%M:%S")
                age_seconds = (datetime.now() - completed_at).total_seconds()
                if age_seconds > max(expected_seconds * 2, 900):
                    health_text = "可能未按计划执行"
                    health_level = "danger"
            except Exception:
                pass

    if last_status == "failed":
        health_text = "最近执行失败"
        health_level = "danger"
    elif last_status == "partial":
        health_text = "最近执行有异常"
        health_level = "warn"

    return {
        "title": "后台定时检测",
        "enabled": True,
        "health_text": health_text,
        "health_level": health_level,
        "schedule_text": schedule_text,
        "last_started_at": last_started_at or "暂无记录",
        "last_completed_at": last_completed_at or "暂无记录",
        "last_status_text": {
            "success": "执行成功",
            "partial": "执行完成，但存在异常项",
            "failed": "执行失败",
        }.get(last_status, "等待首次执行"),
        "summary_text": summary_text,
        "last_error": last_error,
    }


def build_email_panel(runtime_state):
    email_ready = all([SMTP_SERVER, SMTP_USER, SMTP_PASSWORD, TO_EMAIL])
    email_state = runtime_state.get("email", {})
    last_attempt_status = email_state.get("last_attempt_status")

    if not email_ready:
        health_text = "未启用"
        health_level = "warn"
    elif last_attempt_status == "failed":
        health_text = "最近发送失败"
        health_level = "danger"
    elif last_attempt_status == "success":
        health_text = "最近发送成功"
        health_level = "ok"
    else:
        health_text = "已启用，等待触发"
        health_level = "warn"

    return {
        "title": "邮件提醒状态",
        "enabled": email_ready,
        "health_text": health_text,
        "health_level": health_level,
        "recipient_text": TO_EMAIL or "未配置",
        "server_text": f"{SMTP_SERVER}:{SMTP_PORT}" if SMTP_SERVER else "未配置",
        "last_attempt_at": email_state.get("last_attempt_at") or "暂无记录",
        "last_attempt_text": {
            "success": "发送成功",
            "failed": "发送失败",
        }.get(last_attempt_status, "暂无触发记录"),
        "last_attempt_domain": email_state.get("last_attempt_domain") or "-",
        "last_attempt_reason": email_state.get("last_attempt_reason") or "-",
        "last_attempt_error": email_state.get("last_attempt_error"),
    }


def record_scheduled_check_started(domain_count):
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def apply_update(state):
        state["scheduled_check"] = {
            "last_started_at": now_text,
            "last_completed_at": state.get("scheduled_check", {}).get("last_completed_at"),
            "last_status": "running",
            "domain_count": domain_count,
            "summary_text": f"开始检测，共 {domain_count} 个域名",
            "last_error": None,
        }

    update_runtime_state(apply_update)


def record_scheduled_check_finished(stats, results, error=None):
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    failed_count = sum(1 for item in results if item.get("status") == "连接失败")
    if error:
        status = "failed"
        summary_text = f"执行失败：{error}"
    elif failed_count > 0:
        status = "partial"
        summary_text = (
            f"执行完成：正常 {stats['normal']}，即将过期 {stats['warning']}，"
            f"已过期 {stats['expired']}，连接失败 {failed_count}"
        )
    else:
        status = "success"
        summary_text = (
            f"执行完成：正常 {stats['normal']}，即将过期 {stats['warning']}，"
            f"已过期 {stats['expired']}"
        )

    def apply_update(state):
        check_state = state.get("scheduled_check", {})
        check_state["last_completed_at"] = now_text
        check_state["last_status"] = status
        check_state["summary_text"] = summary_text
        check_state["last_error"] = error
        check_state["stats"] = stats
        state["scheduled_check"] = check_state

    update_runtime_state(apply_update)


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
        record_email_status("success", domain, days_left, reason)
        return True
    except Exception as exc:
        print(f"[邮件错误]: {exc}")
        record_email_status("failed", domain, days_left, reason, error=str(exc))
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


def get_cert_expiry(domain, send_alerts=True):
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                expiry_str = cert["notAfter"]
                expiry_date = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
                days_left = (expiry_date - datetime.now()).days

                if send_alerts:
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


def check_all_domains(domains, send_alerts=True):
    if not domains:
        return []
    max_workers = min(8, len(domains))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(lambda domain: get_cert_expiry(domain, send_alerts=send_alerts), domains))


def build_stats(results):
    return {
        "total": len(results),
        "normal": sum(1 for r in results if r["status"] == "正常"),
        "warning": sum(1 for r in results if r["status"] == "即将过期"),
        "expired": sum(1 for r in results if r["status"] == "已过期"),
        "failed": sum(1 for r in results if r["status"] == "连接失败"),
    }


def run_check_cycle(send_alerts):
    domains = get_domains()
    results = check_all_domains(domains, send_alerts=send_alerts)

    def result_sort_key(item):
        days_left = item.get("days_left")
        if isinstance(days_left, int):
            return (0, days_left)
        return (1, 10**9)

    results = sorted(results, key=result_sort_key)
    return results, build_stats(results)


def run_check_once():
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[检查] 开始执行 SSL 检测: {started_at}")
    domain_count = len(get_domains())
    record_scheduled_check_started(domain_count)

    try:
        results, stats = run_check_cycle(send_alerts=True)

        if not results:
            print("[检查] 当前没有配置任何域名，跳过检测。")
            record_scheduled_check_finished(build_stats([]), [], error=None)
            return 0

        for item in results:
            error_suffix = f" | 错误: {item['error']}" if item.get("error") else ""
            print(
                f"[检查] {item['domain']} | 状态: {item['status']} | 过期时间: {item['expiry']} | "
                f"剩余天数: {item['days_left']}{error_suffix}"
            )

        print(
            "[检查] 完成: "
            f"总数={stats['total']} 正常={stats['normal']} 即将过期={stats['warning']} "
            f"已过期={stats['expired']} 连接失败={stats['failed']}"
        )
        record_scheduled_check_finished(stats, results, error=None)
        return 0
    except Exception as exc:
        error_text = str(exc)
        print(f"[检查错误] {error_text}")
        record_scheduled_check_finished(build_stats([]), [], error=error_text)
        return 1


def parse_args():
    parser = argparse.ArgumentParser(description="SSL Pulse 证书监控")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="执行一次检测并退出，适合 systemd timer 或 cron 调用",
    )
    return parser.parse_args()


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
    results, stats = run_check_cycle(send_alerts=False)
    milestones = parse_milestones()
    runtime_state = load_runtime_state()
    timer_panel = build_timer_panel(runtime_state)
    email_panel = build_email_panel(runtime_state)
    current_view = (request.args.get("view") or "all").strip().lower()
    filter_defs = [
        ("all", "全部域名", lambda item: True),
        ("attention", "优先处理", lambda item: item["status"] in {"即将过期", "已过期", "连接失败"}),
        ("warning", "即将过期", lambda item: item["status"] == "即将过期"),
        ("expired", "已过期", lambda item: item["status"] == "已过期"),
        ("failed", "连接失败", lambda item: item["status"] == "连接失败"),
        ("normal", "正常", lambda item: item["status"] == "正常"),
    ]
    filter_lookup = {key: (label, matcher) for key, label, matcher in filter_defs}
    if current_view not in filter_lookup:
        current_view = "all"

    filter_tabs = []
    for key, label, matcher in filter_defs:
        count = sum(1 for item in results if matcher(item))
        filter_tabs.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "active": key == current_view,
                "href": url_for("index", view=key),
            }
        )

    current_filter_label, current_filter_matcher = filter_lookup[current_view]
    filtered_results = [item for item in results if current_filter_matcher(item)]
    priority_results = [item for item in results if item["status"] in {"即将过期", "已过期", "连接失败"}]
    spotlight_results = priority_results[:4]

    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
    <title>SSL 证书监控仪表盘</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            --bg-0: #091121;
            --bg-1: #0e1a31;
            --bg-2: #13284f;
            --text: #d7e5ff;
            --text-soft: #9bb0d8;
            --panel: rgba(12, 22, 42, 0.68);
            --panel-strong: rgba(248, 251, 255, 0.96);
            --line: rgba(154, 184, 232, 0.16);
            --line-strong: rgba(185, 208, 248, 0.34);
            --brand: #5ea4ff;
            --brand-deep: #2d6df6;
            --ok: #22c55e;
            --warn: #f59e0b;
            --danger: #ef4444;
            --shadow: 0 24px 48px rgba(4, 10, 24, 0.26);
        }
        * { box-sizing: border-box; }
        html { color-scheme: light; }
        body {
            margin: 0;
            min-height: 100vh;
            font-family: "SF Pro Display", "Manrope", "PingFang SC", "Microsoft YaHei", sans-serif;
            color: #132445;
            background:
                radial-gradient(circle at 10% 10%, rgba(77, 167, 255, 0.18), transparent 32%),
                radial-gradient(circle at 88% 14%, rgba(58, 209, 167, 0.14), transparent 28%),
                linear-gradient(145deg, var(--bg-0), var(--bg-1) 46%, var(--bg-2));
            padding: 18px 14px 96px;
        }
        body::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            background-image:
                linear-gradient(rgba(255, 255, 255, 0.018) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255, 255, 255, 0.018) 1px, transparent 1px);
            background-size: 28px 28px;
            mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.78), transparent 92%);
        }
        a { color: inherit; }
        .container {
            width: min(1340px, 100%);
            margin: 0 auto;
            display: grid;
            gap: 16px;
        }
        .topbar {
            position: sticky;
            top: 12px;
            z-index: 20;
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            gap: 14px;
            padding: 18px 20px;
            border-radius: 22px;
            border: 1px solid rgba(176, 205, 255, 0.18);
            background:
                linear-gradient(135deg, rgba(255, 255, 255, 0.09), rgba(255, 255, 255, 0.03)),
                linear-gradient(180deg, rgba(16, 28, 52, 0.88), rgba(12, 22, 43, 0.86));
            color: var(--text);
            backdrop-filter: blur(18px);
            box-shadow: var(--shadow);
        }
        .title-wrap {
            display: grid;
            gap: 4px;
        }
        .title {
            margin: 0;
            font-size: 28px;
            font-weight: 750;
            letter-spacing: 0.2px;
        }
        .subtitle {
            margin: 0;
            color: var(--text-soft);
            font-size: 13px;
            line-height: 1.55;
            max-width: 720px;
        }
        .topbar-actions {
            display: flex;
            flex-wrap: wrap;
            align-items: flex-start;
            justify-content: flex-end;
            gap: 10px;
        }
        .hover-group {
            position: relative;
        }
        .icon-chip,
        .status-chip {
            border: 1px solid rgba(176, 205, 255, 0.18);
            background: rgba(255, 255, 255, 0.06);
            color: #f6f9ff;
            border-radius: 999px;
            min-height: 40px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 0 14px;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.2px;
            cursor: default;
            transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
        }
        .icon-chip {
            width: 40px;
            padding: 0;
        }
        .hover-group:hover .icon-chip,
        .hover-group:focus-within .icon-chip,
        .hover-group:hover .status-chip,
        .hover-group:focus-within .status-chip {
            transform: translateY(-1px);
            border-color: rgba(206, 223, 255, 0.32);
            background: rgba(255, 255, 255, 0.10);
        }
        .status-chip.ok { box-shadow: inset 0 0 0 1px rgba(34, 197, 94, 0.22); }
        .status-chip.warn { box-shadow: inset 0 0 0 1px rgba(245, 158, 11, 0.22); }
        .status-chip.danger { box-shadow: inset 0 0 0 1px rgba(239, 68, 68, 0.24); }
        .chip-dot {
            width: 10px;
            height: 10px;
            border-radius: 999px;
            background: #d1d5db;
            box-shadow: 0 0 0 6px rgba(255, 255, 255, 0.05);
        }
        .chip-dot.ok { background: var(--ok); }
        .chip-dot.warn { background: var(--warn); }
        .chip-dot.danger { background: var(--danger); }
        .popover {
            position: absolute;
            top: calc(100% + 12px);
            right: 0;
            width: min(340px, calc(100vw - 28px));
            padding: 14px;
            border-radius: 18px;
            border: 1px solid var(--line-strong);
            background: rgba(250, 253, 255, 0.97);
            color: #173059;
            box-shadow: 0 20px 40px rgba(8, 17, 34, 0.22);
            opacity: 0;
            pointer-events: none;
            transform: translateY(8px);
            transition: opacity 0.18s ease, transform 0.18s ease;
        }
        .popover::before {
            content: "";
            position: absolute;
            top: -7px;
            right: 18px;
            width: 14px;
            height: 14px;
            background: rgba(250, 253, 255, 0.97);
            border-left: 1px solid var(--line-strong);
            border-top: 1px solid var(--line-strong);
            transform: rotate(45deg);
        }
        .hover-group:hover .popover,
        .hover-group:focus-within .popover {
            opacity: 1;
            pointer-events: auto;
            transform: translateY(0);
        }
        .popover-title {
            margin: 0 0 10px;
            font-size: 14px;
            font-weight: 800;
        }
        .popover-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
        }
        .popover-item {
            padding: 8px 10px;
            border-radius: 12px;
            background: #f3f7ff;
            border: 1px solid #dde8fb;
        }
        .popover-label {
            display: block;
            font-size: 11px;
            color: #6b7fa4;
            margin-bottom: 3px;
        }
        .popover-value {
            display: block;
            font-size: 12px;
            font-weight: 700;
            line-height: 1.5;
            word-break: break-word;
        }
        .logout-form { margin: 0; }
        .btn-logout {
            border: 1px solid rgba(176, 205, 255, 0.18);
            background: rgba(255, 255, 255, 0.08);
            color: #f5f9ff;
            border-radius: 999px;
            padding: 10px 14px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 700;
            transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
        }
        .btn-logout:hover {
            transform: translateY(-1px);
            background: rgba(255, 255, 255, 0.14);
            border-color: rgba(214, 226, 255, 0.34);
        }
        .toolbar {
            display: grid;
            gap: 14px;
            padding: 16px;
            border-radius: 22px;
            border: 1px solid rgba(176, 205, 255, 0.14);
            background: rgba(10, 19, 38, 0.54);
            backdrop-filter: blur(14px);
            box-shadow: var(--shadow);
        }
        .toolbar-head {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
        }
        .toolbar-title {
            margin: 0;
            color: #eef5ff;
            font-size: 18px;
            font-weight: 750;
        }
        .toolbar-note {
            margin: 4px 0 0;
            color: var(--text-soft);
            font-size: 12px;
        }
        .meta-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .meta-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 7px 11px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.07);
            border: 1px solid rgba(176, 205, 255, 0.14);
            color: #edf4ff;
            font-size: 12px;
            font-weight: 700;
        }
        .add-form {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 10px;
        }
        input[type="text"] {
            width: 100%;
            border: 1px solid rgba(176, 205, 255, 0.16);
            border-radius: 16px;
            padding: 14px 16px;
            font-size: 14px;
            color: #173059;
            background: rgba(250, 253, 255, 0.96);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
        }
        input[type="text"]:focus {
            outline: none;
            border-color: rgba(94, 164, 255, 0.54);
            box-shadow: 0 0 0 4px rgba(94, 164, 255, 0.14);
        }
        button {
            border: 0;
            border-radius: 16px;
            padding: 0 16px;
            min-height: 48px;
            font-size: 13px;
            font-weight: 750;
            cursor: pointer;
            background: linear-gradient(135deg, var(--brand), var(--brand-deep));
            color: #fff;
            transition: transform 0.18s ease, box-shadow 0.18s ease;
            box-shadow: 0 12px 26px rgba(45, 109, 246, 0.24);
        }
        button:hover {
            transform: translateY(-1px);
            box-shadow: 0 14px 30px rgba(45, 109, 246, 0.28);
        }
        .flash-stack {
            display: grid;
            gap: 8px;
        }
        .msg {
            padding: 11px 12px;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.94);
            border: 1px solid #d4e3ff;
            color: #1c427b;
            font-size: 13px;
            box-shadow: 0 10px 22px rgba(14, 36, 76, 0.10);
        }
        .filter-bar {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .filter-chip {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            text-decoration: none;
            padding: 9px 12px;
            border-radius: 999px;
            border: 1px solid rgba(176, 205, 255, 0.14);
            background: rgba(255, 255, 255, 0.08);
            color: #edf4ff;
            font-size: 12px;
            font-weight: 700;
            transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
        }
        .filter-chip:hover {
            transform: translateY(-1px);
            border-color: rgba(214, 226, 255, 0.28);
            background: rgba(255, 255, 255, 0.12);
        }
        .filter-chip.active {
            background: linear-gradient(135deg, rgba(94, 164, 255, 0.28), rgba(45, 109, 246, 0.32));
            border-color: rgba(122, 183, 255, 0.36);
        }
        .filter-count {
            display: inline-flex;
            min-width: 24px;
            justify-content: center;
            padding: 2px 7px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.14);
            font-size: 11px;
        }
        .content {
            display: grid;
            gap: 12px;
        }
        .content-head {
            display: flex;
            flex-wrap: wrap;
            align-items: baseline;
            justify-content: space-between;
            gap: 10px;
        }
        .content-title {
            margin: 0;
            color: #eef5ff;
            font-size: 20px;
            font-weight: 750;
        }
        .content-desc {
            margin: 4px 0 0;
            color: var(--text-soft);
            font-size: 12px;
        }
        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(265px, 1fr));
            gap: 12px;
        }
        .card {
            background: var(--panel-strong);
            border: 1px solid rgba(191, 210, 242, 0.76);
            border-radius: 20px;
            padding: 14px;
            transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
            position: relative;
            overflow: hidden;
            box-shadow: 0 16px 34px rgba(10, 24, 54, 0.10);
        }
        .card::before {
            content: "";
            position: absolute;
            inset: 0 auto auto 0;
            width: 100%;
            height: 3px;
            background: linear-gradient(90deg, rgba(94, 164, 255, 0.85), rgba(32, 197, 161, 0.72));
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 18px 38px rgba(10, 24, 54, 0.14);
            border-color: rgba(153, 186, 240, 0.92);
        }
        .card-head {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 10px;
        }
        .domain {
            margin: 0;
            font-size: 15px;
            font-weight: 800;
            color: #172a50;
            overflow-wrap: anywhere;
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 11px;
            font-weight: 800;
            white-space: nowrap;
        }
        .status-badge.ok { color: #10753b; background: #e7f9ef; }
        .status-badge.warn { color: #a36300; background: #fff4da; }
        .status-badge.danger { color: #b42318; background: #fde9e7; }
        .meta {
            margin-top: 12px;
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
        }
        .kv {
            background: linear-gradient(180deg, #f7faff, #f1f6ff);
            border: 1px solid #dbe7fb;
            border-radius: 14px;
            padding: 9px 10px;
        }
        .kv-label {
            display: block;
            font-size: 11px;
            color: #7588ab;
            margin-bottom: 4px;
        }
        .kv-value {
            display: block;
            font-size: 13px;
            font-weight: 700;
            color: #173059;
            word-break: break-word;
            line-height: 1.5;
        }
        .error-detail {
            margin-top: 10px;
            border: 1px solid #e2eaf8;
            background: #fbfdff;
            border-radius: 12px;
            padding: 5px 9px;
        }
        .error-detail summary {
            cursor: pointer;
            color: #3f5883;
            font-size: 12px;
            user-select: none;
        }
        .error-text {
            margin: 7px 0 0;
            white-space: pre-wrap;
            word-break: break-word;
            color: #7b241c;
            font-size: 12px;
            line-height: 1.5;
        }
        .card-actions {
            margin-top: 12px;
            display: flex;
            justify-content: flex-end;
        }
        .btn-del {
            min-height: 36px;
            padding: 0 12px;
            background: #fff;
            color: #be2f23;
            border: 1px solid #f2c9c4;
            box-shadow: none;
        }
        .btn-del:hover {
            box-shadow: 0 10px 22px rgba(190, 47, 35, 0.12);
        }
        .empty {
            text-align: center;
            color: var(--text-soft);
            padding: 42px 12px;
            border-radius: 20px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px dashed rgba(176, 205, 255, 0.18);
        }
        .floating-alert {
            position: fixed;
            left: 18px;
            bottom: 18px;
            z-index: 30;
            max-width: min(360px, calc(100vw - 36px));
            color: #fff8f0;
        }
        .floating-trigger {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 12px 14px;
            border-radius: 18px;
            border: 1px solid rgba(245, 158, 11, 0.28);
            background: linear-gradient(135deg, rgba(78, 34, 9, 0.92), rgba(116, 38, 10, 0.92));
            box-shadow: 0 20px 42px rgba(65, 26, 7, 0.30);
            backdrop-filter: blur(16px);
        }
        .floating-trigger strong {
            display: block;
            font-size: 13px;
            margin-bottom: 2px;
        }
        .floating-trigger span {
            display: block;
            font-size: 11px;
            color: rgba(255, 241, 223, 0.82);
        }
        .pulse-dot {
            width: 12px;
            height: 12px;
            border-radius: 999px;
            background: #fbbf24;
            box-shadow: 0 0 0 0 rgba(251, 191, 36, 0.42);
            animation: pulse 1.8s infinite;
            flex: 0 0 auto;
        }
        .floating-panel {
            position: absolute;
            left: 0;
            bottom: calc(100% + 12px);
            width: 100%;
            padding: 14px;
            border-radius: 18px;
            border: 1px solid rgba(247, 207, 140, 0.34);
            background: rgba(255, 250, 244, 0.98);
            color: #5b2b08;
            box-shadow: 0 24px 48px rgba(65, 26, 7, 0.22);
            opacity: 0;
            pointer-events: none;
            transform: translateY(8px);
            transition: opacity 0.18s ease, transform 0.18s ease;
        }
        .floating-alert:hover .floating-panel,
        .floating-alert:focus-within .floating-panel {
            opacity: 1;
            pointer-events: auto;
            transform: translateY(0);
        }
        .floating-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            margin-bottom: 10px;
        }
        .floating-head strong {
            font-size: 14px;
        }
        .floating-link {
            text-decoration: none;
            font-size: 12px;
            font-weight: 700;
            color: #9a4f0a;
        }
        .floating-list {
            display: grid;
            gap: 8px;
        }
        .floating-item {
            padding: 9px 10px;
            border-radius: 12px;
            background: #fff7ee;
            border: 1px solid #f6debf;
        }
        .floating-item-top {
            display: flex;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 4px;
        }
        .floating-domain {
            font-size: 12px;
            font-weight: 800;
            overflow-wrap: anywhere;
        }
        .floating-meta {
            font-size: 11px;
            line-height: 1.55;
            color: #8a4a10;
        }
        .inline-form { margin: 0; }
        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(251, 191, 36, 0.46); }
            70% { box-shadow: 0 0 0 10px rgba(251, 191, 36, 0); }
            100% { box-shadow: 0 0 0 0 rgba(251, 191, 36, 0); }
        }
        @media (max-width: 940px) {
            .topbar {
                position: static;
                padding: 16px;
            }
            .topbar-actions {
                width: 100%;
                justify-content: flex-start;
            }
            .add-form {
                grid-template-columns: 1fr;
            }
        }
        @media (max-width: 720px) {
            body {
                padding-bottom: 120px;
            }
            .popover {
                left: 0;
                right: auto;
                width: min(320px, calc(100vw - 28px));
            }
            .popover::before {
                left: 18px;
                right: auto;
            }
            .popover-grid {
                grid-template-columns: 1fr;
            }
            .cards {
                grid-template-columns: 1fr;
            }
            .meta {
                grid-template-columns: 1fr;
            }
        }
        @media (max-width: 560px) {
            .container {
                gap: 12px;
            }
            .title {
                font-size: 24px;
            }
            .filter-bar,
            .meta-pills {
                gap: 7px;
            }
            .filter-chip,
            .meta-pill {
                font-size: 11px;
            }
        }
    </style>
    </head>
    <body>
        <div class="container">
            <header class="topbar">
                <div class="title-wrap">
                    <h1 class="title">SSL 证书监控仪表盘</h1>
                    <p class="subtitle">最后刷新时间 {{ now }}。当前筛选 {{ current_filter_label }}，共展示 {{ filtered_results|length }} / {{ stats.total }} 个域名。</p>
                </div>

                <div class="topbar-actions">
                    <div class="hover-group" tabindex="0">
                        <div class="status-chip {{ timer_panel.health_level }}">
                            <span class="chip-dot {{ timer_panel.health_level }}"></span>
                            <span>检测</span>
                        </div>
                        <div class="popover">
                            <p class="popover-title">后台定时检测</p>
                            <div class="popover-grid">
                                <div class="popover-item">
                                    <span class="popover-label">状态</span>
                                    <span class="popover-value">{{ timer_panel.health_text }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">调度周期</span>
                                    <span class="popover-value">{{ timer_panel.schedule_text }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">最近开始</span>
                                    <span class="popover-value">{{ timer_panel.last_started_at }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">最近完成</span>
                                    <span class="popover-value">{{ timer_panel.last_completed_at }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">执行结果</span>
                                    <span class="popover-value">{{ timer_panel.last_status_text }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">执行摘要</span>
                                    <span class="popover-value">{{ timer_panel.summary_text }}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="hover-group" tabindex="0">
                        <div class="status-chip {{ email_panel.health_level }}">
                            <span class="chip-dot {{ email_panel.health_level }}"></span>
                            <span>邮件</span>
                        </div>
                        <div class="popover">
                            <p class="popover-title">邮件提醒状态</p>
                            <div class="popover-grid">
                                <div class="popover-item">
                                    <span class="popover-label">状态</span>
                                    <span class="popover-value">{{ email_panel.health_text }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">SMTP</span>
                                    <span class="popover-value">{{ email_panel.server_text }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">收件人</span>
                                    <span class="popover-value">{{ email_panel.recipient_text }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">最近发送时间</span>
                                    <span class="popover-value">{{ email_panel.last_attempt_at }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">最近触发域名</span>
                                    <span class="popover-value">{{ email_panel.last_attempt_domain }}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">最近触发原因</span>
                                    <span class="popover-value">{{ email_panel.last_attempt_reason }}</span>
                                </div>
                            </div>
                            {% if email_panel.last_attempt_error %}
                            <div class="popover-item" style="margin-top:8px;">
                                <span class="popover-label">最近错误</span>
                                <span class="popover-value">{{ email_panel.last_attempt_error }}</span>
                            </div>
                            {% endif %}
                        </div>
                    </div>

                    <div class="hover-group" tabindex="0">
                        <div class="icon-chip">i</div>
                        <div class="popover">
                            <p class="popover-title">提醒规则</p>
                            <div class="popover-grid">
                                <div class="popover-item">
                                    <span class="popover-label">里程碑</span>
                                    <span class="popover-value">{{ milestones_text }} 天</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">每日提醒</span>
                                    <span class="popover-value">{% if enable_daily_reminder %}临期（<= {{ alert_days }} 天）每日最多 1 封{% else %}已关闭{% endif %}</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">排序规则</span>
                                    <span class="popover-value">按剩余天数从小到大，连接失败排最后</span>
                                </div>
                                <div class="popover-item">
                                    <span class="popover-label">域名存储</span>
                                    <span class="popover-value">{{ domains_file }}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <form method="post" action="{{ url_for('logout') }}" class="logout-form">
                        <button type="submit" class="btn-logout">退出登录</button>
                    </form>
                </div>
            </header>

            <section class="toolbar">
                <div class="toolbar-head">
                    <div>
                        <h2 class="toolbar-title">域名控制台</h2>
                        <p class="toolbar-note">把添加域名、筛选视图和运行提示收在这里，列表区域只保留真正的域名内容。</p>
                    </div>
                    <div class="meta-pills">
                        <span class="meta-pill"><span class="chip-dot {{ timer_panel.health_level }}"></span>{{ timer_panel.health_text }}</span>
                        <span class="meta-pill"><span class="chip-dot {{ email_panel.health_level }}"></span>{{ email_panel.health_text }}</span>
                        <span class="meta-pill">总域名 {{ stats.total }}</span>
                    </div>
                </div>

                <form method="post" action="{{ url_for('add_domain') }}" class="add-form">
                    <input type="text" name="domain" placeholder="输入域名，例如 example.com 或 https://example.com" required>
                    <button type="submit">添加检测域名</button>
                </form>

                {% with messages = get_flashed_messages() %}
                    {% if messages %}
                    <div class="flash-stack">
                        {% for msg in messages %}
                        <div class="msg">{{ msg }}</div>
                        {% endfor %}
                    </div>
                    {% endif %}
                {% endwith %}

                <div class="filter-bar">
                    {% for tab in filter_tabs %}
                    <a href="{{ tab.href }}" class="filter-chip{% if tab.active %} active{% endif %}">
                        <span>{{ tab.label }}</span>
                        <span class="filter-count">{{ tab.count }}</span>
                    </a>
                    {% endfor %}
                </div>
            </section>

            <section class="content">
                <div class="content-head">
                    <div>
                        <h2 class="content-title">域名列表</h2>
                        <p class="content-desc">状态通过颜色和标签区分。需要更多上下文时，查看顶部状态悬浮面板或域名卡片内的详细字段。</p>
                    </div>
                    <div class="meta-pills">
                        <span class="meta-pill">当前筛选 {{ current_filter_label }}</span>
                        <span class="meta-pill">显示 {{ filtered_results|length }} / {{ stats.total }}</span>
                    </div>
                </div>

                <div class="cards">
                    {% if filtered_results %}
                        {% for res in filtered_results %}
                        <article class="card">
                            <div class="card-head">
                                <h3 class="domain">{{ res.domain }}</h3>
                                <span class="status-badge {% if res.status == '正常' %}ok{% elif res.status == '即将过期' %}warn{% else %}danger{% endif %}">
                                    <span class="chip-dot {% if res.status == '正常' %}ok{% elif res.status == '即将过期' %}warn{% else %}danger{% endif %}"></span>
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
                    <div class="empty">当前筛选条件下没有匹配域名，可以切换上方筛选查看其他状态。</div>
                    {% endif %}
                </div>
            </section>
        </div>

        {% if priority_results %}
        <div class="floating-alert" tabindex="0">
            <div class="floating-trigger">
                <span class="pulse-dot"></span>
                <div>
                    <strong>有 {{ priority_results|length }} 个域名需要优先处理</strong>
                    <span>悬停或聚焦即可查看明细</span>
                </div>
            </div>
            <div class="floating-panel">
                <div class="floating-head">
                    <strong>优先处理域名</strong>
                    <a href="{{ url_for('index', view='attention') }}" class="floating-link">查看全部</a>
                </div>
                <div class="floating-list">
                    {% for res in spotlight_results %}
                    <div class="floating-item">
                        <div class="floating-item-top">
                            <span class="floating-domain">{{ res.domain }}</span>
                            <span class="status-badge {% if res.status == '即将过期' %}warn{% else %}danger{% endif %}">{{ res.status }}</span>
                        </div>
                        <div class="floating-meta">
                            过期时间：{{ res.expiry }}<br>
                            剩余天数：{{ res.days_left }}
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
        {% endif %}
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
        timer_panel=timer_panel,
        email_panel=email_panel,
        filter_tabs=filter_tabs,
        current_filter_label=current_filter_label,
        filtered_results=filtered_results,
        priority_results=priority_results,
        spotlight_results=spotlight_results,
    )


def main():
    args = parse_args()
    if args.check_only:
        return run_check_once()

    port = int(os.getenv("PORT", "2026"))
    if DASHBOARD_PASSWORD == "admin123456":
        print("[安全提示] 正在使用默认访问密码，请尽快通过环境变量 DASHBOARD_PASSWORD 修改。")
    app.run(host="0.0.0.0", port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
