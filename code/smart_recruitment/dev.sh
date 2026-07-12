#!/usr/bin/env bash
#
# 智能招聘评测系统 —— 本地开发脚本
#
# 用法:
#   ./dev.sh            启动完整本地服务 (server + worker，推荐)
#   ./dev.sh both       启动完整本地服务 (server + worker，推荐)
#   ./dev.sh server     只启动 Django 开发服务器 (不会处理简历解析/题目生成队列)
#   ./dev.sh worker     启动后台 Worker (轮询 AiJob 队列，处理异步任务)
#   ./dev.sh worker-fg  前台运行 Worker (调试/在 Codex 中保持会话)
#   ./dev.sh server-fg  前台运行 Django server (调试/查看热重载日志)
#   ./dev.sh migrate    执行数据库迁移
#   ./dev.sh makemigrations  生成迁移文件
#   ./dev.sh createsuperuser 创建管理员账户
#   ./dev.sh shell      进入 Django shell
#   ./dev.sh stop       停止所有本地服务 (server / worker)
#   ./dev.sh status     查看服务状态
#   ./dev.sh resetdb    重建 sqlite 数据库 (危险! 会清空数据并重新迁移、建 admin)
#   ./dev.sh logs server|worker  查看指定服务日志
#   ./dev.sh tail workers  打开 (仅查) worker.log 末尾
#
# 可自定义端口: PORT=9000 ./dev.sh
# 可自定义 host: HOST=0.0.0.0 ./dev.sh
#
set -euo pipefail

# ---------------- 路径 / 变量 ----------------
cd "$(dirname "$0")"

APP_DIR="$(pwd)"
VENV="${APP_DIR}/.venv"
PY="${VENV}/bin/python"
MANAGE="${PY} ${APP_DIR}/manage.py"
DATA_DIR="${APP_DIR}/data"
LOG_DIR="${DATA_DIR}"
PID_DIR="${APP_DIR}/.run"
SERVER_PID="${PID_DIR}/server.pid"
WORKER_PID="${PID_DIR}/worker.pid"
SERVER_LOG="${LOG_DIR}/server.log"
WORKER_LOG="${LOG_DIR}/worker.log"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

mkdir -p "${PID_DIR}" "${LOG_DIR}"

# ---------------- 辅助 ----------------
c_red()   { printf "\033[31m%s\033[0m\n" "$*"; }
c_green() { printf "\033[32m%s\033[0m\n" "$*"; }
c_yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }
c_blue()  { printf "\033[36m%s\033[0m\n" "$*"; }

is_running() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
find_worker_pid() { pgrep -f "manage.py run_worker" 2>/dev/null | head -1 || true; }

status_line() {
  local name="$1" pidfile="$2" logfile="$3"
  if is_running "$pidfile"; then
    c_green "  ● $name: 运行中 (pid $(cat "$pidfile"))  log: $logfile"
  elif [ -f "$pidfile" ]; then
    c_yellow "  ○ $name: 已停止 (pid 文件残留: $(cat "$pidfile"))"
  else
    c_red    "  ○ $name: 未启动"
  fi
}

stop_pidfile() {
  local name="$1" pidfile="$2"
  if is_running "$pidfile"; then
    local pid; pid="$(cat "$pidfile")"
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    c_green "已停止 $name (pid $pid)"
  elif [ -f "$pidfile" ]; then
    rm -f "$pidfile"
    c_yellow "$name pid 文件残留已清理"
  else
    c_yellow "$name 未在运行"
  fi
  rm -f "$pidfile"
}

# ---------------- 命令 ----------------
cmd_check_env() {
  if [ ! -d "$VENV" ]; then
    c_red "未找到虚拟环境 $VENV，请先创建: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
  fi
  if [ ! -f "${APP_DIR}/.env" ]; then
    if [ -f "${APP_DIR}/.env.example" ]; then
      c_yellow "未发现 .env，从 .env.example 复制一份..."
      cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
      c_yellow "请按需编辑 ${APP_DIR}/.env (LLM_API_KEY 等) 后再启动。"
    else
      c_red "未发现 .env，且没有 .env.example 模板。"
      exit 1
    fi
  fi
}

