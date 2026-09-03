#!/usr/bin/env bash

set -euo pipefail

RUN_SCENARIO=$(dirname "${BASH_SOURCE[0]}")/run_scenario.sh

AWS_COMMAND=aws

# Os limites operacionais da ADR-0012: 4 h por Execução, 72 h por Instância. Não
# são desenho experimental, e por isso não viajam no plano — são flags.
RUN_TIMEOUT_SECONDS=$((4 * 60 * 60))
TOTAL_TIMEOUT_SECONDS=$((72 * 60 * 60))

EXIT_USAGE=2

usage_error() {
  printf 'run_all.sh: %s\n' "$*" >&2
  printf 'uso: run_all.sh --plan <slice.json> --masters-dir <dir> --runs-dir <dir> --bucket <name> --commit <sha> --instance-id <id> --instance-type <type> [--versions-file <path>] [--run-timeout <seconds>] [--total-timeout <seconds>]\n' >&2
  exit "$EXIT_USAGE"
}

log() {
  printf 'run_all.sh: %s\n' "$*" >&2
}

start_watchdog() {
  (
    trap 'kill "$sleeper" 2>/dev/null; exit 0' TERM
    sleep "$RUN_TIMEOUT_SECONDS" &
    sleeper=$!
    wait "$sleeper" || true
    kill -TERM "$1" 2>/dev/null || true
  ) &
  watchdog_pid=$!
}

stop_watchdog() {
  [[ -n ${watchdog_pid:-} ]] || return 0
  kill -TERM "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  watchdog_pid=""
}

# shellcheck disable=SC2329 # invocada pelos `trap TERM` e `trap INT`
abort() {
  trap - TERM INT
  stop_watchdog
  if [[ -n ${run_pid:-} ]]; then
    kill -TERM "$run_pid" 2>/dev/null || true
    wait "$run_pid" 2>/dev/null || true
  fi
  exit "$1"
}

# Deixa em `run_status` em vez de devolver: chamada sob `||`, a função perderia
# o `set -e` por dentro, e o `jq` falhando viraria um run sem `scenario_id`.
execute_run() {
  local run=$1 scenario_id started elapsed
  scenario_id=$(jq -r '.scenario_id' <<<"$run")
  started=$SECONDS

  bash "$RUN_SCENARIO" \
    --run "$run" \
    --masters-dir "$masters_dir" \
    --runs-dir "$runs_dir" \
    --bucket "$bucket" \
    --commit "$commit" \
    --instance-id "$instance_id" \
    --instance-type "$instance_type" \
    --versions-file "$versions_file" </dev/null &
  run_pid=$!
  start_watchdog "$run_pid"

  run_status=0
  wait "$run_pid" || run_status=$?
  run_pid=""
  stop_watchdog

  elapsed=$((SECONDS - started))
  if ((elapsed >= RUN_TIMEOUT_SECONDS)); then
    log "$scenario_id: excedeu o timeout de ${RUN_TIMEOUT_SECONDS}s (status $run_status)"
  else
    log "$scenario_id: status $run_status em ${elapsed}s"
  fi
}

write_done_marker() {
  local marker
  marker=$(mktemp)
  date -Iseconds >"$marker"
  "$AWS_COMMAND" s3 cp "$marker" "s3://$bucket/status/${instance_type}_done"
  rm -f "$marker"
}

plan=""
masters_dir=""
runs_dir=""
bucket=""
commit=""
instance_id=""
instance_type=""
versions_file=${VERSIONS_FILE:-}

while (($#)); do
  flag=$1
  [[ $# -ge 2 ]] || usage_error "$flag exige um valor"
  value=$2
  case $flag in
    --plan) plan=$value ;;
    --masters-dir) masters_dir=$value ;;
    --runs-dir) runs_dir=$value ;;
    --bucket) bucket=$value ;;
    --commit) commit=$value ;;
    --instance-id) instance_id=$value ;;
    --instance-type) instance_type=$value ;;
    --versions-file) versions_file=$value ;;
    --run-timeout) RUN_TIMEOUT_SECONDS=$value ;;
    --total-timeout) TOTAL_TIMEOUT_SECONDS=$value ;;
    *) usage_error "argumento desconhecido: $flag" ;;
  esac
  shift 2
done

for name in plan masters_dir runs_dir bucket commit instance_id instance_type versions_file; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done
for name in RUN_TIMEOUT_SECONDS TOTAL_TIMEOUT_SECONDS; do
  [[ ${!name} =~ ^[0-9]+$ ]] || usage_error "timeout em segundos inteiros: ${!name}"
done

[[ -r $plan ]] || usage_error "plano ilegível: $plan"
jq -e '.blocks | type == "array"' "$plan" >/dev/null 2>&1 ||
  usage_error "o plano não tem a lista .blocks: $plan"

trap 'abort 143' TERM
trap 'abort 130' INT

block_count=$(jq '.blocks | length' "$plan")
runs_total=0
runs_failed=0
capped=""

for ((i = 0; i < block_count; i++)); do
  if ((SECONDS >= TOTAL_TIMEOUT_SECONDS)); then
    log "teto de ${TOTAL_TIMEOUT_SECONDS}s atingido antes do bloco $i: parando"
    capped=1
    break
  fi
  run_count=$(jq ".blocks[$i].runs | length" "$plan")
  for ((j = 0; j < run_count; j++)); do
    run=$(jq -c ".blocks[$i].runs[$j]" "$plan")
    runs_total=$((runs_total + 1))
    execute_run "$run"
    if ((run_status != 0)); then
      runs_failed=$((runs_failed + 1))
    fi
  done
done

write_done_marker
log "$runs_total runs, $runs_failed com falha, ${SECONDS}s"

if ((runs_failed > 0)) || [[ -n $capped ]]; then
  exit 1
fi
