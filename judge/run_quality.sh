#!/usr/bin/env bash
#
# O bash copia, nunca deriva (ADR-0019): a geometria do `scale=`, o `flags=` dele
# e o modelo do VMAF chegam pelo plano.

set -euo pipefail

SCHEMA_VERSION=1

FFMPEG_COMMAND=ffmpeg
AWS_COMMAND=aws

# Operacionais, e não do plano. O teto não é o das 120 h da ADR-0012: aquele foi
# calibrado para uma campanha de quatro dias, e o Pass inteiro é de horas
# (ADR-0025) — 120 h aqui seriam cinco dias de Juiz faturando sem guarda.
OUTPUT_TIMEOUT_SECONDS=$((4 * 60 * 60))
TOTAL_TIMEOUT_SECONDS=$((24 * 60 * 60))

RESULTS_PREFIX=quality/results
PROGRESS_KEY=status/judge_progress
DONE_KEY=status/judge_done

RESULTS_DIR_NAME=results

VMAF_LOG_FILENAME=vmaf.json
JUDGEMENT_FILENAME=judge.json
FFMPEG_LOG_FILENAME=ffmpeg.log

EXIT_USAGE=2
EXIT_DOWNLOAD=70
EXIT_VMAF_LOG=71
EXIT_UPLOAD=72

usage_error() {
  printf 'run_quality.sh: %s\n' "$*" >&2
  printf 'uso: run_quality.sh --plan <plan.json> --masters-dir <dir> --work-dir <dir> --bucket <name> --commit <sha> --instance-id <id> --instance-type <type> [--threads <n>] [--versions-file <path>] [--output-timeout <seconds>] [--total-timeout <seconds>]\n' >&2
  exit "$EXIT_USAGE"
}

log() {
  printf 'run_quality.sh: %s\n' "$*" >&2
}

