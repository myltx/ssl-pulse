#!/usr/bin/env bash
set -euo pipefail

# One-command deploy for Alibaba Cloud Linux 3
# Usage:
#   sudo bash deploy_alinux3.sh
# Optional env:
#   APP_DIR=/root/ssl-pulse PORT=2026 SERVICE_NAME=ssl-monitor DASHBOARD_PASSWORD=your_password PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ sudo -E bash deploy_alinux3.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-${SCRIPT_DIR}}"
SERVICE_NAME="${SERVICE_NAME:-ssl-monitor}"
PORT="${PORT:-2026}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"
DASHBOARD_PASSWORD="${DASHBOARD_PASSWORD:-}"
FLASK_SECRET_KEY="${FLASK_SECRET_KEY:-}"
SESSION_TTL_MINUTES="${SESSION_TTL_MINUTES:-720}"
DOMAINS="${DOMAINS:-}"
ALERT_DAYS="${ALERT_DAYS:-}"
ALERT_MILESTONES="${ALERT_MILESTONES:-}"
ENABLE_DAILY_REMINDER="${ENABLE_DAILY_REMINDER:-}"
SMTP_SERVER="${SMTP_SERVER:-}"
SMTP_PORT="${SMTP_PORT:-}"
SMTP_USER="${SMTP_USER:-}"
SMTP_PASSWORD="${SMTP_PASSWORD:-}"
TO_EMAIL="${TO_EMAIL:-}"
ENV_FILE="${APP_DIR}/.ssl_pulse.env"

