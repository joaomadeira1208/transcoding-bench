#!/usr/bin/env bash
#
# Shim do `pidstat`.

set -euo pipefail

{
  printf '%s\0' "$@"
  printf '\n'
} >>"$SMOKE_ARGV_DIR/pidstat.argv"

pid=""
while (($#)); do
  case $1 in
    -p)
      pid=$2
      shift 2
      ;;
    *) shift ;;
  esac
done

printf '%s\n' '# Time UID PID %usr %system %guest %wait %CPU CPU minflt/s majflt/s VSZ RSS %MEM Command'
while :; do
  printf '1767225600 0 %s 92.00 6.00 0.00 0.00 98.00 0 120.00 0.00 512000 262144 1.50 ffmpeg\n' "$pid"
  sleep 1
done
