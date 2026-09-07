#!/usr/bin/env bash
#
# O harness da camada de aceite (ADR-0022), que roda dentro da imagem.

set -euo pipefail

EXIT_USAGE=2

CLIP_CODEC=ffv1
CLIP_PIX_FMT=yuv420p

PID_RESOLVE_ATTEMPTS=60
PID_RESOLVE_INTERVAL=0.05

usage_error() {
  printf 'acceptance.sh: %s\n' "$*" >&2
  printf 'uso: acceptance.sh --out-dir <dir> --clip <path> --clip-size <WxH> --clip-seconds <n> --clip-fps <n> --pidstat-flags <flags> --pidstat-interval <n> --muxer <name> -- <cadeia de instrumentação>\n' >&2
  exit "$EXIT_USAGE"
}

deepest_descendant() {
  local pid=$1 child
  while child=$(pgrep -o -P "$pid" 2>/dev/null) && [[ -n $child ]]; do
    pid=$child
  done
  printf '%s\n' "$pid"
}

# Apontar o `pidstat` para um dos wrappers daria uma série de processo ocioso; a
# cadeia tem um filho por nível e o FFmpeg é a folha, então descer basta.
resolve_encoder_pid() {
  local attempt pid
  for ((attempt = 0; attempt < PID_RESOLVE_ATTEMPTS; attempt++)); do
    pid=$(deepest_descendant "$1")
    if [[ $pid != "$1" && $(ps -o comm= -p "$pid" 2>/dev/null) == *ffmpeg* ]]; then
      printf '%s\n' "$pid"
      return 0
    fi
    sleep "$PID_RESOLVE_INTERVAL"
  done
  return 1
}

# Sem o SIGINT antes do SIGTERM o `pidstat` morre sem esvaziar o buffer, e a
# captura sai vazia.
stop_pidstat() {
  [[ -n ${pidstat_pid:-} ]] || return 0
  kill -INT "$pidstat_pid" 2>/dev/null || true
  sleep 0.2
  kill -TERM "$pidstat_pid" 2>/dev/null || true
  wait "$pidstat_pid" 2>/dev/null || true
  pidstat_pid=""
}

out_dir=""
clip=""
clip_size=""
clip_seconds=""
clip_fps=""
pidstat_flags=""
pidstat_interval=""
muxer=""

while (($#)); do
  if [[ $1 == -- ]]; then
    shift
    break
  fi
  [[ $# -ge 2 ]] || usage_error "$1 exige um valor"
  case $1 in
    --out-dir) out_dir=$2 ;;
    --clip) clip=$2 ;;
    --clip-size) clip_size=$2 ;;
    --clip-seconds) clip_seconds=$2 ;;
    --clip-fps) clip_fps=$2 ;;
    --pidstat-flags) pidstat_flags=$2 ;;
    --pidstat-interval) pidstat_interval=$2 ;;
    --muxer) muxer=$2 ;;
    *) usage_error "argumento desconhecido: $1" ;;
  esac
  shift 2
done

for name in out_dir clip clip_size clip_seconds clip_fps pidstat_flags pidstat_interval muxer; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done
(($#)) || usage_error "faltou a cadeia de instrumentação depois do --"

mkdir -p "$out_dir" "$(dirname "$clip")"

ffmpeg -nostdin -y -hide_banner -loglevel error \
  -f lavfi -i "testsrc2=size=$clip_size:rate=$clip_fps" \
  -t "$clip_seconds" -pix_fmt "$CLIP_PIX_FMT" -c:v "$CLIP_CODEC" "$clip"

"$@" >/dev/null 2>"$out_dir/ffmpeg.log" &
chain_pid=$!

if encoder_pid=$(resolve_encoder_pid "$chain_pid"); then
  set -m
  # shellcheck disable=SC2086 # as flags chegam juntas e são para palavra-partir
  pidstat $pidstat_flags -p "$encoder_pid" "$pidstat_interval" >"$out_dir/pidstat.txt" 2>&1 &
  pidstat_pid=$!
  set +m
else
  printf 'acceptance.sh: o PID do FFmpeg não foi resolvido dentro da janela\n' >&2
fi

encode_status=0
wait "$chain_pid" || encode_status=$?
stop_pidstat

for output in "$@"; do :; done

if ((encode_status == 0)); then
  ffmpeg -nostdin -hide_banner -loglevel error \
    -i "$output" -c copy -f "$muxer" - | sha256sum | cut -d' ' -f1 >"$out_dir/output.sha256"
fi

exit "$encode_status"
