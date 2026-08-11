#!/usr/bin/env bash
# ============================================================================
# DouyinLiveRecorder systemd 安装脚本
#
# 用法:
#   sudo ./deploy/install.sh [选项]
#
# 选项:
#   --install-dir DIR   安装目录（默认 /opt/DouyinLiveRecorder）
#   --port PORT         WebUI 端口（默认 8000）
#   --user USER         服务运行用户（默认 douyinrec）
#   --no-venv           使用系统 python3 而非创建虚拟环境
#   --no-systemd        仅安装文件，不注册/启动 systemd 服务
#   --uninstall         卸载（停止并移除服务、删除用户；加 --purge 删除安装目录）
# ============================================================================
set -euo pipefail

INSTALL_DIR="/opt/DouyinLiveRecorder"
WEBUI_PORT="8000"
SVC_USER="douyinrec"
USE_VENV=1
USE_SYSTEMD=1
UNINSTALL=0
PURGE=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_NAME="douyinliverecorder"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --port) WEBUI_PORT="$2"; shift 2 ;;
    --user) SVC_USER="$2"; shift 2 ;;
    --no-venv) USE_VENV=0; shift ;;
    --no-systemd) USE_SYSTEMD=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

log()  { echo -e "\033[1;32m[install]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*"; }
die()  { echo -e "\033[1;31m[error]\033[0m $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 卸载
# ---------------------------------------------------------------------------
if [[ "$UNINSTALL" == "1" ]]; then
  log "停止并移除服务 $SERVICE_NAME ..."
  if systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
  fi
  if id "$SVC_USER" &>/dev/null; then
    userdel -r "$SVC_USER" 2>/dev/null || userdel "$SVC_USER"
    log "已删除用户 $SVC_USER"
  fi
  if [[ "$PURGE" == "1" ]] && [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    log "已删除安装目录 $INSTALL_DIR"
  fi
  log "卸载完成。"
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || die "请使用 root 运行: sudo ./deploy/install.sh"

# ---------------------------------------------------------------------------
# 1. 安装系统依赖（ffmpeg / nodejs / python3）
# ---------------------------------------------------------------------------
detect_pkg() {
  if command -v apt-get &>/dev/null; then echo "apt"; 
  elif command -v dnf &>/dev/null; then echo "dnf";
  elif command -v yum &>/dev/null; then echo "yum";
  elif command -v apk &>/dev/null; then echo "apk";
  else echo "unknown"; fi
}
PKG=$(detect_pkg)

install_deps() {
  log "安装系统依赖 (ffmpeg / nodejs / python3-venv) ..."
  case "$PKG" in
    apt)
      apt-get update -qq
      if ! command -v node &>/dev/null; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1 || warn "nodesource 安装失败，尝试 apt 默认版本"
      fi
      apt-get install -y ffmpeg nodejs python3 python3-venv python3-pip curl
      ;;
    dnf|yum)
      command -v node &>/dev/null || dnf install -y nodejs
      dnf install -y ffmpeg python3 python3-pip curl
      ;;
    apk)
      apk add --no-cache ffmpeg nodejs python3 py3-pip curl
      ;;
    *)
      warn "未识别的包管理器，请手动安装 ffmpeg / nodejs / python3"
      ;;
  esac
}
if ! command -v ffmpeg &>/dev/null || ! command -v node &>/dev/null || ! command -v python3 &>/dev/null; then
  install_deps
else
  log "系统依赖已就绪 (ffmpeg: $(command -v ffmpeg), node: $(command -v node), python3: $(command -v python3))"
fi

# ---------------------------------------------------------------------------
# 2. 创建服务用户
# ---------------------------------------------------------------------------
if ! id "$SVC_USER" &>/dev/null; then
  useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SVC_USER"
  log "已创建系统用户 $SVC_USER"
fi

# ---------------------------------------------------------------------------
# 3. 复制项目文件
# ---------------------------------------------------------------------------
log "复制项目到 $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp -a "$SRC_DIR"/. "$INSTALL_DIR"/
rm -rf "$INSTALL_DIR"/.git "$INSTALL_DIR"/downloads "$INSTALL_DIR"/logs 2>/dev/null || true
mkdir -p "$INSTALL_DIR"/downloads "$INSTALL_DIR"/logs "$INSTALL_DIR"/backup_config

# ---------------------------------------------------------------------------
# 4. Python 环境
# ---------------------------------------------------------------------------
# 优先选择系统安装的 Python（/usr/bin），避免单元文件 ProtectHome=true
# 屏蔽 /root、/home 下的解释器（如 mise 管理的 python）导致服务启动失败
if [[ -x /usr/bin/python3 ]]; then
  BASE_PYTHON=/usr/bin/python3
else
  BASE_PYTHON="$(command -v python3)"
fi

if [[ "$USE_VENV" == "1" ]]; then
  log "创建虚拟环境并安装 Python 依赖（基础解释器: $BASE_PYTHON）..."
  "$BASE_PYTHON" -m venv "$INSTALL_DIR/.venv"
  PYTHON_BIN="$INSTALL_DIR/.venv/bin/python"
else
  PYTHON_BIN="$BASE_PYTHON"
fi
"$PYTHON_BIN" -m pip install --upgrade pip -q
"$PYTHON_BIN" -m pip install -r "$INSTALL_DIR/requirements.txt" -q
log "Python 依赖安装完成 ($PYTHON_BIN)"

# ---------------------------------------------------------------------------
# 5. 生成并安装 systemd 单元文件
# ---------------------------------------------------------------------------
chown -R "$SVC_USER:$SVC_USER" "$INSTALL_DIR"

if [[ "$USE_SYSTEMD" == "0" ]]; then
  log "已跳过 systemd 注册（--no-systemd）。可直接运行: sudo -u $SVC_USER $PYTHON_BIN $INSTALL_DIR/main.py --web"
  exit 0
fi

command -v systemctl &>/dev/null || die "未检测到 systemd（容器内请使用 --no-systemd 或 Docker 部署）"

log "生成 systemd 单元文件 ..."
sed -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@PYTHON_BIN@|$PYTHON_BIN|g" \
    "$SCRIPT_DIR/douyinliverecorder.service" > "/etc/systemd/system/${SERVICE_NAME}.service"
sed -i "s|^Environment=WEBUI_PORT=.*|Environment=WEBUI_PORT=${WEBUI_PORT}|" "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
systemctl restart "$SERVICE_NAME"

sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
  log "✅ 服务已启动并设为开机自启"
else
  warn "服务启动失败，查看日志: journalctl -u $SERVICE_NAME -e"
fi

cat <<EOF

=====================================================================
 DouyinLiveRecorder 已封装为 systemd 服务
---------------------------------------------------------------------
 服务名      : $SERVICE_NAME
 安装目录    : $INSTALL_DIR
 运行用户    : $SVC_USER
 WebUI 地址  : http://<本机IP>:${WEBUI_PORT}
 任务文件    : $INSTALL_DIR/config/URL_config.ini（可用 WebUI 管理）
---------------------------------------------------------------------
 常用命令:
   systemctl status $SERVICE_NAME      查看状态
   journalctl -u $SERVICE_NAME -f      实时日志
   systemctl restart $SERVICE_NAME     重启服务
   systemctl stop $SERVICE_NAME        停止服务
 卸载:
   sudo $SCRIPT_DIR/install.sh --uninstall --purge
=====================================================================
EOF
