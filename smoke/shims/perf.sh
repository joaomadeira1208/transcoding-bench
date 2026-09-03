#!/usr/bin/env bash
#
# Shim do `perf`. Escreve o `perf.json` mesmo quando o comando medido falha, como
# o `perf` de verdade — é isso que faz "encode falhou" e "instrumentação falhou"
# serem dois casos distinguíveis.

set -euo pipefail

{
  printf '%s\0' "$@"
  printf '\n'
} >>"$SMOKE_ARGV_DIR/perf.argv"
printf 'perf\n' >>"$SMOKE_ARGV_DIR/sequence"

shift # o subcomando `stat`

output=""
events=""
while (($#)); do
  case $1 in
    -o)
      output=$2
      shift 2
      ;;
    -e)
      events=$2
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *) shift ;;
  esac
done

if [[ ${SMOKE_PERF_EXIT:-0} != 0 ]]; then
  printf 'smoke perf: falha induzida\n' >&2
  exit "${SMOKE_PERF_EXIT}"
fi

status=0
"$@" || status=$?

: >"$output"
IFS=, read -r -a requested <<<"$events"
for event in "${requested[@]}"; do
  # Omitir é diferente de reportar `<not supported>`: o contador não aparece, e é
  # esse o caso que um casamento por substring deixaria passar.
  if [[ $event == "${SMOKE_PERF_OMIT:-}" ]]; then
    continue
  fi
  if [[ $event == "${SMOKE_PERF_UNSUPPORTED:-}" ]]; then
    value='"<not supported>"'
  else
    value='"1234567.000000"'
  fi
  printf '{"counter-value" : %s, "unit" : "", "event" : "%s", "event-runtime" : 1000000, "pcnt-running" : 100.00}\n' \
    "$value" "$event" >>"$output"
done

exit "$status"