cmd_server() {
  cmd_check_env
  if is_running "$SERVER_PID"; then
    c_yellow "服务器已在运行 (pid $(cat "$SERVER_PID"))，先停止再启动或刷新页面试试。"
    return 0
  fi
  rm -f "$SERVER_PID"
  c_blue "启动 Django 开发服务器: http://${HOST}:${PORT}  (日志: $SERVER_LOG)"
  # runserver 默认启用热重载；用 nohup 后台运行并把 pid 写文件，方便 stop
  nohup "$PY" manage.py runserver "${HOST}:${PORT}" >"$SERVER_LOG" 2>&1 &
  echo $! > "$SERVER_PID"
  sleep 2
  if is_running "$SERVER_PID"; then
    c_green "服务器已启动 (pid $(cat "$SERVER_PID"))"
    c_blue "  访问: http://${HOST}:${PORT}/"
    c_blue "  提示: 简历解析、题目生成、AI评分依赖 worker；本地开发建议使用 ./dev.sh 或 ./dev.sh both。"
  else
    c_red "服务器启动失败，查看日志: $SERVER_LOG"
    tail -30 "$SERVER_LOG" || true
    exit 1
  fi
}

cmd_server_foreground() {
  cmd_check_env
  c_blue "前台启动 Django 开发服务器: http://${HOST}:${PORT}  (Ctrl+C 停止)"
  exec "$PY" manage.py runserver "${HOST}:${PORT}"
}

cmd_worker() {
  cmd_check_env
  if is_running "$WORKER_PID"; then
    c_yellow "Worker 已在运行 (pid $(cat "$WORKER_PID"))。"
    return 0
  fi
  local unmanaged_pid
  unmanaged_pid="$(find_worker_pid)"
  if [ -n "$unmanaged_pid" ]; then
    echo "$unmanaged_pid" > "$WORKER_PID"
    c_yellow "检测到已有 Worker 运行 (pid $unmanaged_pid)，已接管 pid 文件。"
    return 0
  fi
  rm -f "$WORKER_PID"
  c_blue "启动后台 Worker (处理简历解析/题目生成/评分报告队列，日志: $WORKER_LOG)"
  nohup "$PY" manage.py run_worker --sleep 2 >"$WORKER_LOG" 2>&1 &
  echo $! > "$WORKER_PID"
  sleep 1
  if is_running "$WORKER_PID"; then
    c_green "Worker 已启动 (pid $(cat "$WORKER_PID"))"
  else
    c_red "Worker 启动失败，查看日志: $WORKER_LOG"
    tail -30 "$WORKER_LOG" || true
    exit 1
  fi
}

cmd_worker_foreground() {
  cmd_check_env
  c_blue "前台启动后台 Worker (Ctrl+C 停止)"
  exec "$PY" manage.py run_worker --sleep 2
}

cmd_both() {
  c_blue "启动完整本地服务: server + worker"
  cmd_worker
  cmd_server
  echo
  cmd_status
}

cmd_stop() {
  stop_pidfile "server" "$SERVER_PID"
  stop_pidfile "worker" "$WORKER_PID"
}

cmd_status() {
  c_blue "本地服务状态:"
  status_line "server" "$SERVER_PID" "$SERVER_LOG"
  if is_running "$WORKER_PID"; then
    c_green "  ● worker: 运行中 (pid $(cat "$WORKER_PID"))  log: $WORKER_LOG"
  else
    local unmanaged_pid
    unmanaged_pid="$(find_worker_pid)"
    if [ -n "$unmanaged_pid" ]; then
      c_yellow "  ● worker: 运行中 (pid ${unmanaged_pid}，未由 pid 文件托管)  log: $WORKER_LOG"
    elif [ -f "$WORKER_PID" ]; then
      c_yellow "  ○ worker: 已停止 (pid 文件残留: $(cat "$WORKER_PID"))"
      c_yellow "  提醒: worker 未启动时，简历解析页面会停留在排队中。运行 ./dev.sh worker 或 ./dev.sh both。"
    else
      c_red "  ○ worker: 未启动"
      c_yellow "  提醒: worker 未启动时，简历解析页面会停留在排队中。运行 ./dev.sh worker 或 ./dev.sh both。"
    fi
  fi
  echo
  c_blue "端口监听:"
  (lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || c_yellow "  端口 ${PORT} 无监听") | head -5
}

