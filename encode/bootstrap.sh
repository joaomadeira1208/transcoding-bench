#!/usr/bin/env bash

set -euxo pipefail

AWS_INSTALLER_URL=https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip
AWS_INSTALLER_ZIP=/tmp/awscliv2.zip
AWS_INSTALLER_DIR=/tmp/aws

APT_LOCK_TIMEOUT_SECONDS=300

PERF_SYSCTL_FILE=/etc/sysctl.d/99-transcoding-bench.conf

IMAGE_TAG=transcoding-bench

MASTERS_DIR_NAME=masters

EXIT_USAGE=2

usage_error() {
  printf 'bootstrap.sh: %s\n' "$*" >&2
  printf 'uso: bootstrap.sh --work-dir <dir> --bucket <name> --plan-key <key> --manifest-key <key> --masters-prefix <prefix/>\n' >&2
  exit "$EXIT_USAGE"
}

work_dir=""
bucket=""
plan_key=""
manifest_key=""
masters_prefix=""

while (($#)); do
  flag=$1
  [[ $# -ge 2 ]] || usage_error "$flag exige um valor"
  value=$2
  case $flag in
    --work-dir) work_dir=$value ;;
    --bucket) bucket=$value ;;
    --plan-key) plan_key=$value ;;
    --manifest-key) manifest_key=$value ;;
    --masters-prefix) masters_prefix=$value ;;
    *) usage_error "argumento desconhecido: $flag" ;;
  esac
  shift 2
done

for name in work_dir bucket plan_key manifest_key masters_prefix; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

sudo apt-get -o "DPkg::Lock::Timeout=$APT_LOCK_TIMEOUT_SECONDS" update
sudo apt-get -o "DPkg::Lock::Timeout=$APT_LOCK_TIMEOUT_SECONDS" install -y \
  docker.io jq unzip linux-tools-common "linux-tools-$(uname -r)"

curl -fsSL "$AWS_INSTALLER_URL" -o "$AWS_INSTALLER_ZIP"
unzip -q -o "$AWS_INSTALLER_ZIP" -d "$(dirname "$AWS_INSTALLER_DIR")"
sudo "$AWS_INSTALLER_DIR/install" --update
rm -rf "$AWS_INSTALLER_DIR" "$AWS_INSTALLER_ZIP"

printf 'kernel.perf_event_paranoid = -1\n' | sudo tee "$PERF_SYSCTL_FILE" >/dev/null
sudo sysctl --system

mkdir -p "$work_dir/$MASTERS_DIR_NAME"

sudo docker build -t "$IMAGE_TAG" "$repo_dir/docker"

plan=$work_dir/$(basename "$plan_key")
manifest=$work_dir/$(basename "$manifest_key")

aws s3 cp "s3://$bucket/$plan_key" "$plan"
aws s3 cp "s3://$bucket/$manifest_key" "$manifest"

bash "$repo_dir/encode/fetch_masters.sh" \
  --manifest "$manifest" \
  --bucket "$bucket" \
  --prefix "$masters_prefix" \
  --dest "$work_dir/$MASTERS_DIR_NAME"
