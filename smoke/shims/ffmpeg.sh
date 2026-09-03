#!/usr/bin/env bash
#
# Shim do `ffmpeg`.

set -euo pipefail

{
  printf '%s\0' "$@"
  printf '\n'
} >>"$SMOKE_ARGV_DIR/ffmpeg.argv"
printf 'ffmpeg\n' >>"$SMOKE_ARGV_DIR/sequence"

# O último argumento discrimina as duas invocações do `run_scenario.sh`: `-` é a
# extração do bitstream, qualquer outra coisa é o output do encode.
for last in "$@"; do :; done

if [[ $last == - ]]; then
  printf '%s' "${SMOKE_BITSTREAM:-bitstream}"
  exit 0
fi

printf '%s\n' "$last" >>"$SMOKE_ARGV_DIR/ffmpeg.encodes"
nth=$(wc -l <"$SMOKE_ARGV_DIR/ffmpeg.encodes" | tr -d ' ')

# Sem `SMOKE_FFMPEG_NTH` o comportamento induzido vale para todo encode; com ela,
# só para o N-ésimo — é o que deixa um run falhar no meio de um bloco cujos
# vizinhos seguem bem.
if [[ -z ${SMOKE_FFMPEG_NTH:-} || $SMOKE_FFMPEG_NTH == "$nth" ]]; then
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
