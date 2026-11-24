#!/usr/bin/env bash
# 自动更新：由 systemd 定时器 elpida-pull.timer 每 15 分钟以普通用户身份运行。
# 拉 GitHub main 的新代码；有变化时（必要时更新依赖、检查语法通过后）通知 systemd 重启 Elpida 的服务。
# 整个逻辑放在函数里：bash 会先读完整个函数再执行，git pull 改写本文件时不会读到一半的新内容。
set -euo pipefail

main() {
  local repo venv data old new
  repo="$(cd "$(dirname "$0")/.." && pwd)"
  venv="$HOME/.local/share/elpida/venv"
  data="${ELPIDA_DATA:-$HOME/elpida-data}"
  cd "$repo"
  old="$(git rev-parse HEAD)"
  git pull --ff-only -q
  new="$(git rev-parse HEAD)"
  [ "$old" = "$new" ] && return 0
  echo "代码更新：${old:0:7} → ${new:0:7}"
  if [ ! -x "$venv/bin/python" ]; then
    echo "还没运行过 deploy/install.sh，只更新代码"
    return 0
  fi
  if ! git diff --quiet "$old" "$new" -- requirements.txt; then
    echo "依赖有变化，安装中"
    "$venv/bin/pip" install -q -r requirements.txt
  fi
  if ! "$venv/bin/python" -m compileall -q elpida >/dev/null; then
    echo "新代码有语法错误，服务不重启，继续用旧进程"
    return 1
  fi
  mkdir -p "$data"
  date -Is > "$data/.deployed"      # elpida-restart.path 看到这个文件被写，就重启网站和总管
  echo "已通知重启服务"
}

main "$@"
exit $?
