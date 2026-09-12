#!/usr/bin/env bash

set -euo pipefail

IMAGE_TAG=transcoding-bench

SCRIPTS_MOUNT=/opt/encode
WORK_MOUNT=/work

MASTERS_DIR_NAME=masters
RUNS_DIR_NAME=runs

EXIT_USAGE=2

usage_error() {
  printf 'launch_container.sh: %s\n' "$*" >&2
  printf 'uso: launch_container.sh --work-dir <dir> --plan <file> --bucket <name> --commit <sha> --instance-id <id> --instance-type <type> [--run-timeout <seconds>] [--total-timeout <seconds>]\n' >&2
  exit "$EXIT_USAGE"
}

work_dir=""
plan=""
bucket=""
commit=""
instance_id=""
instance_type=""
run_timeout=""
total_timeout=""

while (($#)); do
  flag=$1
  [[ $# -ge 2 ]] || usage_error "$flag exige um valor"
  value=$2
  case $flag in
    --work-dir) work_dir=$value ;;
    --plan) plan=$value ;;
    --bucket) bucket=$value ;;
    --commit) commit=$value ;;
    --instance-id) instance_id=$value ;;
    --instance-type) instance_type=$value ;;
    --run-timeout) run_timeout=$value ;;
    --total-timeout) total_timeout=$value ;;
    *) usage_error "argumento desconhecido: $flag" ;;
  esac
  shift 2
done

for name in work_dir plan bucket commit instance_id instance_type; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done

[[ $plan != */* ]] || usage_error "--plan é o nome da fatia dentro do work dir, não um caminho: $plan"
[[ -r $work_dir/$plan ]] || usage_error "fatia ilegível: $work_dir/$plan"

run_all_arguments=(
  --plan "$WORK_MOUNT/$plan"
  --masters-dir "$WORK_MOUNT/$MASTERS_DIR_NAME"
  --runs-dir "$WORK_MOUNT/$RUNS_DIR_NAME"
  --bucket "$bucket"
  --commit "$commit"
  --instance-id "$instance_id"
  --instance-type "$instance_type"
)
[[ -z $run_timeout ]] || run_all_arguments+=(--run-timeout "$run_timeout")
[[ -z $total_timeout ]] || run_all_arguments+=(--total-timeout "$total_timeout")

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

exec sudo docker run --rm \
  --cap-add=PERFMON \
  -v "$repo_dir/encode:$SCRIPTS_MOUNT:ro" \
  -v "$work_dir:$WORK_MOUNT" \
  "$IMAGE_TAG" \
  bash "$SCRIPTS_MOUNT/run_all.sh" \
  "${run_all_arguments[@]}"
