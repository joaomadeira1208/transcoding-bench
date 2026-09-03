#!/usr/bin/env bash
#
# Shim da AWS CLI: `s3://<bucket>/<key>` é `$SMOKE_S3_ROOT/<bucket>/<key>`.

set -euo pipefail

{
  printf '%s\0' "$@"
  printf '\n'
} >>"$SMOKE_ARGV_DIR/aws.argv"
printf 'aws\n' >>"$SMOKE_ARGV_DIR/sequence"

if [[ ${SMOKE_AWS_EXIT:-0} != 0 ]]; then
  printf 'smoke aws: falha induzida\n' >&2
  exit "$SMOKE_AWS_EXIT"
fi

fail() {
  printf 'smoke aws: %s\n' "$*" >&2
  exit 255
}

object_keys() {
  (cd "$1" && find . -type f | sed 's|^\./||' | LC_ALL=C sort)
}

object_path() {
  [[ $1 == s3://*/* ]] || fail "URI fora do formato s3://<bucket>/<key>: $1"
  printf '%s\n' "$SMOKE_S3_ROOT/${1#s3://}"
}

s3_cp() {
  local recursive="" source="" destination=""
  while (($#)); do
    case $1 in
      --recursive) recursive=1 ;;
      --*) ;;
      *)
        if [[ -z $source ]]; then
          source=$1
        elif [[ -z $destination ]]; then
          destination=$1
        else
          fail "argumento a mais em s3 cp: $1"
        fi
        ;;
    esac
    shift
  done
  [[ -n $source && -n $destination ]] || fail "s3 cp exige origem e destino"
  destination=$(object_path "$destination")

  if [[ -n $recursive ]]; then
    [[ -d $source ]] || fail "origem recursiva não é diretório: $source"
    source=${source%/}
    destination=${destination%/}
    local relative
    while IFS= read -r relative; do
      mkdir -p "$(dirname "$destination/$relative")"
      cp "$source/$relative" "$destination/$relative"
      printf 'upload: %s to %s\n' "$source/$relative" "$destination/$relative"
    done < <(object_keys "$source")
  else
    [[ -f $source ]] || fail "origem não é arquivo: $source"
    mkdir -p "$(dirname "$destination")"
    cp "$source" "$destination"
    printf 'upload: %s to %s\n' "$source" "$destination"
  fi
}

# Chaves em ordem binária e nenhuma saída quando não há objeto, como a CLI de
# verdade: um leitor que espere sempre um documento JSON quebra aqui primeiro.
s3api_list_objects_v2() {
  local bucket="" prefix=""
  while (($#)); do
    case $1 in
      --bucket)
        bucket=$2
        shift 2
        ;;
      --prefix)
        prefix=$2
        shift 2
        ;;
      --*)
        shift 2
        ;;
      *) shift ;;
    esac
  done
  [[ -n $bucket ]] || fail "list-objects-v2 exige --bucket"
  local root=$SMOKE_S3_ROOT/$bucket
  [[ -d $root ]] || fail "bucket inexistente: $bucket"

  local key size
  while IFS= read -r key; do
    [[ $key == "$prefix"* ]] || continue
    size=$(wc -c <"$root/$key" | tr -d ' ')
    jq -n --arg key "$key" --argjson size "$size" '{Key: $key, Size: $size}'
  done < <(object_keys "$root") |
    jq -s 'if length == 0 then empty else {Contents: .} end'
}

case "${1:-} ${2:-}" in
  "s3 cp")
    shift 2
    s3_cp "$@"
    ;;
  "s3api list-objects-v2")
    shift 2
    s3api_list_objects_v2 "$@"
    ;;
  *) fail "subcomando não shimado: ${1:-} ${2:-}" ;;
esac
