#!/usr/bin/env bash
#
# O bash copia, nunca deriva (ADR-0019): até o que caberia numa tabela aqui dentro
# — o muxer do bitstream, os eventos de PMU — chega como dado.

set -euo pipefail

SCHEMA_VERSION=1

FFMPEG_COMMAND=ffmpeg
PERF_COMMAND=perf
PIDSTAT_COMMAND=pidstat

# Variável, e não literal: um caminho absoluto não é substituível por shim no
# PATH, e o smoke roda este script de verdade no Mac, cujo `time` é o do BSD.
TIME_BIN=${TIME_BIN:-/usr/bin/time}

# Sem o `--quiet` da invocação, o GNU time prefixa "Command exited with non-zero
# status N" ao formato, e o `time.json` de todo run falho deixa de ser JSON.
TIME_FORMAT='{"elapsed_s":%e,"user_s":%U,"sys_s":%S,"max_rss_kb":%M,"major_page_faults":%F,"minor_page_faults":%R,"fs_inputs":%I,"fs_outputs":%O,"voluntary_ctx_switches":%w,"involuntary_ctx_switches":%c,"exit_status":%x}'

PIDSTAT_INTERVAL=1

PID_RESOLVE_ATTEMPTS=60
PID_RESOLVE_INTERVAL=0.05

# `time` é a raiz, `perf stat` é o nível 1, o FFmpeg é o 2.
ENCODER_DEPTH=2

EXIT_USAGE=2
EXIT_INSTRUMENTATION=70
EXIT_BITSTREAM=71

usage_error() {
  printf 'run_scenario.sh: %s\n' "$*" >&2
  printf 'uso: run_scenario.sh --run <json> --masters-dir <dir> --runs-dir <dir> --commit <sha> --instance-id <id> --instance-type <type> [--versions-file <path>]\n' >&2
  exit "$EXIT_USAGE"
}

process_descendants() {
  ps -Ao pid=,ppid=,args= | awk -v root="$1" '
    {
      pid[NR] = $1
      parent[NR] = $2
      sub(/^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+/, "")
      args[NR] = $0
      n = NR
    }
    END {
      queue[1] = root
      queued = 1
      depth[root] = 0
      for (head = 1; head <= queued; head++)
        for (i = 1; i <= n; i++)
          if (parent[i] == queue[head] && !(pid[i] in depth)) {
            depth[pid[i]] = depth[queue[head]] + 1
            queue[++queued] = pid[i]
            command[pid[i]] = args[i]
          }
      for (tail = queued; tail > 1; tail--)
        print depth[queue[tail]] "\t" queue[tail] "\t" command[queue[tail]]
    }
  '
}

# Casar só pelo nome pegaria o `perf` na janela em que ele já existe e o FFmpeg
# ainda não, porque os wrappers carregam o argv do FFmpeg dentro do seu. Rejeitado
# `pidstat -C ffmpeg`: pega a extração do bitstream, que roda mais abaixo.
resolve_encoder_pid() {
  local root=$1 attempt depth pid args
  for ((attempt = 0; attempt < PID_RESOLVE_ATTEMPTS; attempt++)); do
    while IFS=$'\t' read -r depth pid args; do
      if ((depth >= ENCODER_DEPTH)) && [[ $args == *"$FFMPEG_COMMAND"* ]]; then
        printf '%s\n' "$pid"
        return 0
      fi
    done < <(process_descendants "$root")
    sleep "$PID_RESOLVE_INTERVAL"
  done
  return 1
}

kill_process_tree() {
  local depth pid args
  while IFS=$'\t' read -r depth pid args; do
    kill "$pid" 2>/dev/null || true
  done < <(process_descendants "$1")
  kill "$1" 2>/dev/null || true
}

# SIGINT, e não SIGTERM: a saída vai para arquivo, portanto block-buffered, e só
# o primeiro faz o `pidstat` esvaziá-la. O SIGTERM depois desprende o `wait`.
stop_pidstat() {
  [[ -n ${pidstat_pid:-} ]] || return 0
  kill -INT "$pidstat_pid" 2>/dev/null || true
  sleep 0.2
  kill -TERM "$pidstat_pid" 2>/dev/null || true
  wait "$pidstat_pid" 2>/dev/null || true
  pidstat_pid=""
}

