# Configuração da infraestrutura AWS

## Região

**us-east-1 (N. Virginia).** Região mais barata da AWS. Como o experimento é batch (sem requisito de latência), proximidade geográfica é irrelevante. sa-east-1 (São Paulo) seria ~20–30% mais cara sem benefício.

## Rede

**Subnets públicas.** Instâncias recebem IP público e acessam internet diretamente. Security groups restringem acesso: porta 22 aberta apenas pro IP da instância de controle dentro da VPC. Subnets privadas adicionariam NAT Gateway (~$2–3 de custo) e complexidade de roteamento sem ganho de segurança proporcional pra instâncias efêmeras de 2 dias.

## AMI base

**Ubuntu 24.04 LTS.** Escolhido por estabilidade e previsibilidade em workloads de benchmarking. Amazon Linux 2023 tem reports documentados de regressões de performance em instâncias compute-optimized (issues #1005, #819, #1029 no GitHub do amazonlinux). Pra um projeto que mede performance, o OS precisa ser o mais estável possível. Ubuntu 24.04 tem frame pointers habilitados por default (útil pra profiling) e é mais usado em comunidades de benchmarking/HPC. Docker, git, perf e sysstat (pidstat) são instalados no bootstrap. AWS CLI precisa ser instalado manualmente (não vem pré-instalado como no AL2023).

## Considered Options

- **sa-east-1 (São Paulo)** — rejeitado: 20–30% mais cara; latência não importa pra batch.
- **Subnets privadas + NAT Gateway** — rejeitado: custo e complexidade extras; security group em subnet pública já isola as instâncias. Risco residual (IP público exposto) é irrelevante pra instâncias efêmeras protegidas por SG.
- **Amazon Linux 2023** — rejeitado: reports documentados de regressões de performance 2–3x em instâncias compute-optimized (GitHub issues #1005, #819, #1029). Inaceitável pra um projeto de benchmarking onde o OS deve introduzir overhead mínimo e previsível. AWS CLI pré-instalado não compensa o risco.

## Consequences

- Todas as instâncias ficam na mesma VPC em us-east-1. Transferências S3 são intra-região (grátis).
- Bootstrap de cada instância inclui: instalar Docker + AWS CLI → git clone → docker build (encode) ou git clone (controle/Juiz).
- Security group é a única camada de proteção de rede. Deve ser configurado com cuidado no Terraform — porta 22 restrita, não aberta ao mundo.
- `perf_event_paranoid` deve ser configurado para permitir acesso aos hardware PMU counters (necessário pro `perf stat` da ADR-0006).
