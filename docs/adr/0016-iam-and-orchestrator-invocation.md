# IAM, credenciais e invocação do orquestrador

Define como cada instância autentica na AWS, com quais permissões, e como o Orquestrador é iniciado e mantido vivo durante a campanha (~46h+).

## Autenticação: instance profiles, sem chaves estáticas

As três instâncias (Orquestrador, encode, Juiz) recebem **IAM roles via instance profile** (Terraform). Nenhuma chave estática toca o disco. O AWS CLI obtém credenciais temporárias do IMDS, que **rotacionam sozinhas** durante toda a vida da instância — não há expiração a gerenciar. Como o Orquestrador chama a AWS CLI via `subprocess` (ADR-0009), cada chamada é um processo novo que lê o IMDS na hora e recebe credencial válida; o mesmo vale pro `aws s3 cp` das instâncias de encode ao longo das ~46h.

## Matriz de permissões

| Role | Permissões | Escopo |
|---|---|---|
| **orchestrator** | `ec2:RunInstances`, `ec2:TerminateInstances`, `ec2:DescribeInstances`, `ec2:DescribeInstanceStatus` | `*` + condições (ver abaixo) |
| | `iam:PassRole` | ARNs das roles `encode` e `judge` |
| | `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` | bucket do experimento |
| | `s3:DeleteObject` | `bucket/runs/*` (limpeza seletiva pós-Pass, ADR-0014) |
| | `ssm:GetParameter`, `kms:Decrypt` | parâmetro da chave SSH |
| **encode** | `s3:GetObject` | `bucket/masters/*`, `bucket/scenarios/*` |
| | `s3:PutObject` | `bucket/runs/*`, `bucket/status/*` |
| **judge** | `s3:GetObject` | `bucket/runs/*`, `bucket/masters/*`, `bucket/quality/plan.json` |
| | `s3:PutObject` | `bucket/quality/results/*`, `bucket/status/*` |

Os escopos seguem o layout-contrato de prefixos da ADR-0011. O Orquestrador precisa de S3 r/w/list porque faz o bootstrap dos Masters (ADR-0014), o `quality_triage.py` baixa `meta.json` e `output.sha256` de `runs/`, e o `resume.py` lista `runs/`. O `DeleteObject` existe pra exatamente um caso de uso — a limpeza seletiva pós-Pass (ADR-0014) — e é **escopado a `runs/*`**: deleção é a única operação destrutiva, e o escopo protege `masters/`, `scenarios/` e `quality/` de um path malformado no script de limpeza (mesmo instinto do PassRole escopado). O `PutObject` do Orquestrador segue bucket-wide deliberadamente: ele escreve em três prefixos (`masters/`, `scenarios/`, `quality/`) e escopar daria três statements por ganho marginal. **Não** precisa de permissão de Budgets — o budget alert (ADR-0012) é criado pelo Terraform e dispara email; o Orquestrador não o consulta.

### PassRole

`run-instances --iam-instance-profile` exige que a role `orchestrator` tenha `iam:PassRole` sobre a role passada. Escopado às ARNs de `encode` e `judge` (não `*`) pra limitar o blast radius se a instância do Orquestrador — que tem IP público (ADR-0015) — for comprometida. O Terraform já conhece as ARNs que cria, então o escopo custa nada.

### Escopo do EC2 — condições de região e tipo

`ec2:RunInstances`/`TerminateInstances` ficam com `Resource: "*"`, mas com duas condições:

- `aws:RequestedRegion = us-east-1`
- `ec2:InstanceType ∈ {c7g.xlarge, c7i.xlarge, c7a.xlarge, t3.micro, <tipo do Juiz>}`

A condição de InstanceType é a salvaguarda de **custo** sob comprometimento (reforça o budget de $150 da ADR-0012): mesmo invadido, o Orquestrador não consegue lançar uma instância cara. Não se usou escopo por tag (resource-level) — ver Considered Options.

## Chave SSH via SSM Parameter Store

A ADR-0010 usa SSH (não SSM Session Manager) como canal Orquestrador→instâncias. A chave **privada** precisa estar na instância do Orquestrador. O Terraform gera o par (`tls_private_key`), registra a pública (`aws_key_pair`) e guarda a privada como **SecureString** no SSM Parameter Store. No bootstrap, o Orquestrador lê via `ssm:GetParameter` + `kms:Decrypt` e grava em `~/.ssh/`. A chave nunca entra no repositório nem fica parada em S3.

## Invocação e ciclo de vida do Orquestrador

O Orquestrador é iniciado **manualmente dentro de um `tmux`** na sua instância: o pesquisador dá SSH, abre o tmux, roda `python orchestrator.py`, e faz detach. Sobrevive à desconexão de SSH e permite reattach pra observar/intervir ao vivo. **Sem auto-restart** — coerente com a retomada semi-automática e o gate humano da ADR-0012.

## Validação de fumaça

Antes da campanha, validar o IAM lançando uma instância descartável e confirmando que ela sobe, acessa S3 e termina. Pega `AccessDenied` opaco (ex.: PassRole, condição de InstanceType) antes de desperdiçar horas de compute.

## Considered Options

- **Credenciais estáticas (`aws configure`)** — rejeitado: chave de longa duração no disco de uma instância com IP público; risco de vazamento permanente. Instance profile rotaciona sozinho e não expõe segredo.
- **`PassRole` com `Resource: "*"`** — rejeitado: deixaria o Orquestrador passar qualquer role da conta a qualquer instância; escalonamento de privilégio desnecessário. ARNs específicas custam nada.
- **EC2 escopado por tag (resource-level, tag-on-launch)** — rejeitado pro threat model atual: conta pessoal de TCC, quase vazia; o dano realista é custo, não movimento lateral, e a condição de InstanceType já põe teto de custo. Tag-on-launch adiciona acoplamento de runtime (`--tag-specifications` tem que casar exatamente com a policy em cada tipo de recurso) e `AccessDenied` opaco durante a janela de 2 dias. Reconsiderar se a conta virar compartilhada, se a pipeline virar infra recorrente, ou se houver requisito de compliance.
- **EC2 wildcard puro (sem condições)** — rejeitado: por ~2 linhas de condição compra-se teto de custo real. Defensável num TCC, mas a InstanceType allowlist tem retorno alto pelo esforço quase nulo.
- **Chave SSH via S3** — rejeitado: chave privada parada num objeto S3 durante o experimento, sem ganho sobre SSM SecureString.
- **Chave SSH via `scp` manual** — rejeitado: passo manual que se repete a cada `resume.py` que recrie o Orquestrador; contraria o "provisiona uma vez" da ADR-0009.
- **systemd pro Orquestrador** — rejeitado: o auto-restart contraria o gate humano da ADR-0012 (reiniciaria no mesmo erro); e é mais setup.
- **`nohup` + arquivo de log** — rejeitado: sobrevive à desconexão mas não permite reattach interativo pra intervir ao vivo.

## Consequences

- O Terraform cria: 3 roles + instance profiles, a policy `orchestrator` com as condições de EC2, o key pair, e o parâmetro SSM SecureString da chave privada.
- A role `orchestrator` acumula três papéis de credencial: lançar/terminar instâncias (EC2), mover artefatos (S3), e ler a chave SSH (SSM/KMS).
- O **hop limit do IMDS no encode** foi resolvido na ADR-0018: a Execução roda dentro do container, então o `aws s3 cp` também — logo o `run-instances` do encode usa **hop limit 2** (`HttpPutResponseHopLimit=2`).
- A validação de fumaça é pré-requisito operacional antes de disparar a campanha.
