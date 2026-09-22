#!/usr/bin/env bash
#
# Shim do `ffmpeg`.

set -euo pipefail

{
  printf '%s\0' "$@"
  printf '\n'
} >>"$SMOKE_ARGV_DIR/ffmpeg.argv"
printf 'ffmpeg\n' >>"$SMOKE_ARGV_DIR/sequence"

DEFAULT_BITSTREAM=bitstream

DEFAULT_VMAF=95

# A forma do log do `libvmaf` v3: a série por frame em `frames[].metrics` e o
# resumo em `pooled_metrics`, que é o que a ADR-0005 manda persistir.
VMAF_LOG_VERSION=3.1.0
VMAF_LOG_FRAMES=3

# Contar encodes, e não extrações: a extração do bitstream de um run que falhou
# não acontece, e o índice dos dois `_NTH` passaria a ser outro. O julgamento do
# Juiz tem tabela própria porque o laço dele é outro.
counted() {
  wc -l <"$SMOKE_ARGV_DIR/$1" | tr -d ' '
}

tallied() {
  printf '%s\n' "$2" >>"$SMOKE_ARGV_DIR/$1"
  counted "$1"
}

nth_matches() {
  [[ -z $1 || $1 == "$2" ]]
}

write_vmaf_log() {
  jq -n \
    --arg version "$VMAF_LOG_VERSION" \
    --argjson frames "$VMAF_LOG_FRAMES" \
    --argjson vmaf "${SMOKE_VMAF:-$DEFAULT_VMAF}" \
    '($vmaf / 100) as $ssim
      | {
          version: $version,
          frames: [
            range($frames)
            | {frameNum: ., metrics: {vmaf: $vmaf, float_ssim: $ssim}}
          ],
          pooled_metrics: {
            vmaf: {min: $vmaf, max: $vmaf, mean: $vmaf, harmonic_mean: $vmaf},
            float_ssim: {min: $ssim, max: $ssim, mean: $ssim, harmonic_mean: $ssim}
          },
          aggregate_metrics: {}
        }' >"$1"
}

# A invocação do Juiz não se discrimina pelo último argumento: o `-f null -` do
# `run_quality.sh` termina no mesmo `-` da extração do bitstream.
log_path=""
for argument in "$@"; do
  [[ $argument == *libvmaf=* && $argument =~ log_path=([^:]+) ]] || continue
  log_path=${BASH_REMATCH[1]}
done

if [[ -n $log_path ]]; then
  if nth_matches "${SMOKE_FFMPEG_NTH:-}" "$(tallied ffmpeg.judgements "$log_path")"; then
    induced=1
  else
    induced=""
  fi

  printf 'frame=    3 fps=1.2 q=-0.0 Lsize=N/A time=00:00:00.12 bitrate=N/A speed=0.5x\n' >&2

  # `exec`, e não `sleep` em subprocesso: o watchdog manda SIGTERM ao FFmpeg, e um
  # shim que só fosse pai do `sleep` deixaria o travamento vivo depois de morto.
  if [[ -n $induced && -n ${SMOKE_FFMPEG_HANG:-} ]]; then
    exec sleep "$SMOKE_FFMPEG_HANG"
  fi

  # Sem log quando o FFmpeg falha: um `libvmaf` que não terminou não deixa log
  # completo, e escrever um aqui faria o julgamento falho parecer legível.
  if [[ -n $induced && ${SMOKE_FFMPEG_EXIT:-0} != 0 ]]; then
    exit "$SMOKE_FFMPEG_EXIT"
  fi

  write_vmaf_log "$log_path"
  exit 0
fi

# O último argumento discrimina as duas invocações do `run_scenario.sh`: `-` é a
# extração do bitstream, qualquer outra coisa é o output do encode.
for last in "$@"; do :; done

if [[ $last == - ]]; then
  if nth_matches "${SMOKE_BITSTREAM_NTH:-}" "$(counted ffmpeg.encodes)"; then
    printf '%s' "${SMOKE_BITSTREAM:-$DEFAULT_BITSTREAM}"
  else
    printf '%s' "$DEFAULT_BITSTREAM"
  fi
  exit 0
fi

if nth_matches "${SMOKE_FFMPEG_NTH:-}" "$(tallied ffmpeg.encodes "$last")"; then
  induced=1
else
  induced=""
fi

# Troca o próprio processo por um que não se parece com o FFmpeg: é o que torna a
# não-resolução do PID exercitável sem depender de corrida.
if [[ -n $induced && -n ${SMOKE_ENCODER_INVISIBLE:-} ]]; then
  exec sleep 1
fi

printf '%s\n' "$$" >"$SMOKE_ARGV_DIR/ffmpeg.pid"

printf '%s\n' "$last" >"$last"
printf 'frame=  120 fps= 24 q=28.0 Lsize=    1234KiB time=00:00:05.00 bitrate=2021.4kbits/s speed=1.02x\n' >&2

if [[ -n $induced && -n ${SMOKE_FFMPEG_HANG:-} ]]; then
  sleep "$SMOKE_FFMPEG_HANG"
fi

# O encode tem de durar o suficiente para o laço de resolução de PID alcançá-lo,
# ou o teste passa a exercitar o caminho de falha.
sleep 0.5

if [[ -n $induced ]]; then
  exit "${SMOKE_FFMPEG_EXIT:-0}"
fi
exit 0
