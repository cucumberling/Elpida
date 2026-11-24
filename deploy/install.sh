#!/usr/bin/env bash
# Elpida 服务器一键安装（Debian / Ubuntu）。用普通用户运行，不要加 sudo（脚本需要时自己调用 sudo）：
#
#   bash ~/Elpida/deploy/install.sh
#
# 做的事：
#   1. 装系统依赖和 Python 虚拟环境（~/.local/share/elpida/venv）
#   2. 生成网站访问钥匙（~/elpida-data/web_token，只在这台服务器上）
#   3. 注册 systemd 服务：elpida-agents（总管，调度所有 agent）、elpida-web（网站，只听 127.0.0.1:8780）
#   4. 自动更新：elpida-pull.timer 每 15 分钟拉 GitHub，有新代码就自动重启上面两个服务
#   5. HTTPS：用 Caddy 自动申请证书，域名 elpida.<IP>.sslip.io（免费，不用买域名）
# 可以重复运行：更新安装脚本本身之后再跑一次即可，数据不会丢。
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ME="$(id -un)"
DATA="$HOME/elpida-data"
VENV="$HOME/.local/share/elpida/venv"
PORT=8780
MARK="# managed-by: elpida"

if [ "$(id -u)" -eq 0 ]; then echo "请用普通用户运行（不要 sudo），脚本会在需要时自己调用 sudo"; exit 1; fi
[ -f "$REPO/elpida/__main__.py" ] || { echo "$REPO 不是 Elpida 仓库"; exit 1; }

owner_of() { { sudo ss -tlnpH "sport = :$1" 2>/dev/null | grep -o 'users:(("[^"]*"' | head -1 | cut -d'"' -f2; } || true; }

echo "==> 1/5 系统依赖（如果系统正在自动更新，会等它结束，最多 10 分钟）"
sudo apt-get -o DPkg::Lock::Timeout=600 update -qq
sudo apt-get -o DPkg::Lock::Timeout=600 install -y -qq python3 python3-venv git curl iproute2 >/dev/null

