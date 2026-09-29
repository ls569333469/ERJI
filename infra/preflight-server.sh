#!/usr/bin/env bash
# Read-only inventory. Does not display environment files, keys or nginx config bodies.
set -eu
printf 'Host: '; hostname
uname -m
printf '\nResources\n'
getconf _NPROCESSORS_ONLN
free -m
df -h /opt
printf '\nListening ports\n'
ss -lntp
printf '\nKnown service states\n'
systemctl is-active nginx xbot telegram-forwarder || true
printf '\nRunning processes by memory\n'
ps -eo pid,comm,%cpu,%mem,rss --sort=-rss | head -n 16
printf '\nExisting routes and public health\n'
for url in https://xiexiu.io/xbot/ https://xiexiu.io/xbot/api/health https://xiexiu.io/tg/; do
 curl -sS --max-time 15 -o /dev/null -w "$url %{http_code} %{time_total}s\n" "$url" || true
done
if command -v docker >/dev/null; then
 docker compose version
 docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
 docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
 docker network ls
fi
printf '\nExisting ERJI deployment paths\n'
for p in /opt/erji-current /opt/erji-shared; do [ ! -e "$p" ] || ls -ld "$p"; done
# Actual vhost selection and backend health checks require operator review after this snapshot.
