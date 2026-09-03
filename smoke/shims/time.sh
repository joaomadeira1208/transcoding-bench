#!/usr/bin/env bash
#
# Shim do `/usr/bin/time` do GNU, alcançado pelo `TIME_BIN` do `run_scenario.sh`.

set -euo pipefail

{
  printf '%s\0' "$@"
  printf '\n'
} >>"$SMOKE_ARGV_DIR/time.argv"
printf 'time\n' >>"$SMOKE_ARGV_DIR/sequence"

format=""
output=""
while (($#)); do
  case $1 in
    --quiet | -q) shift ;;
    -f | --format)
      format=$2
      shift 2
      ;;
    -o | --output)
      output=$2
      shift 2
      ;;
    *) break ;;
  esac
done

status=0
"$@" || status=$?

# Trocar cada especificador por um número faz este shim **verificar** o formato em
# vez de só aceitá-lo: um `TIME_FORMAT` com uma aspa fora de lugar deixa de ser
# JSON aqui, e o run falha em vez de gravar um `time.json` ilegível.
printf '%s\n' "$format" | sed 's/%[A-Za-z]/0/g' >"$output"

exit "$status"
