#!/usr/bin/env bash
#
# O bash copia, nunca deriva (ADR-0019): nome, geometria, codec, cadência e
# contagem de frames de cada Master chegam no plano, e o que este script faz é
# materializá-los e conferir o que o `ffprobe` encontrou contra eles.

set -euo pipefail

SCHEMA_VERSION=1

CURL_COMMAND=curl
UNZIP_COMMAND=unzip
FFMPEG_COMMAND=ffmpeg
FFPROBE_COMMAND=ffprobe
AWS_COMMAND=aws

SCALE_FLAGS=lanczos

MASTERS_PREFIX=masters
MANIFEST_NAME=manifest.json

PROBED_FIELDS=(width height codec_name pix_fmt frame_rate frames)

EXIT_USAGE=2
EXIT_SOURCE=70
EXIT_MASTER=71

usage_error() {
  printf 'prepare.sh: %s\n' "$*" >&2
  printf 'uso: prepare.sh --plan <json> --bucket <name> --work-dir <dir> [--versions-file <path>]\n' >&2
  exit "$EXIT_USAGE"
}

source_error() {
  printf 'prepare.sh: %s\n' "$*" >&2
  exit "$EXIT_SOURCE"
}

master_error() {
  printf 'prepare.sh: %s\n' "$*" >&2
  exit "$EXIT_MASTER"
}

plan_field() {
  jq -r --arg name "$2" \
    'if has($name) then .[$name] else error("campo ausente no plano: \($name)") end' \
    <<<"$1"
}

prepare_source() {
  local video=$1 slug source url file expected archive path digest size
  slug=$(plan_field "$video" video)
  source=$(jq -c '.source' <<<"$video")
  url=$(plan_field "$source" url)
  file=$(plan_field "$source" file)
  expected=$(plan_field "$source" sha256)

  archive=$sources_dir/$file.zip
  path=$sources_dir/$file

  "$CURL_COMMAND" -fsSL "$url" -o "$archive"
  "$UNZIP_COMMAND" -q -o -j "$archive" -d "$sources_dir"
  [[ -r $path ]] || source_error "$slug: o unzip de $url não produziu $file"

  digest=$(sha256sum "$path" | cut -d' ' -f1)
  if [[ $digest != "$expected" ]]; then
    source_error "$slug: o sha256 de $file diverge do plano: esperado $expected, observado $digest"
  fi
  size=$(wc -c <"$path" | tr -d ' ')

  jq -cn \
    --arg slug "$slug" \
    --arg url "$url" \
    --arg file "$file" \
    --argjson size "$size" \
    --arg sha256 "$digest" \
    '{($slug): {url: $url, file: $file, size: $size, sha256: $sha256}}' \
    >>"$sources_ndjson"
}

remux_master() {
  local video=$1 source file name
  source=$(jq -c '.source' <<<"$video")
  file=$(plan_field "$source" file)
  name=$(plan_field "$(jq -c '.master' <<<"$video")" name)

  "$FFMPEG_COMMAND" -nostdin -y \
    -i "$sources_dir/$file" \
    -c copy \
    "$masters_dir/$name"
}

derive_master() {
  local master=$1 from=$2 name width height codec_name pix_fmt
  name=$(plan_field "$master" name)
  width=$(plan_field "$master" width)
  height=$(plan_field "$master" height)
  codec_name=$(plan_field "$master" codec_name)
  pix_fmt=$(plan_field "$master" pix_fmt)

  "$FFMPEG_COMMAND" -nostdin -y \
    -i "$masters_dir/$from" \
    -vf "scale=$width:$height:flags=$SCALE_FLAGS" \
    -c:v "$codec_name" \
    -pix_fmt "$pix_fmt" \
    -an \
    "$masters_dir/$name"
}