start_watchdog() {
  (
    trap 'kill "$sleeper" 2>/dev/null; exit 0' TERM
    sleep "$OUTPUT_TIMEOUT_SECONDS" &
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
  if [[ -n ${ffmpeg_pid:-} ]]; then
    kill -TERM "$ffmpeg_pid" 2>/dev/null || true
    wait "$ffmpeg_pid" 2>/dev/null || true
  fi
  exit "$1"
}

output_field() {
  jq -r --arg name "$1" \
    'if has($name) then .[$name] else error("campo ausente no output do plano: \($name)") end' \
    <<<"$output"
}

# O Master escalado pelo **mesmo** `scale=W:H:flags=` que produziu o output, e no
# mesmo filtergraph do `libvmaf`: medir o encoder, e não a cadeia de downscale
# (ADR-0005). O output é a primeira entrada e a referência a segunda, que é a
# ordem em que o filtro lê distorcido e referência.
filtergraph() {
  printf '[1:v]scale=%s:%s:flags=%s[ref];[0:v][ref]libvmaf=model=version=%s:feature=name=float_ssim:n_threads=%s:log_fmt=json:log_path=%s' \
    "$output_width" "$output_height" "$scale_flags" \
    "$vmaf_model" "$threads" "$vmaf_log"
}

# Deixa em `ffmpeg_status` em vez de devolver: chamada sob `if`, a função perderia
# o `set -e` por dentro.
measure_quality() {
  "$FFMPEG_COMMAND" \
    -nostdin \
    -y \
    -i "$local_output" \
    -i "$masters_dir/$master" \
    -filter_complex "$(filtergraph)" \
    -f null - >/dev/null 2>"$ffmpeg_log" &
  ffmpeg_pid=$!
  start_watchdog "$ffmpeg_pid"

  ffmpeg_status=0
  wait "$ffmpeg_pid" || ffmpeg_status=$?
  ffmpeg_pid=""
  stop_watchdog
}

# Projeção, e não campos remontados um a um: `jq` copia cada valor com o tipo que
# ele já tinha, e é isso que mantém `cell_divergent` booleano e `frames` número.
write_judgement() {
  jq -n \
    --arg schema_version "$SCHEMA_VERSION" \
    --argjson output "$output" \
    --arg started_at "$started_at" \
    --arg finished_at "$finished_at" \
    --argjson exit_code "$exit_code" \
    --arg commit "$commit" \
    --arg instance_id "$instance_id" \
    --arg instance_type "$instance_type" \
    --slurpfile versions "$versions_file" \
    '{schema_version: $schema_version}
      + ($output | {
          run_id, scenario_id, codec, encoder, input_res, output_res, video,
          instance, sha256, master, output_width, output_height, scale_flags,
          container, frames, shared_by, cell_divergent
        })
      + {
          started_at: $started_at,
          finished_at: $finished_at,
          exit_code: $exit_code,
          commit: $commit,
          instance_id: $instance_id,
          instance_type: $instance_type,
          versions: $versions[0]
        }' \
    >"$judgement_json"
}

# Deixa em `exit_code`, `run_id` e `scenario_id` em vez de devolver: o objeto de
# progresso escrito logo depois nomeia o output que acabou.
judge_output() {
  local index=$1 started elapsed key

  output=$(jq -c --argjson index "$index" '.outputs[$index]' "$plan")
  run_id=$(output_field run_id)
  scenario_id=$(output_field scenario_id)
  container=$(output_field container)
  master=$(output_field master)
  output_width=$(output_field output_width)
  output_height=$(output_field output_height)
  scale_flags=$(output_field scale_flags)

  result_dir=$results_dir/$run_id
  mkdir -p "$result_dir"
  vmaf_log=$result_dir/$VMAF_LOG_FILENAME
  ffmpeg_log=$result_dir/$FFMPEG_LOG_FILENAME
  judgement_json=$result_dir/$JUDGEMENT_FILENAME

  local_output=$work_dir/$run_id.$container
  key=runs/$run_id/output.$container

  started_at=$(date -Iseconds)
  exit_code=0

  if ! "$AWS_COMMAND" s3 cp "s3://$bucket/$key" "$local_output"; then
    log "$run_id: o download de s3://$bucket/$key falhou"
    exit_code=$EXIT_DOWNLOAD
  else
    started=$SECONDS
    measure_quality
    elapsed=$((SECONDS - started))
    if ((elapsed >= OUTPUT_TIMEOUT_SECONDS)); then
      log "$run_id: excedeu o timeout de ${OUTPUT_TIMEOUT_SECONDS}s (status $ffmpeg_status)"
    else
      log "$run_id: status $ffmpeg_status em ${elapsed}s"
    fi

    if ((ffmpeg_status != 0)); then
      exit_code=$ffmpeg_status
    elif ! jq -e '.frames | type == "array" and length > 0' "$vmaf_log" >/dev/null 2>&1; then
      log "$run_id: o libvmaf não deixou log legível em $vmaf_log"
      exit_code=$EXIT_VMAF_LOG
    fi
  fi

  finished_at=$(date -Iseconds)
  write_judgement

  if ! "$AWS_COMMAND" s3 cp "$result_dir/" "s3://$bucket/$RESULTS_PREFIX/$run_id/" --recursive; then
    log "$run_id: o upload de $result_dir falhou"
    if ((exit_code == 0)); then
      exit_code=$EXIT_UPLOAD
    fi
  fi

  # Fora do `if` do download: o Pass da campanha só cabe em 100 GB se o `.mkv`
  # sair do disco em todo caminho, e não apenas no do output julgado com sucesso.
  rm -f "$local_output"
}

write_progress() {
  local payload
  payload=$(mktemp)
  jq -n \
    --arg instance_id "$instance_id" \
    --argjson output_index "$1" \
    --argjson output_count "$output_count" \
    --arg run_id "$run_id" \
    --arg scenario_id "$scenario_id" \
    --argjson runs_total "$runs_total" \
    --argjson runs_failed "$runs_failed" \
    --argjson elapsed_seconds "$SECONDS" \
    --arg written_at "$(date -Iseconds)" \
    '{
      instance_id: $instance_id,
      output_index: $output_index,
      output_count: $output_count,
      run_id: $run_id,
      scenario_id: $scenario_id,
      runs_total: $runs_total,
      runs_failed: $runs_failed,
      elapsed_seconds: $elapsed_seconds,
      written_at: $written_at
    }' >"$payload"
  # Telemetria não derruba o Pass: sem o `if`, um `s3 cp` de poucos bytes que
  # falhe aqui leva o `set -e` e o plano inteiro com ele.
  if ! "$AWS_COMMAND" s3 cp "$payload" "s3://$bucket/$PROGRESS_KEY"; then
    log "$run_id: o progresso não subiu"
  fi
  rm -f "$payload"
}