random_hex() {
  local n="${1:-16}"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "${n}"
  else
    head -c "${n}" /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

escape_for_env_file() {
  local value="${1//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "${value}"
}

read_env_file_value() {
  local key="$1"
  if [[ ! -f "${ENV_FILE}" ]]; then
    return 0
  fi
  local quoted
  quoted="$(sed -n "s/^${key}=\"\\(.*\\)\"$/\\1/p" "${ENV_FILE}" | head -n 1)"
  if [[ -n "${quoted}" ]]; then
    printf '%s' "${quoted}"
    return 0
  fi
  sed -n "s/^${key}=\\(.*\\)$/\\1/p" "${ENV_FILE}" | head -n 1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

use_env_or_existing_or_default() {
  local current="$1"
  local key="$2"
  local fallback="$3"
  if [[ -n "${current}" ]]; then
    printf '%s' "${current}"
    return 0
  fi
  local existing
  existing="$(read_env_file_value "${key}")"
  if [[ -n "${existing}" ]]; then
    printf '%s' "${existing}"
  else
    printf '%s' "${fallback}"
  fi
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "[ERROR] Please run as root: sudo bash deploy_alinux3.sh"
  exit 1
fi

if [[ ! -f "${APP_DIR}/check_ssl.py" ]]; then
  echo "[ERROR] Missing file: ${APP_DIR}/check_ssl.py"
  exit 1
fi

if [[ ! -f "${APP_DIR}/requirements.txt" ]]; then
  echo "[ERROR] Missing file: ${APP_DIR}/requirements.txt"
  exit 1
fi

EXISTING_DASHBOARD_PASSWORD="$(read_env_file_value "DASHBOARD_PASSWORD")"
EXISTING_FLASK_SECRET_KEY="$(read_env_file_value "FLASK_SECRET_KEY")"

PASSWORD_GENERATED=0
PASSWORD_REUSED=0
if [[ -z "${DASHBOARD_PASSWORD}" ]]; then
  if [[ -n "${EXISTING_DASHBOARD_PASSWORD}" ]]; then
    DASHBOARD_PASSWORD="${EXISTING_DASHBOARD_PASSWORD}"
    PASSWORD_REUSED=1
  else
    DASHBOARD_PASSWORD="$(random_hex 12)"
    PASSWORD_GENERATED=1
  fi
fi

SECRET_GENERATED=0
SECRET_REUSED=0
if [[ -z "${FLASK_SECRET_KEY}" ]]; then
  if [[ -n "${EXISTING_FLASK_SECRET_KEY}" ]]; then
    FLASK_SECRET_KEY="${EXISTING_FLASK_SECRET_KEY}"
    SECRET_REUSED=1
  else
    FLASK_SECRET_KEY="$(random_hex 24)"
    SECRET_GENERATED=1
  fi
fi

DOMAINS="$(use_env_or_existing_or_default "${DOMAINS}" "DOMAINS" "")"
ALERT_DAYS="$(use_env_or_existing_or_default "${ALERT_DAYS}" "ALERT_DAYS" "30")"
ALERT_MILESTONES="$(use_env_or_existing_or_default "${ALERT_MILESTONES}" "ALERT_MILESTONES" "30,15,7,3,1")"
ENABLE_DAILY_REMINDER="$(use_env_or_existing_or_default "${ENABLE_DAILY_REMINDER}" "ENABLE_DAILY_REMINDER" "true")"
SMTP_SERVER="$(use_env_or_existing_or_default "${SMTP_SERVER}" "SMTP_SERVER" "")"
SMTP_PORT="$(use_env_or_existing_or_default "${SMTP_PORT}" "SMTP_PORT" "587")"
SMTP_USER="$(use_env_or_existing_or_default "${SMTP_USER}" "SMTP_USER" "")"
SMTP_PASSWORD="$(use_env_or_existing_or_default "${SMTP_PASSWORD}" "SMTP_PASSWORD" "")"
TO_EMAIL="$(use_env_or_existing_or_default "${TO_EMAIL}" "TO_EMAIL" "")"

echo "[1/5] Install python dependencies..."
dnf install -y python3 python3-pip

echo "[2/5] Create virtualenv and install packages..."
"${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
PY_VER="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
PY_MAJOR="${PY_VER%%.*}"
PY_MINOR="${PY_VER##*.}"
if [[ "${PY_MAJOR}" -ne 3 || "${PY_MINOR}" -lt 6 ]]; then
  echo "[ERROR] Python >= 3.6 is required, current: ${PY_VER}"
  exit 1
fi

VENV_PY="${APP_DIR}/.venv/bin/python"
PIP_ARGS=(--no-cache-dir -i "${PIP_INDEX_URL}")
if [[ -n "${PIP_TRUSTED_HOST}" ]]; then
  PIP_ARGS+=(--trusted-host "${PIP_TRUSTED_HOST}")
fi

# Keep pip/setuptools versions compatible with old Python releases.
if [[ "${PY_MINOR}" -lt 7 ]]; then
  PIP_UPGRADE_PKGS=("pip<22" "setuptools<60" "wheel<0.38")
elif [[ "${PY_MINOR}" -lt 8 ]]; then
  PIP_UPGRADE_PKGS=("pip<24" "setuptools<70" "wheel")
else
  PIP_UPGRADE_PKGS=("pip" "setuptools" "wheel")
fi

"${VENV_PY}" -m pip install "${PIP_ARGS[@]}" --upgrade "${PIP_UPGRADE_PKGS[@]}"
"${VENV_PY}" -m pip install "${PIP_ARGS[@]}" -r "${APP_DIR}/requirements.txt"

echo "[3/5] Create systemd service..."
PORT_ESCAPED="$(escape_for_env_file "${PORT}")"
DASHBOARD_PASSWORD_ESCAPED="$(escape_for_env_file "${DASHBOARD_PASSWORD}")"
FLASK_SECRET_KEY_ESCAPED="$(escape_for_env_file "${FLASK_SECRET_KEY}")"
SESSION_TTL_ESCAPED="$(escape_for_env_file "${SESSION_TTL_MINUTES}")"
DOMAINS_ESCAPED="$(escape_for_env_file "${DOMAINS}")"
ALERT_DAYS_ESCAPED="$(escape_for_env_file "${ALERT_DAYS}")"
ALERT_MILESTONES_ESCAPED="$(escape_for_env_file "${ALERT_MILESTONES}")"
ENABLE_DAILY_REMINDER_ESCAPED="$(escape_for_env_file "${ENABLE_DAILY_REMINDER}")"
SMTP_SERVER_ESCAPED="$(escape_for_env_file "${SMTP_SERVER}")"
SMTP_PORT_ESCAPED="$(escape_for_env_file "${SMTP_PORT}")"
SMTP_USER_ESCAPED="$(escape_for_env_file "${SMTP_USER}")"
SMTP_PASSWORD_ESCAPED="$(escape_for_env_file "${SMTP_PASSWORD}")"
TO_EMAIL_ESCAPED="$(escape_for_env_file "${TO_EMAIL}")"

cat >"${ENV_FILE}" <<EOF
PORT="${PORT_ESCAPED}"
PYTHONUNBUFFERED="1"
DASHBOARD_PASSWORD="${DASHBOARD_PASSWORD_ESCAPED}"
FLASK_SECRET_KEY="${FLASK_SECRET_KEY_ESCAPED}"
SESSION_TTL_MINUTES="${SESSION_TTL_ESCAPED}"
DOMAINS="${DOMAINS_ESCAPED}"
ALERT_DAYS="${ALERT_DAYS_ESCAPED}"
ALERT_MILESTONES="${ALERT_MILESTONES_ESCAPED}"
ENABLE_DAILY_REMINDER="${ENABLE_DAILY_REMINDER_ESCAPED}"
SMTP_SERVER="${SMTP_SERVER_ESCAPED}"
SMTP_PORT="${SMTP_PORT_ESCAPED}"
SMTP_USER="${SMTP_USER_ESCAPED}"
SMTP_PASSWORD="${SMTP_PASSWORD_ESCAPED}"
TO_EMAIL="${TO_EMAIL_ESCAPED}"
EOF
chmod 600 "${ENV_FILE}"

cat >"/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=SSL Monitoring Dashboard (Flask)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/check_ssl.py
EnvironmentFile=${ENV_FILE}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "[4/5] Enable and start service..."
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "[5/5] Open firewall port if firewalld is enabled..."
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="${PORT}/tcp"
  firewall-cmd --reload
  echo "[INFO] firewalld opened ${PORT}/tcp"
else
  echo "[INFO] firewalld not running, skip firewall-cmd"
fi

echo
echo "Deploy completed."
echo "Service status:"
systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,20p'
echo
if [[ "${PASSWORD_GENERATED}" -eq 1 ]]; then
  echo "[INFO] Dashboard password (generated): ${DASHBOARD_PASSWORD}"
elif [[ "${PASSWORD_REUSED}" -eq 1 ]]; then
  echo "[INFO] Dashboard password: reused from existing ${ENV_FILE}"
else
  echo "[INFO] Dashboard password: using value from env DASHBOARD_PASSWORD"
fi
if [[ "${SECRET_GENERATED}" -eq 1 ]]; then
  echo "[INFO] Flask secret key was auto-generated and stored in ${ENV_FILE}"
elif [[ "${SECRET_REUSED}" -eq 1 ]]; then
  echo "[INFO] Flask secret key: reused from existing ${ENV_FILE}"
fi
echo "[INFO] Runtime env file: ${ENV_FILE}"
echo
echo "Logs:"
echo "  journalctl -u ${SERVICE_NAME} -f"
echo "Visit:"
echo "  http://<your-server-ip>:${PORT}"