cmd_migrate() {
  cmd_check_env
  c_blue "执行数据库迁移..."
  $MANAGE migrate
}

cmd_makemigrations() {
  cmd_check_env
  c_blue "生成迁移文件..."
  $MANAGE makemigrations recruitment
}

cmd_createsuperuser() {
  cmd_check_env
  c_blue "创建超级用户 (按提示输入)..."
  $MANAGE createsuperuser
}

cmd_shell() {
  cmd_check_env
  $MANAGE shell
}

cmd_resetdb() {
  c_red "⚠ 这会删除 ${DATA_DIR}/app.sqlite3 并重建，清空所有数据。确认请输入 yes:"
  read -r ans
  [ "$ans" = "yes" ] || { c_yellow "已取消"; exit 0; }
  cmd_stop >/dev/null 2>&1 || true
  rm -rf "${DATA_DIR}/app.sqlite3"
  cmd_migrate
  c_blue "重建默认 admin 账户 (admin / admin)..."
  DJANGO_SUPERUSER_PASSWORD=admin "$PY" -c "
import os,django; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from django.contrib.auth import get_user_model
U=get_user_model()
U.objects.filter(username='admin').delete()
U.objects.create_superuser('admin','','admin')
print('admin 创建完成')
"
  c_green "数据库已重建。账户: admin / admin  访问: http://${HOST}:${PORT}/"
}

cmd_logs() {
  local which="${1:-server}"
  case "$which" in
    server) local f="$SERVER_LOG" ;;
    worker) local f="$WORKER_LOG" ;;
    *) c_red "用法: $0 logs server|worker"; exit 1 ;;
  esac
  [ -f "$f" ] || { c_yellow "$f 不存在"; exit 0; }
  c_blue "tail -f $f  (Ctrl+C 退出)"
  tail -n 100 -f "$f"
}

cmd_tail_worker() {
  [ -f "$WORKER_LOG" ] || { c_yellow "$WORKER_LOG 不存在"; exit 0; }
  tail -n "${1:-50}" "$WORKER_LOG"
}

# ---------------- 入口 ----------------
sub="${1:-both}"
case "$sub" in
  server)    shift || true; cmd_server "$@" ;;
  server-fg|server-foreground) shift || true; cmd_server_foreground "$@" ;;
  worker)    shift || true; cmd_worker "$@" ;;
  worker-fg|worker-foreground) shift || true; cmd_worker_foreground "$@" ;;
  both)      shift || true; cmd_both "$@" ;;
  stop)      cmd_stop ;;
  status)     cmd_status ;;
  migrate)    cmd_migrate ;;
  makemigrations|makemigs) cmd_makemigrations ;;
  createsuperuser|createuser) cmd_createsuperuser ;;
  shell)      cmd_shell ;;
  resetdb)    cmd_resetdb ;;
  logs)       shift || true; cmd_logs "$@" ;;
  tail)       shift || true; cmd_tail_worker "$@" ;;
  help|--help|-h)
    cat <<EOF
$(c_blue "智能招聘评测系统 —— 本地开发脚本")

用法: $0 <命令> [参数]

命令:
  server       启动 Django 开发服务器 (HOST/PORT 可自定义)
  worker       启动后台 Worker (轮询 AiJob 队列)
  both         同时启动 server + worker
  stop         停止所有本地服务
  status       查看服务运行状态
  migrate      执行数据库迁移
  makemigrations 生成迁移文件 (recruitment app)
  createsuperuser  创建管理员账户
  shell        进入 Django shell
  resetdb      重建 sqlite 并初始化 admin/admin  (危险: 清空数据)
  logs server  跟踪 server 日志
  logs worker  跟踪 worker 日志
  tail [N]     打印 worker 日志末尾 N 行 (默认 50)

环境变量:
  HOST  监听地址  (默认 127.0.0.1)
  PORT  监听端口  (默认 8000)

示例:
  $0 server
  PORT=9000 $0 both
  $0 logs server
EOF
    ;;
  *)
    c_red "未知命令: $sub"
    "$0" help
    exit 1
    ;;
esac
