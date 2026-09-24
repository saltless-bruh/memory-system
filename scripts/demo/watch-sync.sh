#!/usr/bin/env bash
# Make the push -> index chain VISIBLE on screen while the lead pushes.
#
#   ./scripts/demo/watch-sync.sh
#
# Leave this running in its own window. Every push prints one block:
#
#   14:32:01  webhook nhan duoc        <- Gitea goi host-sync
#   14:32:01  snapshot moi  0fd47b2    <- vault-replica da doi
#   14:32:04  da index  39 moi / 392 giu nguyen / 0 xoa   (3.1s)
#
# Nothing here queries the vault; it only reads container logs.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

echo "Dang theo doi duong dong bo. Push mot lan de xem."
echo "Ctrl-C de dung."
echo
docker compose logs -f --tail 0 host-sync sync-job 2>&1 | while IFS= read -r line; do
  ts=$(date +%H:%M:%S)
  case "$line" in
    *"hooks/wiki-update"*202*)
      printf '  %s  \033[36mwebhook nhận được\033[0m        Gitea → host-sync\n' "$ts" ;;
    *"Published wiki snapshot at commit"*)
      c=$(printf '%s' "$line" | grep -oE '[0-9a-f]{40}' | head -1)
      printf '  %s  \033[36msnapshot mới\033[0m  %.7s      vault-replica đã đổi\n' "$ts" "$c" ;;
    *'"stage": "watcher_wake"'*)
      printf '  %s  sync-job thức dậy\n' "$ts" ;;
    *'"stage": "cycle_complete"'*)
      i=$(printf '%s' "$line" | grep -oE '"indexed_count": [0-9]+'   | grep -oE '[0-9]+')
      u=$(printf '%s' "$line" | grep -oE '"unchanged_count": [0-9]+' | grep -oE '[0-9]+')
      d=$(printf '%s' "$line" | grep -oE '"deleted_count": [0-9]+'   | grep -oE '[0-9]+')
      ms=$(printf '%s' "$line" | grep -oE '"elapsed_ms": [0-9.]+'    | grep -oE '[0-9.]+')
      s=$(awk -v m="${ms:-0}" 'BEGIN{printf "%.1f", m/1000}')
      printf '  %s  \033[32mđã index\033[0m  %s mới / %s giữ nguyên / %s xoá   (%ss)\n\n' \
             "$ts" "${i:-?}" "${u:-?}" "${d:-0}" "$s" ;;
  esac
done