echo "==> 2/5 Python 虚拟环境和依赖（第一次装 pandas 要几分钟）"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$REPO/requirements.txt"
mkdir -p "$DATA"
chmod 700 "$DATA"
if [ ! -s "$DATA/web_token" ]; then
  ( umask 077; "$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(24))' > "$DATA/web_token" )
  echo "  已生成网站访问钥匙"
fi

echo "==> 3/5 systemd 服务"
unit() { sudo tee "/etc/systemd/system/$1" >/dev/null; }
for svc in agents web; do
  cmd=$([ $svc = agents ] && echo loop || echo web)
  desc=$([ $svc = agents ] && echo "Elpida orchestrator (runs all agents)" || echo "Elpida dashboard")
  unit "elpida-$svc.service" <<EOF
$MARK
[Unit]
Description=$desc
After=network-online.target
Wants=network-online.target
[Service]
User=$ME
WorkingDirectory=$REPO
Environment=ELPIDA_DATA=$DATA ELPIDA_PORT=$PORT PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
ExecStart=$VENV/bin/python -m elpida $cmd
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF
done
unit elpida-pull.service <<EOF
$MARK
[Unit]
Description=Pull Elpida from GitHub and redeploy if changed
After=network-online.target
[Service]
Type=oneshot
User=$ME
Environment=ELPIDA_DATA=$DATA
ExecStart=/bin/bash $REPO/deploy/update.sh
EOF
unit elpida-pull.timer <<EOF
$MARK
[Unit]
Description=Pull Elpida every 15 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true
[Install]
WantedBy=timers.target
EOF
# 新代码拉下来后，update.sh 写 ~/elpida-data/.deployed；这里看到就重启服务（重启由 systemd 自己做，不用给仓库代码 root 权限）
unit elpida-restart.path <<EOF
$MARK
[Unit]
Description=Restart Elpida when new code is deployed
[Path]
PathChanged=$DATA/.deployed
[Install]
WantedBy=multi-user.target
EOF
unit elpida-restart.service <<EOF
$MARK
[Unit]
Description=Restart Elpida services
[Service]
Type=oneshot
ExecStart=/bin/systemctl try-restart elpida-web.service elpida-agents.service
EOF
sudo systemctl daemon-reload
sudo systemctl enable -q elpida-agents elpida-web elpida-pull.timer elpida-restart.path
sudo systemctl restart elpida-agents elpida-web elpida-restart.path
sudo systemctl start elpida-pull.timer

echo "==> 4/5 HTTPS"
IP="$(curl -s -m 5 -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || true)"
[[ "$IP" =~ ^[0-9.]+$ ]] || IP="$(curl -s -m 10 https://api.ipify.org || true)"
[[ "$IP" =~ ^[0-9.]+$ ]] || { echo "  查不到本机公网 IP"; IP=""; }
P80="$(owner_of 80)"; P443="$(owner_of 443)"
URL=""
if [ -z "$IP" ]; then
  echo "  跳过 HTTPS（没有公网 IP）"
elif [ -n "$P80" ] && [ "$P80" != caddy ]; then
  echo "  80 端口被「$P80」占用，没法自动申请证书，跳过 HTTPS（没有改动它）。网站仍在本机 127.0.0.1:$PORT 运行。"
else
  if [ -z "$P443" ] || [ "$P443" = caddy ]; then HTTPS_PORT=443; else HTTPS_PORT=8443; echo "  443 被「$P443」占用，不碰它，网站用 8443 端口"; fi
  DOMAIN="elpida.${IP//./-}.sslip.io"
  if [ "$HTTPS_PORT" = 443 ]; then SITE="$DOMAIN"; URL="https://$DOMAIN"; else SITE="$DOMAIN:$HTTPS_PORT"; URL="https://$DOMAIN:$HTTPS_PORT"; fi
  if ! command -v caddy >/dev/null; then
    echo "  安装 Caddy"
    sudo apt-get -o DPkg::Lock::Timeout=600 install -y -qq caddy >/dev/null 2>&1 || {
      sudo apt-get -o DPkg::Lock::Timeout=600 install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
      sudo apt-get -o DPkg::Lock::Timeout=600 update -qq
      sudo apt-get -o DPkg::Lock::Timeout=600 install -y -qq caddy >/dev/null
    }
  fi
  sudo tee /etc/caddy/elpida.caddy >/dev/null <<EOF
$MARK
$SITE {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT
}
EOF
  CF=/etc/caddy/Caddyfile
  BAK="$CF.before-elpida-$(date +%Y%m%d-%H%M%S)"
  [ -f "$CF" ] && sudo cp -p "$CF" "$BAK"
  if [ ! -f "$CF" ] || sudo grep -q '/usr/share/caddy' "$CF"; then
    # 还是安装包自带的示例配置：换成只引入 Elpida 站点
    printf '%s\nimport /etc/caddy/elpida.caddy\n' "$MARK" | sudo tee "$CF" >/dev/null
  elif ! sudo grep -q 'import /etc/caddy/elpida.caddy' "$CF"; then
    # 已有别的站点：只在末尾加一行引入，不动原有内容
    printf '\nimport /etc/caddy/elpida.caddy  %s\n' "$MARK" | sudo tee -a "$CF" >/dev/null
  fi
  if sudo caddy validate --config "$CF" --adapter caddyfile >/dev/null 2>&1; then
    sudo systemctl enable -q caddy
    sudo systemctl reload caddy 2>/dev/null || sudo systemctl restart caddy
    echo "  Caddy 已配置：$URL"
  else
    echo "  Caddy 配置检查不通过，已恢复原配置，跳过 HTTPS："
    sudo caddy validate --config "$CF" --adapter caddyfile || true
    [ -f "$BAK" ] && sudo cp -p "$BAK" "$CF"
    URL=""
  fi
fi

echo "==> 5/5 检查"
sleep 3
for s in elpida-agents elpida-web elpida-pull.timer elpida-restart.path; do
  printf '  %-22s %s\n' "$s" "$(systemctl is-active "$s" || true)"
done
if curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" | grep -q 401; then
  echo "  网站本机访问正常"
else
  echo "  网站本机访问异常，看日志：sudo journalctl -u elpida-web -n 30 --no-pager"
fi
KEY="$(cat "$DATA/web_token")"
if [ -n "$URL" ]; then
  LINK="$URL/?key=$KEY"
else
  LINK="（HTTPS 没配好）先用 SSH 隧道：ssh -L $PORT:127.0.0.1:$PORT 本服务器，再打开 http://127.0.0.1:$PORT/?key=$KEY"
fi
( umask 077; echo "$LINK" > "$DATA/owner_link" )

cat <<EOF

================================================================
  Elpida 装好了。
  主人链接（带访问钥匙，别发给别人；以后忘了：cat ~/elpida-data/owner_link）：
    $LINK

  - 总管一启动就会先跑一次行情数据（第一次下载全部历史合约，几分钟），然后算期权。
  - 第一次打开 HTTPS 链接可能要等半分钟，Caddy 在申请证书。
  - 打不开的话：到云服务商控制台的防火墙，放行 TCP 80 和 ${HTTPS_PORT:-443} 端口。
  - 看日志：sudo journalctl -u elpida-agents -n 50 --no-pager
================================================================
EOF
