#!/usr/bin/env bash

set -euxo pipefail

AWS_INSTALLER_URL=https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip
AWS_INSTALLER_ZIP=/tmp/awscliv2.zip
AWS_INSTALLER_DIR=/tmp/aws

APT_LOCK_TIMEOUT_SECONDS=300

PYTHON=python3.12

INFRA_FILE=infra.json

# O `external.py` abre o SSH com este caminho como default; os dois nomes têm
# que casar.
SSH_KEY_PATH=$HOME/.ssh/transcoding-bench.pem

EXIT_USAGE=2

usage_error() {
  printf 'bootstrap.sh: %s\n' "$*" >&2
  printf 'uso: bootstrap.sh --work-dir <dir> --infra <json>\n' >&2
  exit "$EXIT_USAGE"
}

work_dir=""
infra=""

while (($#)); do
  flag=$1
  [[ $# -ge 2 ]] || usage_error "$flag exige um valor"
  value=$2
  case $flag in
    --work-dir) work_dir=$value ;;
    --infra) infra=$value ;;
    *) usage_error "argumento desconhecido: $flag" ;;
  esac
  shift 2
done

for name in work_dir infra; do
  [[ -n ${!name} ]] || usage_error "faltou --${name//_/-}"
done

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

sudo apt-get -o "DPkg::Lock::Timeout=$APT_LOCK_TIMEOUT_SECONDS" update
sudo apt-get -o "DPkg::Lock::Timeout=$APT_LOCK_TIMEOUT_SECONDS" install -y \
  git jq tmux unzip "$PYTHON-venv"

curl -fsSL "$AWS_INSTALLER_URL" -o "$AWS_INSTALLER_ZIP"
unzip -q -o "$AWS_INSTALLER_ZIP" -d "$(dirname "$AWS_INSTALLER_DIR")"
sudo "$AWS_INSTALLER_DIR/install" --update
rm -rf "$AWS_INSTALLER_DIR" "$AWS_INSTALLER_ZIP"

"$PYTHON" -m venv "$repo_dir/.venv"
"$repo_dir/.venv/bin/pip" install --upgrade pip
"$repo_dir/.venv/bin/pip" install -r "$repo_dir/orchestrator/requirements.txt"

mkdir -p "$work_dir"
jq . <<<"$infra" >"$work_dir/$INFRA_FILE"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
key_parameter=$(jq -er '.ssh_private_key_parameter_name' "$work_dir/$INFRA_FILE")
(
  umask 077
  aws ssm get-parameter --name "$key_parameter" --with-decryption \
    --query Parameter.Value --output text >"$SSH_KEY_PATH"
)
chmod 600 "$SSH_KEY_PATH"