probe_master() {
  local master=$1 name probe observed field seen want size digest
  name=$(plan_field "$master" name)
  probe=$("$FFPROBE_COMMAND" -v error -of json -select_streams v:0 -count_packets \
    -show_entries stream=width,height,codec_name,pix_fmt,r_frame_rate,nb_read_packets \
    "$masters_dir/$name")
  observed=$(jq -c '.streams[0]
    | {
        width, height, codec_name, pix_fmt,
        frame_rate: .r_frame_rate,
        frames: (.nb_read_packets | tonumber)
      }' <<<"$probe")

  for field in "${PROBED_FIELDS[@]}"; do
    seen=$(plan_field "$observed" "$field")
    want=$(plan_field "$master" "$field")
    if [[ $seen != "$want" ]]; then
      master_error "$name: o ffprobe diverge do plano em $field: esperado $want, observado $seen"
    fi
  done

  size=$(wc -c <"$masters_dir/$name" | tr -d ' ')
  digest=$(sha256sum "$masters_dir/$name" | cut -d' ' -f1)

  jq -cn \
    --argjson master "$master" \
    --argjson observed "$observed" \
    --argjson size "$size" \
    --arg sha256 "$digest" \
    '($master | {name, video, tier}) + $observed + {size: $size, sha256: $sha256}' \
    >>"$masters_ndjson"
}

write_manifest() {
  jq -n \
    --arg schema_version "$SCHEMA_VERSION" \
    --slurpfile versions "$versions_file" \
    --slurpfile sources "$sources_ndjson" \
    --slurpfile masters "$masters_ndjson" \
    '{
      schema_version: $schema_version,
      versions: $versions[0],
      sources: ($sources | add),
      masters: $masters
    }' >"$manifest_json"
}

plan=""
bucket=""
work_dir=""
versions_file=${VERSIONS_FILE:-}

while (($#)); do
  flag=$1
  [[ $# -ge 2 ]] || usage_error "$flag exige um valor"
  value=$2
  case $flag in
    --plan) plan=$value ;;
    --bucket) bucket=$value ;;
    --work-dir) work_dir=$value ;;
    --versions-file) versions_file=$value ;;
    *) usage_error "argumento desconhecido: $flag" ;;
  esac
  shift 2
done

for name in plan bucket work_dir versions_file; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done

[[ -r $versions_file ]] || usage_error "arquivo de versões ilegível: $versions_file"
jq -e '.videos | type == "array" and length > 0' >/dev/null <<<"$plan" ||
  usage_error "o plano não tem a lista .videos"

sources_dir=$work_dir/sources
masters_dir=$work_dir/$MASTERS_PREFIX
mkdir -p "$sources_dir" "$masters_dir"

manifest_json=$work_dir/$MANIFEST_NAME
sources_ndjson=$work_dir/sources.ndjson
masters_ndjson=$work_dir/masters.ndjson
: >"$sources_ndjson"
: >"$masters_ndjson"

video_count=$(jq '.videos | length' <<<"$plan")
for ((v = 0; v < video_count; v++)); do
  video=$(jq -c ".videos[$v]" <<<"$plan")
  prepare_source "$video"
  remux_master "$video"

  remuxed=$(plan_field "$(jq -c '.master' <<<"$video")" name)
  derived_count=$(jq '.derived | length' <<<"$video")
  for ((d = 0; d < derived_count; d++)); do
    derive_master "$(jq -c ".derived[$d]" <<<"$video")" "$remuxed"
  done
done

while IFS= read -r master; do
  probe_master "$master"
done < <(jq -c '.videos[] | .master, .derived[]' <<<"$plan")

while IFS= read -r name; do
  "$AWS_COMMAND" s3 cp "$masters_dir/$name" "s3://$bucket/$MASTERS_PREFIX/$name"
done < <(jq -r '.name' "$masters_ndjson")

write_manifest
"$AWS_COMMAND" s3 cp "$manifest_json" "s3://$bucket/$MASTERS_PREFIX/$MANIFEST_NAME"

printf 'prepare.sh: %s Masters em s3://%s/%s/\n' \
  "$(jq -s length "$masters_ndjson")" "$bucket" "$MASTERS_PREFIX" >&2
