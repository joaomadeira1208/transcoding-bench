#!/usr/bin/env bash
#
# Shim do `unzip`: o arquivo baixado é o placeholder cru, então "descomprimir" é
# copiá-lo para dentro do diretório de destino, sem o sufixo do arquivo.
#
# Cópia e não `mv`: o unzip de verdade deixa o arquivo onde estava, e quem o
# apaga é o `prepare.sh`. Com `mv`, aquele `rm` passaria a agir sobre um arquivo
# que já não existe e o smoke não teria como flagrá-lo.

set -euo pipefail

{
  printf '%s\0' "$@"
  printf '\n'
} >>"$SMOKE_ARGV_DIR/unzip.argv"
printf 'unzip\n' >>"$SMOKE_ARGV_DIR/sequence"

archive=""
destination=""
while (($#)); do
  case $1 in
    -d)
      destination=$2
      shift 2
      ;;
    -*) shift ;;
    *)
      archive=$1
      shift
      ;;
  esac
done

if [[ -z $archive || -z $destination ]]; then
  printf 'smoke unzip: exige o arquivo e o -d de destino\n' >&2
  exit 255
fi

entry=$(basename "$archive")
cp "$archive" "$destination/${entry%.zip}"
