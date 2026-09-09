#!/bin/bash
#
# Template, não script: quem o renderiza é o `templatefile()` do Terraform e o
# `string.Template` do Python (`orchestrator/README.md`). Nenhum cifrão fora dos
# placeholders — uma variável de shell aqui dentro é placeholder para o
# `string.Template`, e a renderização em Python passa a levantar.
#
# shellcheck disable=SC2154 # os placeholders não são variáveis de shell: quem os
# atribui é o renderizador, antes de a instância ver o arquivo.

set -euxo pipefail

apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y git

sudo -u ubuntu -H -- git clone "${repo_url}" /home/ubuntu/transcoding-bench
sudo -u ubuntu -H -- git -C /home/ubuntu/transcoding-bench checkout "${commit}"

# shellcheck disable=SC2086 # `role_args` é a linha de argumentos do papel, e
# citá-la a entregaria como um argumento só.
sudo -u ubuntu -H -- bash "/home/ubuntu/transcoding-bench/${role}/bootstrap.sh" ${role_args}
