# Tooling e linguagens do projeto

O projeto usa **três linguagens/ferramentas**, cada uma na camada onde é natural:

| Camada | Ferramenta | Justificativa |
|---|---|---|
| Infra estática (S3, VPC, SG, IAM, key pair, instância do Orquestrador) | **Terraform** | Declarativo; cria uma vez, `terraform destroy` no fim. Ideal pra recursos que persistem o experimento inteiro. |
| Orquestração (criar/destruir instâncias efêmeras, disparar cenários, monitorar, coletar) | **Python + AWS CLI (via subprocess)** | Imperativo; natural pra lógica com estado (retomada, seed, meta.json). AWS CLI via subprocess evita aprender boto3. Mesma linguagem da análise pós-experimento. |
| Dentro das instâncias de encode/Juiz (invocar FFmpeg, instrumentação, upload S3) | **Shell (bash)** | FFmpeg, `perf stat`, `pidstat`, `/usr/bin/time` são comandos de terminal. Shell é o wrapper natural. Instâncias são "burras" — recebem plano pronto e executam. |

Instâncias efêmeras (encode e Juiz) são criadas/destruídas pelo orquestrador via `aws ec2 run-instances` / `aws ec2 terminate-instances`, não pelo Terraform. Terraform gerencia apenas infra de lifecycle longo. Isso evita stages ou `-target` no Terraform — um `apply` no início, um `destroy` no fim.

## Considered Options

- **Python + boto3 pra tudo (sem Terraform)** — rejeitado: usuário já tem contato com Terraform; boto3 seria ferramenta nova sem ganho proporcional. Risco de esquecer instância ligada sem `terraform destroy` como rede de segurança.
- **Terraform pra tudo (incluindo instâncias efêmeras)** — rejeitado: Terraform é declarativo, mas o lifecycle das instâncias é imperativo (cria → roda 46h → destrói → cria Juiz → destrói). Forçar isso no Terraform requer stages, `-target`, ou comentar/descomentar — luta contra a ferramenta.
- **Shell puro pra orquestração** — rejeitado: lógica com estado (retomada, randomização com seed, geração de meta.json, parsing de JSON) é frágil em bash.
- **Python dentro das instâncias de encode** — rejeitado: o loop de cenários é um `while` + `for` que chama dois comandos (run_scenario.sh + aws s3 cp). Não justifica instalar/configurar Python nas instâncias de encode.

## Consequences

- Python é a linguagem dominante do projeto (orquestração + análise). Shell é auxiliar e contido dentro das instâncias.
- AWS CLI precisa estar instalada nas instâncias de encode, no Juiz, e na instância do Orquestrador.
- A fronteira é clara: Python decide, shell executa.