write_done_marker() {
  local marker
  marker=$(mktemp)
  jq -n \
    --arg instance_id "$instance_id" \
    --arg finished_at "$(date -Iseconds)" \
    --argjson runs_total "$runs_total" \
    --argjson runs_failed "$runs_failed" \
    --argjson capped "$capped" \
    --argjson exit_status "$exit_status" \
    '{
      instance_id: $instance_id,
      finished_at: $finished_at,
      runs_total: $runs_total,
      runs_failed: $runs_failed,
      capped: $capped,
      exit_status: $exit_status
    }' >"$marker"
  "$AWS_COMMAND" s3 cp "$marker" "s3://$bucket/$DONE_KEY"
  rm -f "$marker"
}

plan=""
masters_dir=""
work_dir=""
bucket=""
commit=""
instance_id=""
instance_type=""
threads=""
versions_file=${VERSIONS_FILE:-}

while (($#)); do
  flag=$1
  [[ $# -ge 2 ]] || usage_error "$flag exige um valor"
  value=$2
  case $flag in
    --plan) plan=$value ;;
    --masters-dir) masters_dir=$value ;;
    --work-dir) work_dir=$value ;;
    --bucket) bucket=$value ;;
    --commit) commit=$value ;;
    --instance-id) instance_id=$value ;;
    --instance-type) instance_type=$value ;;
    --threads) threads=$value ;;
    --versions-file) versions_file=$value ;;
    --output-timeout) OUTPUT_TIMEOUT_SECONDS=$value ;;
    --total-timeout) TOTAL_TIMEOUT_SECONDS=$value ;;
    *) usage_error "argumento desconhecido: $flag" ;;
  esac
  shift 2
done

for name in plan masters_dir work_dir bucket commit instance_id instance_type versions_file; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done

# Resolvido aqui e não no default da variável: o `nproc` de uma máquina que não o
# tem derrubaria também a invocação que trouxe `--threads`.
[[ -n $threads ]] || threads=$(nproc)
for name in threads OUTPUT_TIMEOUT_SECONDS TOTAL_TIMEOUT_SECONDS; do
  [[ ${!name} =~ ^[0-9]+$ ]] || usage_error "$name em inteiro positivo: ${!name}"
done

[[ -r $plan ]] || usage_error "plano ilegível: $plan"
[[ -r $versions_file ]] || usage_error "arquivo de versões ilegível: $versions_file"
jq -e '.outputs | type == "array"' "$plan" >/dev/null 2>&1 ||
  usage_error "o plano não tem a lista .outputs: $plan"
vmaf_model=$(jq -r '.quality.vmaf_model // empty' "$plan")
[[ -n $vmaf_model ]] || usage_error "o plano não declara .quality.vmaf_model: $plan"

results_dir=$work_dir/$RESULTS_DIR_NAME
mkdir -p "$results_dir"

trap 'abort 143' TERM
trap 'abort 130' INT

output_count=$(jq '.outputs | length' "$plan")
runs_total=0
runs_failed=0
capped=false

for ((i = 0; i < output_count; i++)); do
  if ((SECONDS >= TOTAL_TIMEOUT_SECONDS)); then
    log "teto de ${TOTAL_TIMEOUT_SECONDS}s atingido antes do output $((i + 1)): parando"
    capped=true
    break
  fi
  runs_total=$((runs_total + 1))
  judge_output "$i"
  if ((exit_code != 0)); then
    runs_failed=$((runs_failed + 1))
  fi
  write_progress "$((i + 1))"
done

exit_status=0
if ((runs_failed > 0)) || [[ $capped == true ]]; then
  exit_status=1
fi

write_done_marker
log "$runs_total outputs, $runs_failed com falha, ${SECONDS}s"

exit "$exit_status"
