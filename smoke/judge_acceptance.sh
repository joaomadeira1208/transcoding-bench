#!/usr/bin/env bash
#
# O harness do Juiz na camada de aceite (ADR-0022), que roda dentro da imagem.

set -euo pipefail

EXIT_USAGE=2

CLIP_CODEC=ffv1
CLIP_PIX_FMT=yuv420p

usage_error() {
  printf 'judge_acceptance.sh: %s\n' "$*" >&2
  printf 'uso: judge_acceptance.sh --out-dir <dir> --clip <path> --clip-size <WxH> --clip-seconds <n> --clip-fps <n> -- <julgamento>\n' >&2
  exit "$EXIT_USAGE"
}

out_dir=""
clip=""
clip_size=""
clip_seconds=""
clip_fps=""

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
    *) usage_error "argumento desconhecido: $1" ;;
  esac
  shift 2
done

for name in out_dir clip clip_size clip_seconds clip_fps; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done
(($#)) || usage_error "faltou o julgamento depois do --"

mkdir -p "$out_dir" "$(dirname "$clip")"

ffmpeg -nostdin -y -hide_banner -loglevel error \
  -f lavfi -i "testsrc2=size=$clip_size:rate=$clip_fps" \
  -t "$clip_seconds" -pix_fmt "$CLIP_PIX_FMT" -c:v "$CLIP_CODEC" "$clip"

cp "$VERSIONS_FILE" "$out_dir/versions.json"

"$@" >/dev/null 2>"$out_dir/ffmpeg.log"
