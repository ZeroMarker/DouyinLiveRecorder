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
#   --user USER         服务运行用户（默认 ubuntu）
#   --no-venv           使用系统 python3 而非创建虚拟环境
#   --no-systemd        仅安装文件，不注册/启动 systemd 服务
#   --upgrade           升级模式：跳过系统依赖与服务用户检查，复用已有虚拟环境
#   --allow-dirty       允许从含未提交改动的源码目录安装（默认拒绝，见下）
#   --force-dir         跳过安装目录的常规性检查（仅当 --install-dir 指向非默认路径时需要）
#   --check             只校验安装目录与安装清单是否一致，不做任何修改
#   --uninstall         卸载（停止并移除服务、删除用户；加 --purge 删除安装目录）
#
# 安装时会为每个代码文件生成 sha256 清单（.deploy-manifest.json），安装结束、
# 服务启动、WebUI 加载时都会比对，用于发现“跨版本混装”（不同版本的文件互相调用，
# 只会在某条操作路径上抛运行时错误）。因此默认拒绝从有未提交改动的源码目录安装：
# 那种状态下安装内容不对应任何提交，正是混装的来源。
# ============================================================================
set -euo pipefail

INSTALL_DIR="/opt/DouyinLiveRecorder"
WEBUI_PORT="8000"
SVC_USER="ubuntu"
USE_VENV=1
USE_SYSTEMD=1
UNINSTALL=0
PURGE=0
UPGRADE=0
ALLOW_DIRTY=0
CHECK_ONLY=0
FORCE_DIR=0
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
    --upgrade) UPGRADE=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    --force-dir) FORCE_DIR=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

log()  { echo -e "\033[1;32m[install]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*"; }
die()  { echo -e "\033[1;31m[error]\033[0m $*" >&2; exit 1; }

DEPLOY_CHECK="$SRC_DIR/src/deploy_check.py"

# 清单/校验统一走 src/deploy_check.py：目录名单与哈希逻辑只此一份
py_deploy_check() {
  if command -v python3 &>/dev/null; then
    python3 "$DEPLOY_CHECK" "$@"
  elif [[ -x "$INSTALL_DIR/.venv/bin/python" ]]; then
    "$INSTALL_DIR/.venv/bin/python" "$DEPLOY_CHECK" "$@"
  else
    die "需要 python3 生成/校验部署清单"
  fi
}