# shellcheck disable=SC2329 # invocada pelo `trap EXIT`
cleanup() {
  stop_pidstat
  if [[ -n ${chain_pid:-} ]]; then
    kill_process_tree "$chain_pid"
    chain_pid=""
  fi
}

run_field() {
  jq -r --arg name "$1" \
    'if has($name) then .[$name] else error("campo ausente no objeto de run: \($name)") end' \
    <<<"$run"
}

# A checagem do `perf.json` é textual porque `perf stat -j` emite um objeto por
# linha, com cabeçalho que varia com a versão.
instrumentation_failure_reason() {
  local event
  if [[ ! -s $perf_json ]]; then
    printf '%s' "o perf stat não escreveu $perf_json"
    return 0
  fi
  if grep -q "not supported" "$perf_json"; then
    printf '%s' "algum evento de PMU voltou não-suportado em $perf_json"
    return 0
  fi
  for event in "${pmu_events[@]}"; do
    # Entre aspas, e não como substring: solto, `instructions` casaria dentro de
    # `branch-instructions` e um contador ausente passaria pela guarda.
    if ! grep -q -- "\"$event\"" "$perf_json"; then
      printf '%s' "o evento $event não aparece em $perf_json"
      return 0
    fi
  done
  if [[ ! -s $time_json ]] || ! jq . "$time_json" >/dev/null 2>&1; then
    printf '%s' "o /usr/bin/time não escreveu JSON em $time_json"
    return 0
  fi
  if [[ ! -s $pidstat_txt ]]; then
    printf '%s' "o pidstat não escreveu amostra nenhuma em $pidstat_txt"
    return 0
  fi
}

extract_bitstream_sha256() {
  local digest
  digest=$("$FFMPEG_COMMAND" -nostdin -hide_banner -loglevel error \
    -i "$output_path" -c copy -f "$bitstream_muxer" - | sha256sum | cut -d' ' -f1) || return 1
  printf '%s\n' "$digest" >"$sha256_path"
}

# Projeção, e não campos remontados um a um: `jq` copia cada valor com o tipo que
# ele já tinha, e é isso que mantém `warmup` booleano em vez da string que
# derrubaria o filtro de warm-up.
write_meta() {
  jq -n \
    --arg schema_version "$SCHEMA_VERSION" \
    --argjson run "$run" \
    --arg run_id "$run_id" \
    --arg started_at "$started_at" \
    --arg finished_at "$finished_at" \
    --argjson exit_code "$exit_code" \
    --arg commit "$commit" \
    --arg instance_id "$instance_id" \
    --arg instance_type "$instance_type" \
    --slurpfile versions "$versions_file" \
    '{schema_version: $schema_version}
      + ($run | {
          scenario_id, warmup, seed, codec, encoder, input_res, output_res, video,
          instance, master, output_width, output_height, preset, crf, encoder_args,
          threads, gop_size, pix_fmt, strip_audio, container, scale_flags
        })
      + {
          run_id: $run_id,
          started_at: $started_at,
          finished_at: $finished_at,
          exit_code: $exit_code,
          commit: $commit,
          instance_id: $instance_id,
          instance_type: $instance_type,
          versions: $versions[0]
        }' \
    >"$meta_json"
}

run=""
masters_dir=""
runs_dir=""
commit=""
instance_id=""
instance_type=""
versions_file=${VERSIONS_FILE:-}

while (($#)); do
  flag=$1
  [[ $# -ge 2 ]] || usage_error "$flag exige um valor"
  value=$2
  case $flag in
    --run) run=$value ;;
    --masters-dir) masters_dir=$value ;;
    --runs-dir) runs_dir=$value ;;
    --commit) commit=$value ;;
    --instance-id) instance_id=$value ;;
    --instance-type) instance_type=$value ;;
    --versions-file) versions_file=$value ;;
    *) usage_error "argumento desconhecido: $flag" ;;
  esac
  shift 2
done

for name in run masters_dir runs_dir commit instance_id instance_type versions_file; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done

[[ -r $versions_file ]] || usage_error "arquivo de versões ilegível: $versions_file"
jq -e 'type == "object"' >/dev/null <<<"$run" ||
  usage_error "o objeto de run não é um objeto JSON"

master=$(run_field master)
[[ -r $masters_dir/$master ]] || usage_error "master ilegível: $masters_dir/$master"

