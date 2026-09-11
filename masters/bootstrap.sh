#!/usr/bin/env bash

set -euxo pipefail

AWS_INSTALLER_ZIP=/tmp/awscliv2.zip
AWS_INSTALLER_DIR=/tmp/aws

APT_LOCK_TIMEOUT_SECONDS=300

IMAGE_TAG=transcoding-bench

EXIT_USAGE=2

usage_error() {
  printf 'bootstrap.sh: %s\n' "$*" >&2
  printf 'uso: bootstrap.sh --work-dir <dir>\n' >&2
  exit "$EXIT_USAGE"
}

work_dir=""

while (($#)); do
  flag=$1
  [[ $# -ge 2 ]] || usage_error "$flag exige um valor"
  value=$2
  case $flag in
    --work-dir) work_dir=$value ;;
    *) usage_error "argumento desconhecido: $flag" ;;
  esac
  shift 2
done

[[ -n $work_dir ]] || usage_error "faltou --work-dir"

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

sudo apt-get -o "DPkg::Lock::Timeout=$APT_LOCK_TIMEOUT_SECONDS" update
sudo apt-get -o "DPkg::Lock::Timeout=$APT_LOCK_TIMEOUT_SECONDS" install -y \
  docker.io unzip

curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o "$AWS_INSTALLER_ZIP"
unzip -q -o "$AWS_INSTALLER_ZIP" -d "$(dirname "$AWS_INSTALLER_DIR")"
sudo "$AWS_INSTALLER_DIR/install" --update
rm -rf "$AWS_INSTALLER_DIR" "$AWS_INSTALLER_ZIP"

mkdir -p "$work_dir"

sudo usermod -aG docker "$(id -un)"
sudo docker build -t "$IMAGE_TAG" "$repo_dir/docker"
