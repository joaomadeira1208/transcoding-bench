#!/usr/bin/env bash

set -euo pipefail

AWS_COMMAND=aws

EXIT_FAILURE=1
EXIT_USAGE=2

usage_error() {
  printf 'fetch_masters.sh: %s\n' "$*" >&2
  printf 'uso: fetch_masters.sh --manifest <path> --bucket <name> --prefix <prefix/> --dest <dir>\n' >&2
  exit "$EXIT_USAGE"
}

fail() {
  printf 'fetch_masters.sh: %s\n' "$*" >&2
  exit "$EXIT_FAILURE"
}

log() {
  printf 'fetch_masters.sh: %s\n' "$*" >&2
}

manifest=""
bucket=""
prefix=""
dest=""

while (($#)); do
  flag=$1
  [[ $# -ge 2 ]] || usage_error "$flag exige um valor"
  value=$2
  case $flag in
    --manifest) manifest=$value ;;
    --bucket) bucket=$value ;;
    --prefix) prefix=$value ;;
    --dest) dest=$value ;;
    *) usage_error "argumento desconhecido: $flag" ;;
  esac
  shift 2
done

for name in manifest bucket prefix dest; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done

[[ $prefix == */ ]] || usage_error "o prefixo tem de terminar em barra: $prefix"
[[ -r $manifest ]] || usage_error "manifesto ilegível: $manifest"
jq -e '.masters | type == "array" and length > 0' "$manifest" >/dev/null 2>&1 ||
  usage_error "o manifesto não tem a lista .masters: $manifest"

mkdir -p "$dest"

validated=0
while IFS=$'\t' read -r name expected; do
  target=$dest/$name
  "$AWS_COMMAND" s3 cp "s3://$bucket/$prefix$name" "$target" ||
    fail "$name: o download de s3://$bucket/$prefix$name falhou"
  observed=$(sha256sum "$target" | cut -d' ' -f1)
  [[ $observed == "$expected" ]] ||
    fail "$name: sha256 $observed diverge do $expected que o manifesto declara"
  validated=$((validated + 1))
done < <(jq -r '.masters[] | [.name, .sha256] | @tsv' "$manifest")

log "$validated Masters conferidos contra $manifest"
