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
# As chaves do `-e` são a sintaxe de grupo do `perf`; o JSON de saída traz cada
# membro do grupo como uma linha própria, com o nome do evento sem elas.
IFS=, read -r -a requested <<<"${events//[\{\}]/}"
for event in "${requested[@]}"; do
  # Omitir é diferente de reportar `<not supported>`: o contador não aparece, e é
  # esse o caso que um casamento por substring deixaria passar.
  if [[ $event == "${SMOKE_PERF_OMIT:-}" ]]; then
    continue
  fi
  # `SMOKE_PERF_VALUES=evento=valor,evento=valor`: o valor entra literal, o que
  # cobre num mecanismo só o `<not supported>`, o `<not counted>`, o zero mudo e
  # o par que responde e não mede.
  value="1234567.000000"
  for override in ${SMOKE_PERF_VALUES:+${SMOKE_PERF_VALUES//,/ }}; do
    if [[ $event == "${override%%=*}" ]]; then
      value=${override#*=}
    fi
  done
  printf '{"counter-value" : "%s", "unit" : "", "event" : "%s", "event-runtime" : 1000000, "pcnt-running" : %s}\n' \
    "$value" "$event" "${SMOKE_PERF_PCNT:-100.00}" >>"$output"
done

exit "$status"
