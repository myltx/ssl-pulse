#!/usr/bin/env bash
set -euo pipefail

# Upgrade/install Python runtime on Alibaba Cloud Linux 3
# Usage:
#   sudo bash update_python_alinux3.sh
#   sudo env TARGET_PYTHON=3.11 bash update_python_alinux3.sh

TARGET_PYTHON="${TARGET_PYTHON:-auto}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[ERROR] Please run as root: sudo bash update_python_alinux3.sh"
  exit 1
fi

normalize_target_pkg() {
  local target="$1"
  if [[ "${target}" == "auto" ]]; then
    printf '%s\n' "auto"
    return
  fi
  if [[ "${target}" == python3* ]]; then
    printf '%s\n' "${target}"
    return
  fi
  printf 'python%s\n' "${target}"
}

TARGET_PKG="$(normalize_target_pkg "${TARGET_PYTHON}")"

if [[ "${TARGET_PKG}" == "auto" ]]; then
  CANDIDATES=("python3.12" "python3.11" "python3.10" "python3.9" "python3.8" "python3")
else
  CANDIDATES=("${TARGET_PKG}")
fi

echo "[1/4] Refresh package metadata..."
dnf makecache

echo "[2/4] Install Python package..."
PY_BIN=""
INSTALLED_PKG=""
for pkg in "${CANDIDATES[@]}"; do
  echo "  - trying ${pkg}"
  if dnf install -y "${pkg}"; then
    if [[ "${pkg}" == "python3" ]]; then
      candidate_bin="/usr/bin/python3"
    else
      candidate_bin="/usr/bin/${pkg}"
    fi
    if [[ -x "${candidate_bin}" ]]; then
      PY_BIN="${candidate_bin}"
      INSTALLED_PKG="${pkg}"
      break
    fi
  fi
done

if [[ -z "${PY_BIN}" ]]; then
  echo "[ERROR] Failed to install target Python package."
  echo "        Tried: ${CANDIDATES[*]}"
  exit 1
fi

echo "[3/4] Ensure pip and tooling..."
dnf install -y python3-pip || true
"${PY_BIN}" -m ensurepip --upgrade || true

PY_VER="$("${PY_BIN}" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
PY_MINOR="${PY_VER##*.}"
PIP_ARGS=(--no-cache-dir -i "${PIP_INDEX_URL}")
if [[ -n "${PIP_TRUSTED_HOST}" ]]; then
  PIP_ARGS+=(--trusted-host "${PIP_TRUSTED_HOST}")
fi

if [[ "${PY_MINOR}" -lt 7 ]]; then
  PIP_UPGRADE_PKGS=("pip<22" "setuptools<60" "wheel<0.38")
elif [[ "${PY_MINOR}" -lt 8 ]]; then
  PIP_UPGRADE_PKGS=("pip<24" "setuptools<70" "wheel")
else
  PIP_UPGRADE_PKGS=("pip" "setuptools" "wheel")
fi

"${PY_BIN}" -m pip install "${PIP_ARGS[@]}" --upgrade "${PIP_UPGRADE_PKGS[@]}"

echo "[4/4] Done."
echo "Installed package: ${INSTALLED_PKG}"
echo "Python binary: ${PY_BIN}"
"${PY_BIN}" --version
"${PY_BIN}" -m pip --version
echo
echo "Deploy with this Python:"
echo "  sudo env PYTHON_BIN=${PY_BIN} bash deploy_alinux3.sh"
