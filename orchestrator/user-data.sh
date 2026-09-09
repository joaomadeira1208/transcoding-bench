#!/bin/bash
#
# O user-data fino de todo papel (ADR-0013/0017): um template só, renderizado
# pelo `templatefile()` do Terraform para o Orquestrador e pelo `string.Template`
# da stdlib para as efêmeras. Daí os placeholders de cifrão-e-chaves, a única
# sintaxe que os dois resolvem, e a regra que ela impõe: **nenhum cifrão fora
# deles**. Um cifrão solto — uma variável de shell, um subshell — é placeholder
# para o `string.Template`, e a renderização em Python passa a levantar
# `ValueError`. O que precisa de variável mora no `bootstrap.sh` do papel.
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
