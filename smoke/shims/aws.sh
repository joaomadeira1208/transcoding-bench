#!/usr/bin/env bash
#
# Shim da AWS CLI: `s3://<bucket>/<key>` é `$SMOKE_S3_ROOT/<bucket>/<key>`, nos
# dois sentidos do `s3 cp` e no de bucket para disco do `s3 sync`.

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

object_key() {
  local without_scheme=${1#s3://}
  printf '%s\n' "${without_scheme#*/}"
}

# Toda versão do objeto, e não só a última: o `status/{type}_progress` é
# sobrescrito a cada run, e um `run_index` gravado sempre como `run_count`
# passaria pela asserção sobre o objeto que ficou no bucket.
record_version() {
  local dir=$SMOKE_ARGV_DIR/versions/$1
  mkdir -p "$dir"
  cp "$2" "$(printf '%s/%04d' "$dir" "$(find "$dir" -type f | wc -l)")"
}

s3_cp() {
  local recursive="" source="" destination="" key
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

  if [[ $source == s3://* ]]; then
    [[ -z $recursive ]] || fail "s3 cp --recursive de bucket para disco não é shimado"
    source=$(object_path "$source")
    [[ -f $source ]] || fail "objeto inexistente: $source"
    mkdir -p "$(dirname "$destination")"
    cp "$source" "$destination"
    printf 'download: %s to %s\n' "$source" "$destination"
    return
  fi

  key=$(object_key "$destination")
  [[ $key != "${SMOKE_AWS_FAIL_KEY:-}" ]] || fail "falha induzida no objeto $key"
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
    record_version "$key" "$source"
    printf 'upload: %s to %s\n' "$source" "$destination"
  fi
}

s3_sync() {
  local source="" destination="" filter_flag=() filter_pattern=() bucket relative included i
  while (($#)); do
    case $1 in
      --exclude | --include)
        filter_flag+=("$1")
        filter_pattern+=("$2")
        shift 2
        continue
        ;;
      --only-show-errors) ;;
      # Recusa em vez de ignorar: uma flag com valor — o `--copy-props` do outro
      # `s3 sync` do adaptador — ignorada aqui deixaria o valor dela virar a
      # origem, e o fake responderia sobre o prefixo errado.
      --*) fail "flag não shimada em s3 sync: $1" ;;
      *)
        if [[ -z $source ]]; then
          source=$1
        elif [[ -z $destination ]]; then
          destination=$1
        else
          fail "argumento a mais em s3 sync: $1"
        fi
        ;;
    esac
    shift
  done
  [[ -n $source && -n $destination ]] || fail "s3 sync exige origem e destino"
  [[ $source == s3://* && $destination != s3://* ]] ||
    fail "s3 sync fora do sentido bucket para disco não é shimado"

  bucket=${source#s3://}
  bucket=${bucket%%/*}
  [[ -d $SMOKE_S3_ROOT/$bucket ]] || fail "bucket inexistente: $bucket"

  source=$(object_path "$source")
  # Prefixo sem objeto é um sync de zero arquivos com status zero, como na CLI de
  # verdade. Falhar aqui faria a campanha que morreu antes do primeiro upload —
  # todo bloco ausente, o caso mais comum da retomada — voltar como bucket
  # ilegível.
  [[ -d $source ]] || return 0
  source=${source%/}
  destination=${destination%/}

  while IFS= read -r relative; do
    included=1
    for ((i = 0; i < ${#filter_pattern[@]}; i++)); do
      # Sem aspas de propósito: o padrão é glob, e o `*` do
      # `--include '*/meta.json'` tem de atravessar a barra como o fnmatch da CLI
      # de verdade.
      # shellcheck disable=SC2053
      [[ $relative == ${filter_pattern[i]} ]] || continue
      if [[ ${filter_flag[i]} == --include ]]; then
        included=1
      else
        included=""
      fi
    done
    [[ -n $included ]] || continue
    mkdir -p "$(dirname "$destination/$relative")"
    cp "$source/$relative" "$destination/$relative"
    printf 'download: %s to %s\n' "$source/$relative" "$destination/$relative"
  done < <(object_keys "$source")
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
  "s3 sync")
    shift 2
    s3_sync "$@"
    ;;
  "s3api list-objects-v2")
    shift 2
    s3api_list_objects_v2 "$@"
    ;;
  *) fail "subcomando não shimado: ${1:-} ${2:-}" ;;
esac