encoder=$(run_field encoder)
preset=$(run_field preset)
crf=$(run_field crf)
output_width=$(run_field output_width)
output_height=$(run_field output_height)
scale_flags=$(run_field scale_flags)
threads=$(run_field threads)
gop_size=$(run_field gop_size)
pix_fmt=$(run_field pix_fmt)
strip_audio=$(run_field strip_audio)
container=$(run_field container)
bitstream_muxer=$(run_field bitstream_muxer)

pmu_event_list=$(jq -r '.pmu_events | join(",")' <<<"$run")
IFS=, read -r -a pmu_events <<<"$pmu_event_list"

run_id=$(uuidgen | tr '[:upper:]' '[:lower:]')
run_dir=$runs_dir/$run_id
mkdir -p "$run_dir"

# Antes de qualquer coisa poder falhar: é por ela que o laço sabe o que subir
# quando o run morre no meio.
printf '%s\n' "$run_dir"

meta_json=$run_dir/meta.json
time_json=$run_dir/time.json
perf_json=$run_dir/perf.json
pidstat_txt=$run_dir/pidstat.txt
ffmpeg_log=$run_dir/ffmpeg.log
output_path=$run_dir/output.$container
sha256_path=$run_dir/output.sha256

ffmpeg_argv=(
  "$FFMPEG_COMMAND"
  -nostdin
  -y
  -i "$masters_dir/$master"
  -vf "scale=$output_width:$output_height:flags=$scale_flags"
  -c:v "$encoder"
  -preset "$preset"
  -crf "$crf"
)
while IFS= read -r encoder_arg; do
  ffmpeg_argv+=("$encoder_arg")
done < <(jq -r '.encoder_args[]' <<<"$run")
ffmpeg_argv+=(-g "$gop_size" -pix_fmt "$pix_fmt" -threads "$threads")
if [[ $strip_audio == true ]]; then
  ffmpeg_argv+=(-an)
fi
# `-stats` explícito: o progresso no stderr é a quarta fonte de instrumentação da
# ADR-0006, não algo a deixar no default do FFmpeg.
ffmpeg_argv+=(-stats "$output_path")

trap cleanup EXIT
# O timeout por Execução da ADR-0012 chega como SIGTERM: sem sair pela porta do
# `trap`, o `cleanup` não roda e o FFmpeg fica órfão faturando a instância.
trap 'exit 130' INT
trap 'exit 143' TERM

started_at=$(date -Iseconds)

# Nada entre o `perf` e o encode: um wrapper ali entraria na contagem de
# instruções. É por isso que o `2>` prende no `time` e o `ffmpeg.log` acaba
# recebendo o stderr da cadeia — prendê-lo no argv mais interno exigiria um shell.
"$TIME_BIN" --quiet -f "$TIME_FORMAT" -o "$time_json" \
  "$PERF_COMMAND" stat -j -e "$pmu_event_list" -o "$perf_json" -- \
  "${ffmpeg_argv[@]}" >/dev/null 2>"$ffmpeg_log" &
chain_pid=$!

failure_reason=""
if encoder_pid=$(resolve_encoder_pid "$chain_pid"); then
  # Um shell não-interativo entrega SIGINT **ignorado** aos processos que põe em
  # background: sem job control o sinal do `stop_pidstat` não chega ao `pidstat`.
  set -m
  "$PIDSTAT_COMMAND" -h -r -u -p "$encoder_pid" "$PIDSTAT_INTERVAL" >"$pidstat_txt" 2>&1 &
  pidstat_pid=$!
  set +m
else
  failure_reason="o PID do FFmpeg não foi resolvido dentro da janela"
  kill_process_tree "$chain_pid"
fi

if wait "$chain_pid"; then
  encode_status=0
else
  encode_status=$?
fi
chain_pid=""
stop_pidstat

if [[ -z $failure_reason ]]; then
  failure_reason=$(instrumentation_failure_reason)
fi

if [[ -n $failure_reason ]]; then
  printf 'run_scenario.sh: instrumentação inválida: %s\n' "$failure_reason" >&2
  exit_code=$EXIT_INSTRUMENTATION
else
  exit_code=$encode_status
fi

if ((encode_status == 0)); then
  if ! extract_bitstream_sha256; then
    printf 'run_scenario.sh: a extração do bitstream falhou\n' >&2
    if ((exit_code == 0)); then
      exit_code=$EXIT_BITSTREAM
    fi
  fi
fi

finished_at=$(date -Iseconds)
write_meta

exit "$exit_code"
