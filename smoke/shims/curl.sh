#!/usr/bin/env bash
#
# Shim do `curl`: entrega `$SMOKE_SOURCE_FILE` no lugar dos GB da URL do plano.

set -euo pipefail

{
  printf '%s\0' "$@"
  printf '\n'
} >>"$SMOKE_ARGV_DIR/curl.argv"
printf 'curl\n' >>"$SMOKE_ARGV_DIR/sequence"

output=""
while (($#)); do
  case $1 in
    -o | --output)
      output=$2
      shift 2
      ;;
    *) shift ;;
  esac
done

if [[ -z $output ]]; then
  printf 'smoke curl: sem -o, e o shim não escreve em stdout\n' >&2
  exit 255
fi

cp "$SMOKE_SOURCE_FILE" "$output"
