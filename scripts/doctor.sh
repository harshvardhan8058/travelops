#!/usr/bin/env bash
# Pre-flight check. Run this on the demo machine before an evaluation.
# Exits non-zero if anything required is missing.

set -uo pipefail

pass=0; fail=0; warn=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
note() { printf '  \033[33m!\033[0m %s\n' "$1"; warn=$((warn+1)); }

echo ""
echo "TravelOps AI — environment check"
echo ""

echo "Required:"
command -v docker >/dev/null 2>&1 && ok "docker $(docker --version 2>/dev/null | awk '{print $3}' | tr -d ,)" || bad "docker not found"
docker compose version >/dev/null 2>&1 && ok "docker compose available" || bad "docker compose plugin missing"
if command -v docker >/dev/null 2>&1; then
  docker info >/dev/null 2>&1 && ok "docker daemon responding" || bad "docker daemon not running"
fi
command -v git >/dev/null 2>&1 && ok "git $(git --version | awk '{print $3}')" || bad "git not found"

echo ""
echo "Local development (optional if using Docker only):"
command -v uv  >/dev/null 2>&1 && ok "uv $(uv --version | awk '{print $2}')"   || note "uv not found — backend runs in Docker regardless"
command -v node>/dev/null 2>&1 && ok "node $(node --version)"                   || note "node not found — frontend runs in Docker regardless"

echo ""
echo "Project files:"
for f in .env.example docker-compose.yml Makefile config/assurance.v1.yaml; do
  [ -f "$f" ] && ok "$f" || bad "$f missing"
done
[ -f .env ] && ok ".env present" || note ".env missing — run 'make env'"
[ -d policy_packs/in-moca-charter-2019 ] && ok "charter policy pack present" || bad "policy pack missing"
[ -d frontend/src/design ] && ok "frontend design tokens present" || bad "frontend tokens missing"

echo ""
echo "Disk and memory:"
avail_kb=$(df -Pk . | awk 'NR==2{print $4}')
if [ "${avail_kb:-0}" -gt 10485760 ]; then ok "disk free $((avail_kb/1048576))GB (need 10GB)"; else note "disk free $((avail_kb/1048576))GB — 10GB recommended"; fi
if [ -r /proc/meminfo ]; then
  mem_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)
  if [ "${mem_kb:-0}" -gt 8000000 ]; then ok "memory $((mem_kb/1048576))GB (need 8GB)"; else note "memory $((mem_kb/1048576))GB — 8GB recommended"; fi
fi

echo ""
printf 'passed %d   warnings %d   failed %d\n' "$pass" "$warn" "$fail"
echo ""
if [ "$fail" -gt 0 ]; then
  echo "Fix the failures above before relying on this machine for a demo."
  exit 1
fi
echo "Ready. Next: make up && make migrate && make seed"