# 安装目录校验：prune 会删除目录内的旧代码条目，--purge 会整目录删除，chown -R 会改属主，
# 因此路径必须先确认"确实是本项目的安装目录"。`${VAR:?}` 只能拦住空值，拦不住
# `--install-dir /home` 这类笔误（那会删掉 /home/<同名目录> 并把家目录改属主）。
validate_install_dir() {
  local dir="$1" base
  [[ "$dir" == /* ]] || die "安装目录必须是绝对路径: $dir"
  # 归一化父目录（不跟随最后一段，避免把符号链接解析成别的路径后校验与实际操作对象不一致）
  local parent
  parent="$(cd -P -- "$(dirname -- "$dir")" 2>/dev/null && pwd)" || parent=""
  base="$(basename -- "$dir")"
  if [[ -z "$parent" || ! -d "$parent" ]]; then
    die "安装目录的父目录不存在: $(dirname -- "$dir")（请先创建，例如 sudo mkdir -p $(dirname -- "$dir")）"
  fi
  dir="$parent/$base"
  [[ "$dir" != "/" ]] || die "安装目录不能是根目录"
  [[ ! -L "$dir" ]] || die "安装目录 $dir 是符号链接：链接可在校验后被改写指向别处，请改用真实目录"
  case "$dir" in
    /|/home|/home/*|/root|/root/*|/usr|/usr/*|/etc|/etc/*|/var|/var/*|/opt|/boot|/boot/*|/srv|/srv/*|/tmp|/tmp/*|/mnt|/mnt/*|/media|/media/*|/dev|/dev/*|/proc|/proc/*|/sys|/sys/*|/run|/run/*)
      die "拒绝把 $dir 作为安装目录：安装会删除其中的旧代码条目并执行 chown -R，必须使用专属子目录（默认 /opt/DouyinLiveRecorder）" ;;
  esac
  [[ "$base" == "DouyinLiveRecorder" || "$FORCE_DIR" == "1" ]] || \
    die "安装目录名 '$base' 不像本项目目录（默认 /opt/DouyinLiveRecorder）；确认无误请加 --force-dir"
  if [[ -d "$dir" && ! -f "$dir/main.py" && ! -f "$dir/.deploy-manifest.json" && "$FORCE_DIR" != "1" ]]; then
    die "$dir 已存在但不是本项目安装目录（缺少 main.py）；确认无误请加 --force-dir"
  fi
  # 归一化后的路径回写，后续所有操作都用它
  INSTALL_DIR="$dir"
}

# 源码目录与安装目录不得相同或互相包含。
#
# 安装是"先删旧代码、再从源码复制"，两者相同时第一步就会把源码删掉（实测会删掉
# main.py、requirements.txt、src/ 等，然后无源码可复制，安装目录只剩运行数据）。
# 注意 systemd 部署下 install.sh 本身就在安装目录里（$INSTALL_DIR/deploy/install.sh），
# 因此"在安装目录里重跑脚本升级"这条路径必须被明确拒绝，而不是"尽力而为"。
validate_source_isolation() {
  local src inst
  src="$(realpath -m -- "$SRC_DIR")"
  inst="$(realpath -m -- "$INSTALL_DIR")"
  if [[ "$src" == "$inst" ]]; then
    die "源码目录与安装目录相同（$src）：安装会先清理旧代码再复制，就地安装会互相删除。请在源码副本目录执行，或改用 --install-dir 指向另一个目录"
  fi
  case "$src/" in
    "$inst"/*) die "源码目录位于安装目录内部（$src）：清理阶段会删除源码。请把源码放在安装目录之外" ;;
  esac
  case "$inst/" in
    "$src"/*) die "安装目录位于源码目录内部（$inst）：复制阶段会把安装目录递归复制进自身。请改用 --install-dir 指向源码目录之外" ;;
  esac
}

# ---------------------------------------------------------------------------
# --check：只校验，不改动任何文件（不需要 root）
# ---------------------------------------------------------------------------
if [[ "$CHECK_ONLY" == "1" ]]; then
  [[ -d "$INSTALL_DIR" ]] || die "安装目录不存在: $INSTALL_DIR"
  py_deploy_check verify --dir "$INSTALL_DIR"
  exit $?
fi

# ---------------------------------------------------------------------------
# 安装路径检查（先于 root 检查：非 root 运行时也应先报出"就地安装/目录选错"，
# 而不是让人以为只是缺 sudo）
# ---------------------------------------------------------------------------
if [[ "$UNINSTALL" != "1" ]]; then
  validate_install_dir "$INSTALL_DIR"
  validate_source_isolation
fi

# ---------------------------------------------------------------------------
# 卸载
# ---------------------------------------------------------------------------
if [[ "$UNINSTALL" == "1" ]]; then
  if [[ "$PURGE" == "1" ]]; then validate_install_dir "$INSTALL_DIR"; fi
  log "停止并移除服务 $SERVICE_NAME ..."
  if systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
  fi
  if [[ "$SVC_USER" == "ubuntu" ]]; then
    warn "保留登录用户 ubuntu，仅移除服务（不删除用户）"
  elif id "$SVC_USER" &>/dev/null; then
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

log "安装目录: $INSTALL_DIR"
log "源码目录: $SRC_DIR"

# ---------------------------------------------------------------------------
# 0. 源码来源登记与“跨版本混装”拦截
# ---------------------------------------------------------------------------
SRC_COMMIT=""
SRC_BRANCH=""
SRC_DIRTY="false"
if command -v git &>/dev/null && git -C "$SRC_DIR" rev-parse --git-dir &>/dev/null; then
  SRC_COMMIT="$(git -C "$SRC_DIR" rev-parse HEAD 2>/dev/null || true)"
  SRC_BRANCH="$(git -C "$SRC_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  DIRTY_FILES="$(git -C "$SRC_DIR" status --porcelain 2>/dev/null || true)"
  if [[ -n "$DIRTY_FILES" ]]; then
    SRC_DIRTY="true"
    warn "源码目录有未提交改动，安装内容不对应任何提交（跨版本混装的来源）："
    # 只显示前 20 行：这里不能用 head（提前关闭管道会让 printf 收到 SIGPIPE，
    # 在 set -o pipefail 下会直接中止脚本）
    DIRTY_SHOWN="$(printf '%s\n' "$DIRTY_FILES" | sed -n '1,20p' | sed 's/^/    /')"
    printf '%s\n' "$DIRTY_SHOWN"
    DIRTY_COUNT="$(printf '%s\n' "$DIRTY_FILES" | wc -l)"
    if [[ "$DIRTY_COUNT" -gt 20 ]]; then warn "（共 $DIRTY_COUNT 项改动，仅显示前 20 项）"; fi
    if [[ "$ALLOW_DIRTY" != "1" ]]; then
      die "源码目录有未提交改动，安装内容不对应任何提交（跨版本混装的来源）。推荐先提交/合并到干净工作区再安装；若确实要安装当前工作区内容，请加 --allow-dirty，安装后 WebUI 会持续提示该部署含本地改动"
    fi
    warn "已按 --allow-dirty 继续：清单按当前工作区内容生成（会标记 dirty），WebUI 与日志会持续提示该部署含本地改动"
  fi
  # 注意不能用 ${SRC_DIRTY:+ ...}：该变量始终有值（"false"），会无条件拼上后缀
  DIRTY_LABEL=""
  if [[ "$SRC_DIRTY" == "true" ]]; then DIRTY_LABEL=" (含未提交改动)"; fi
  log "源码来源: ${SRC_BRANCH:-?} ${SRC_COMMIT:0:12}${DIRTY_LABEL}"
else
  warn "源码目录不是 git 仓库，无法登记提交号（安装清单仍会记录文件哈希）"
fi

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
if [[ "$UPGRADE" == "1" ]]; then
  log "升级模式：跳过系统依赖检查"
elif ! command -v ffmpeg &>/dev/null || ! command -v node &>/dev/null || ! command -v python3 &>/dev/null; then
  install_deps
else
  log "系统依赖已就绪 (ffmpeg: $(command -v ffmpeg), node: $(command -v node), python3: $(command -v python3))"
fi

# ---------------------------------------------------------------------------
# 2. 创建服务用户
# ---------------------------------------------------------------------------
if [[ "$UPGRADE" == "1" ]]; then
  id "$SVC_USER" &>/dev/null || die "升级模式下服务用户不存在: $SVC_USER"
elif ! id "$SVC_USER" &>/dev/null; then
  if [[ "$SVC_USER" == "ubuntu" ]]; then
    useradd --create-home --shell /bin/bash "$SVC_USER"
  else
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SVC_USER"
  fi
  log "已创建用户 $SVC_USER"
fi

# ---------------------------------------------------------------------------
# 3. 复制项目文件
#
# 只替换"代码"：先删掉安装目录里属于本项目的旧代码条目（保留运行数据与运维自建
# 目录），再从源码目录复制。之前直接 cp -a 会留下源码中已删除的旧文件（曾导致
# webui/static/ 里躺着一份过期的 app.py），并且会把开发机的 config/ 覆盖到安装
# 目录、抹掉已配置的任务。
# 跳过名单与"该删哪些"的判断都在 src/deploy_check.py 里，与安装清单/校验共用一份。
# ---------------------------------------------------------------------------
# 跳过名单同样先落文件再判退出码：< <(...) 吞掉失败会让名单变空，
# 那样连 config/ downloads/ 都会被当成代码复制（覆盖安装目录里的任务配置）。
EXCLUDES_OUT="$(mktemp)"
if ! py_deploy_check excludes > "$EXCLUDES_OUT"; then
  rm -f "$EXCLUDES_OUT"
  die "读取跳过目录名单失败（src/deploy_check.py excludes 退出异常），已中止安装"
fi
mapfile -t CODE_EXCLUDES < "$EXCLUDES_OUT"
rm -f "$EXCLUDES_OUT"
if [[ "${#CODE_EXCLUDES[@]}" -eq 0 ]]; then
  die "跳过目录名单为空，已中止安装（避免把 config/ downloads/ 等运行数据当作代码覆盖）"
fi

# 顶层条目是否属于"运行数据/环境"（不复制、不删除）
is_excluded() {
  local name="$1" entry
  for entry in ${CODE_EXCLUDES[@]+"${CODE_EXCLUDES[@]}"}; do
    if [[ "$name" == "$entry" ]]; then return 0; fi
  done
  return 1
}

log "同步代码到 $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"

# 删除上一版安装的本项目代码（含源码中已删除的文件）；运维自建条目由 prune 保留并提示。
# 输出先落临时文件再判断退出码：进程替换 < <(...) 会吞掉子命令的失败，
# prune 一旦崩溃就会静默"什么都不删"，留下旧代码并让后续校验看似通过。
PRUNE_OUT="$(mktemp)"
PRUNE_RC=0
py_deploy_check prune --dir "$INSTALL_DIR" --source "$SRC_DIR" > "$PRUNE_OUT" || PRUNE_RC=$?
if [[ "$PRUNE_RC" -eq 3 ]]; then
  rm -f "$PRUNE_OUT"
  die "安装目录存在名字不安全的条目（含换行/控制字符，见上方列表），已中止安装"
elif [[ "$PRUNE_RC" -ne 0 ]]; then
  rm -f "$PRUNE_OUT"
  die "计算待清理条目失败（src/deploy_check.py prune 退出码 $PRUNE_RC），已中止安装以免留下混合版本"
fi
REMOVED=0
while IFS= read -r name; do
  if [[ -n "$name" ]]; then
    # 逐层校验：名字必须是单一、安全的路径组件，且解析后仍严格位于安装目录内。
    # （prune 已对含换行/控制字符的名字退出非 0，这里是第二层防护，避免任何
    #  拆分后的片段被拼进 rm -rf 目标。）
    case "$name" in
      */*|.|..|*[![:print:]]*)
        rm -f "$PRUNE_OUT"
        die "待清理条目名不安全，已中止安装: $(printf '%q' "$name")" ;;
    esac
    target="$(realpath -m -- "${INSTALL_DIR:?}/$name")"
    if [[ "$target" != "$INSTALL_DIR"/* ]]; then
      rm -f "$PRUNE_OUT"
      die "待清理条目不在安装目录内，已中止安装: $target"
    fi
    rm -rf "$target"
    REMOVED=$((REMOVED + 1))
  fi
done < "$PRUNE_OUT"
rm -f "$PRUNE_OUT"
log "已清理 $REMOVED 个旧代码条目"

shopt -s dotglob nullglob
for path in "$SRC_DIR"/*; do
  name="$(basename "$path")"
  if is_excluded "$name"; then continue; fi
  cp -a "$path" "$INSTALL_DIR"/
done
shopt -u dotglob nullglob

mkdir -p "$INSTALL_DIR"/downloads "$INSTALL_DIR"/logs "$INSTALL_DIR"/backup_config "$INSTALL_DIR"/config
# 首次安装（或配置缺失）时补默认配置；已有配置一律保留
if [[ ! -f "$INSTALL_DIR/config/config.ini" ]]; then
  if [[ -f "$SRC_DIR/config/config.ini" ]]; then
    cp -a "$SRC_DIR/config/config.ini" "$INSTALL_DIR/config/config.ini"
  else
    : > "$INSTALL_DIR/config/config.ini"
  fi
  log "已初始化 config/config.ini"
fi
[[ -f "$INSTALL_DIR/config/URL_config.ini" ]] || : > "$INSTALL_DIR/config/URL_config.ini"
log "代码已同步（保留 config/ downloads/ logs/ backup_config/ .venv/ 等运行数据）"

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
  if [[ "$UPGRADE" == "1" && -x "$INSTALL_DIR/.venv/bin/python" ]]; then
    log "升级模式：复用已有虚拟环境 $INSTALL_DIR/.venv"
  else
    log "创建虚拟环境并安装 Python 依赖（基础解释器: $BASE_PYTHON）..."
    "$BASE_PYTHON" -m venv "$INSTALL_DIR/.venv"
  fi
  PYTHON_BIN="$INSTALL_DIR/.venv/bin/python"
else
  PYTHON_BIN="$BASE_PYTHON"
fi
"$PYTHON_BIN" -m pip install --upgrade pip -q
"$PYTHON_BIN" -m pip install -r "$INSTALL_DIR/requirements.txt" -q
log "Python 依赖安装完成 ($PYTHON_BIN)"

# ---------------------------------------------------------------------------
# 5. 生成安装清单并校验
#
# 清单记录每个代码文件的 sha256，且哈希取自**源码目录**（--source）：只有描述
# "源码应该长什么样"，安装后比对才有意义——如果从安装目录自身生成，比对必然通过，
# 清理失败的残留会被写进清单"洗白"。校验通过即说明安装内容与源码逐字节一致；
# 残留文件会以 extra 形式暴露。服务启动与 WebUI 加载时会再比对一次，用于发现
# 事后发生的"跨版本混装"（不同版本文件互相调用，只会在某条操作路径上抛运行时错误）。
# ---------------------------------------------------------------------------
log "生成部署清单 ..."
MANIFEST_ARGS=(make --dir "$INSTALL_DIR" --source "$SRC_DIR" --commit "$SRC_COMMIT" --branch "$SRC_BRANCH")
if [[ "$SRC_DIRTY" == "true" ]]; then MANIFEST_ARGS+=(--dirty); fi
py_deploy_check "${MANIFEST_ARGS[@]}"

POST_VERIFY_RC=0
py_deploy_check verify --dir "$INSTALL_DIR" || POST_VERIFY_RC=$?
if [[ "$POST_VERIFY_RC" -eq 3 ]]; then
  # 清单外文件是提示性质（运维放在安装目录里的东西），不能中止安装：
  # 此时代码已替换，中止只会造成"磁盘新代码 / 内存旧代码"的分裂状态。
  warn "安装目录存在清单外文件（非本项目文件，已按要求保留，见上方提示）；不影响本次安装"
elif [[ "$POST_VERIFY_RC" -ne 0 ]]; then
  die "安装后校验失败（退出码 $POST_VERIFY_RC）：安装目录代码与源码不一致，已中止。
     注意：安装目录里的代码已被替换，但服务尚未重启。请修复后重跑本脚本，再执行:
       systemctl restart $SERVICE_NAME"
fi
# 安装标记：清单若被误删（例如在安装目录执行 git clean -xfd），启动时按"无法校验"告警
py_deploy_check sentinel --dir "$INSTALL_DIR" --source "$SRC_DIR" \
  --commit "$SRC_COMMIT" --branch "$SRC_BRANCH"

# ---------------------------------------------------------------------------
# 6. 生成并安装 systemd 单元文件
# ---------------------------------------------------------------------------
# 删除/复制之后再确认一次：中间任何一步都不该让 INSTALL_DIR 变成别的目标
[[ "$(readlink -f -- "$INSTALL_DIR")" == "$INSTALL_DIR" ]] || \
  die "安装目录在校验后发生了变化（$INSTALL_DIR），已中止"
chown -R "$SVC_USER:$SVC_USER" "$INSTALL_DIR"

if [[ "$USE_SYSTEMD" == "0" ]]; then
  log "已跳过 systemd 注册（--no-systemd）。可直接运行: sudo -u $SVC_USER $PYTHON_BIN $INSTALL_DIR/main.py --web"
  exit 0
fi

command -v systemctl &>/dev/null || die "未检测到 systemd（容器内请使用 --no-systemd 或 Docker 部署）"

log "生成 systemd 单元文件 ..."
sed -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@PYTHON_BIN@|$PYTHON_BIN|g" \
    -e "s|@SVC_USER@|$SVC_USER|g" \
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
 源码目录    : $SRC_DIR
 运行用户    : $SVC_USER
 WebUI 上游  : http://127.0.0.1:${WEBUI_PORT}（仅本机监听，请通过反向代理访问）
 任务文件    : $INSTALL_DIR/config/URL_config.ini（可用 WebUI 管理）
 部署清单    : $INSTALL_DIR/.deploy-manifest.json（代码哈希，用于发现版本混装）
---------------------------------------------------------------------
 常用命令:
   systemctl status $SERVICE_NAME      查看状态
   journalctl -u $SERVICE_NAME -f      实时日志
   systemctl restart $SERVICE_NAME     重启服务
   systemctl stop $SERVICE_NAME        停止服务
 升级（保留 config/downloads 数据）:
   在源码目录 $SRC_DIR 执行:
   sudo $SRC_DIR/deploy/install.sh --upgrade --port $WEBUI_PORT
   ※ 不能在安装目录内就地升级：安装会先清理旧代码再复制，源码与安装目录相同时
     会把源码一起删掉，脚本会直接拒绝这种用法。
 校验安装目录与清单是否一致（可在安装目录内执行，只读）:
   sudo $INSTALL_DIR/deploy/install.sh --check
 卸载:
   sudo $SRC_DIR/deploy/install.sh --uninstall --purge
=====================================================================
EOF
