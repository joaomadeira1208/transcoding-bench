# IAM, credenciais e invocação do orquestrador

Define como cada instância autentica na AWS, com quais permissões, e como o Orquestrador é iniciado e mantido vivo durante a campanha (~46h+).

## Autenticação: instance profiles, sem chaves estáticas

As três instâncias (Orquestrador, encode, Juiz) recebem **IAM roles via instance profile** (Terraform). Nenhuma chave estática toca o disco. O AWS CLI obtém credenciais temporárias do IMDS, que **rotacionam sozinhas** durante toda a vida da instância — não há expiração a gerenciar. Como o Orquestrador chama a AWS CLI via `subprocess` (ADR-0009), cada chamada é um processo novo que lê o IMDS na hora e recebe credencial válida; o mesmo vale pro `aws s3 cp` das instâncias de encode ao longo das ~46h.

## Matriz de permissões

| Role | Permissões | Escopo |
|---|---|---|
| **orchestrator** | `ec2:RunInstances`, `ec2:DescribeInstances`, `ec2:DescribeInstanceStatus` | `*` + condições (ver abaixo) |
| | `ec2:TerminateInstances` | `*`, condicionado a `ec2:ResourceTag/role` nos papéis efêmeros (emenda abaixo) |
| | `iam:PassRole` | ARNs das roles `encode` e `judge` |
| | `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` | bucket do experimento |
| | `s3:DeleteObject` | `bucket/runs/*` (limpeza seletiva pós-Pass, ADR-0014) |
| | `ec2:CreateTags` | `*`, condicionado a `ec2:CreateAction = RunInstances` (emenda abaixo) |
| | `ssm:GetParameter`, `kms:Decrypt` | parâmetro da chave SSH |
| **encode** | `s3:GetObject` | `bucket/masters/*`, `bucket/scenarios/*` |
| | `s3:PutObject` | `bucket/runs/*`, `bucket/status/*` |
| **judge** | `s3:GetObject` | `bucket/runs/*`, `bucket/masters/*`, `bucket/quality/plan.json` |
| | `s3:PutObject` | `bucket/quality/results/*`, `bucket/status/*` |
| **masters** (emenda) | `s3:PutObject` | `bucket/masters/*` |
| | `s3:ListBucket` | bucket, condicionado ao prefixo `masters/` |

Os escopos seguem o layout-contrato de prefixos da ADR-0011. O Orquestrador precisa de S3 r/w/list porque faz o bootstrap dos Masters (ADR-0014), o `quality_triage.py` baixa `meta.json` e `output.sha256` de `runs/`, e o `resume.py` lista `runs/`. O `DeleteObject` existe pra exatamente um caso de uso — a limpeza seletiva pós-Pass (ADR-0014) — e é **escopado a `runs/*`**: deleção é a única operação destrutiva, e o escopo protege `masters/`, `scenarios/` e `quality/` de um path malformado no script de limpeza (mesmo instinto do PassRole escopado). O `PutObject` do Orquestrador segue bucket-wide deliberadamente: ele escreve em três prefixos (`masters/`, `scenarios/`, `quality/`) e escopar daria três statements por ganho marginal. **Não** precisa de permissão de Budgets — o budget alert (ADR-0012) é criado pelo Terraform e dispara email; o Orquestrador não o consulta.

**Emenda: um quarto papel, `masters`.** A preparação dos masters roda numa instância efêmera própria (ADR-0014), e ela recebe papel próprio em vez de herdar o do Orquestrador: só escreve em `masters/`, e dar a uma instância de 1–2 h o `DeleteObject` de `runs/` e o `RunInstances` do Orquestrador seria blast radius sem função. O `PutObject` bucket-wide do Orquestrador continua como está — ele segue escrevendo `scenarios/` e `quality/`, e o argumento de "escopar daria statements por ganho marginal" não muda por um prefixo a menos. A condição de `InstanceType` abaixo já inclui `c7g.xlarge`, que é o tipo da instância de preparação; nenhum tipo novo entra na lista.

**Emenda: `ec2:CreateTags` na criação, e só nela.** A matriz acima não listava a ação, e a spec #40 (D24) manda taggear toda instância efêmera com `role` e `commit`. `run-instances --tag-specifications` não é tagueamento posterior: a EC2 autoriza `ec2:CreateTags` **dentro** da mesma chamada, e sem a ação o lançamento inteiro é recusado com `UnauthorizedOperation` — nenhuma instância sobe. A ação entra com `Resource: "*"`, a condição de região das demais, e `ec2:CreateAction = RunInstances`, que a prende ao caminho do lançamento: taggear uma instância já existente da conta continua negado.

Isto **não reverte** a opção rejeitada "EC2 escopado por tag (resource-level, tag-on-launch)" abaixo. O que aquela rejeição recusou foi a tag como **chave de autorização** — condições de `ec2:ResourceTag`/`aws:RequestTag` que a policy tem de casar por tipo de recurso, com o acoplamento de runtime e o `AccessDenied` opaco que ela nomeia. O que o D24 traz de volta é a tag como **atributo**: atribuição de custo e identificação de instância órfã. Nenhuma condição desta policy lê o valor de uma tag; `ec2:CreateAction` é sobre qual chamada está criando o recurso, não sobre o que a tag diz. A rejeição segue de pé para esta emenda; quem a revisita em parte, e por outro argumento, é a emenda seguinte.

**Emenda: `ec2:TerminateInstances` escopado por tag, e o argumento é resiliência.** A ação sai do statement do `RunInstances` para um próprio, com a condição `ec2:ResourceTag/role ∈ {encode, judge, masters}`. Sem ela a policy termina qualquer instância de tipo permitido em us-east-1 — inclusive a t3.micro em que o próprio Orquestrador roda.

O argumento **não é segurança**. Os gatilhos que a rejeição de "EC2 escopado por tag" escreveu para si mesma — conta compartilhada, pipeline virando infra recorrente, requisito de compliance — não dispararam, e enquadrar a mudança como segurança seria reabrir a decisão pelos critérios que ela própria definiu e que seguem não atendidos. O argumento é **resiliência**: um `instance_ids` malformado mata o Orquestrador no meio de uma campanha de 46 h. É a mesma classe de bug da qual o `DeleteObject` escopado a `runs/*` já defende ("path malformado no script de limpeza"), e a terceira aplicação de um princípio que esta ADR já adotou duas vezes — `PassRole` escopado, `DeleteObject` escopado.

O custo de escopar caiu por dois motivos, e é por isso que a conta muda de sinal agora. A tag passou a existir, pela emenda do `CreateTags`. E o tagueamento é **atômico com o lançamento**: se o `CreateTags` falhar, o `RunInstances` inteiro é revertido, então não existe efêmera rodando sem tag — o medo óbvio de escopar por tag, uma órfã interminável queimando dinheiro por 46 h, não se materializa neste desenho. O acoplamento de runtime que a rejeição nomeia continua real, mas já está pago: `--tag-specifications` tem de casar com a policy desde o `CreateTags`, e escopar o terminate não acrescenta um segundo lugar onde isso possa divergir.

A invariante que faz a proteção existir: **a instância do Orquestrador não pode cair no conjunto de papéis efêmeros**. Ela nasce com a tag `role = orchestrator`, que não está entre os valores da condição — e é só isso que a torna interminável por ela mesma.

A ADR previu este modo de falha — "`--tag-specifications` tem que casar exatamente com a policy" e "`AccessDenied` opaco durante a janela de 2 dias" — e ele se materializou pela omissão da ação, não pela adoção do escopo por tag. A correção é conceder a ação, e ela é exatamente o tipo de erro que a validação de fumaça abaixo existe pra pegar.

### PassRole

`run-instances --iam-instance-profile` exige que a role `orchestrator` tenha `iam:PassRole` sobre a role passada. Escopado às ARNs de `encode`, `judge` e `masters` (não `*`) pra limitar o blast radius se a instância do Orquestrador — que tem IP público (ADR-0015) — for comprometida. O Terraform já conhece as ARNs que cria, então o escopo custa nada.

### Escopo do EC2 — condições de região, tipo e tag

`ec2:RunInstances` fica com `Resource: "*"`, mas com duas condições:

- `aws:RequestedRegion = us-east-1`
- `ec2:InstanceType ∈ {c7g.xlarge, c7i.xlarge, c7a.xlarge, t3.micro, <tipo do Juiz>}`

`TerminateInstances` ficava no mesmo statement e ganhou o seu, com a região e a condição de tag da emenda acima.

A condição de InstanceType é a salvaguarda de **custo** sob comprometimento (reforça o budget de $150 da ADR-0012): mesmo invadido, o Orquestrador não consegue lançar uma instância cara. Escopo por tag (resource-level) entra só no terminate, e por resiliência — ver a emenda acima e Considered Options.

### Dois buckets, uma matriz

**Emenda.** O piloto tem bucket próprio (ADR-0011). Toda linha da matriz que diz "bucket" vale para os **dois** ARNs, com os mesmos prefixos: o Terraform conhece os dois e cada statement lista os dois. Nenhum papel novo, nenhuma permissão nova — só o recurso duplicado. Um papel por bucket foi rejeitado: dobraria a matriz por nenhum ganho, já que o mesmo código roda contra os dois.

## Chave SSH via SSM Parameter Store

A ADR-0010 usa SSH (não SSM Session Manager) como canal Orquestrador→instâncias. A chave **privada** precisa estar na instância do Orquestrador. O Terraform gera o par (`tls_private_key`), registra a pública (`aws_key_pair`) e guarda a privada como **SecureString** no SSM Parameter Store. No bootstrap, o Orquestrador lê via `ssm:GetParameter` + `kms:Decrypt` e grava em `~/.ssh/`. A chave nunca entra no repositório nem fica parada em S3.

## Invocação e ciclo de vida do Orquestrador

O Orquestrador é iniciado **manualmente dentro de um `tmux`** na sua instância: o pesquisador dá SSH, abre o tmux, roda `python orchestrator.py`, e faz detach. Sobrevive à desconexão de SSH e permite reattach pra observar/intervir ao vivo. **Sem auto-restart** — coerente com a retomada semi-automática e o gate humano da ADR-0012.

## Validação de fumaça

Antes da campanha, validar o IAM lançando uma instância descartável e confirmando que ela sobe, acessa S3 e termina. Pega `AccessDenied` opaco (ex.: PassRole, condição de InstanceType) antes de desperdiçar horas de compute.

O escopo dessa validação foi **ampliado pela ADR-0022** (e já vinha sendo, pela ADR-0021, que mandou incluir o caminho do clone): em vez de só "sobe, toca S3, termina", ela roda o **caminho completo numa `c7g.xlarge`** — `run-instances` → clone + `git checkout <sha>` → `docker build` → um `run_scenario.sh` com `perf` real → upload → `quality_triage.py` → Juiz → terminate, sobre um clip curto — mais uma **verificação dos eventos PMU** (ADR-0006) nas outras duas arquiteturas. É a primeira e única vez que PassRole, condição de InstanceType, chave via SSM, hop limit 2 do IMDS, `-march=native` e `perf` dentro do container rodam juntos antes de a campanha valer dado.

## Considered Options

- **Credenciais estáticas (`aws configure`)** — rejeitado: chave de longa duração no disco de uma instância com IP público; risco de vazamento permanente. Instance profile rotaciona sozinho e não expõe segredo.
- **`PassRole` com `Resource: "*"`** — rejeitado: deixaria o Orquestrador passar qualquer role da conta a qualquer instância; escalonamento de privilégio desnecessário. ARNs específicas custam nada.
- **EC2 escopado por tag (resource-level, tag-on-launch)** — rejeitado pro threat model atual: conta pessoal de TCC, quase vazia; o dano realista é custo, não movimento lateral, e a condição de InstanceType já põe teto de custo. Tag-on-launch adiciona acoplamento de runtime (`--tag-specifications` tem que casar exatamente com a policy em cada tipo de recurso) e `AccessDenied` opaco durante a janela de 2 dias. Reconsiderar se a conta virar compartilhada, se a pipeline virar infra recorrente, ou se houver requisito de compliance. **Parcialmente revisto** pela emenda do `TerminateInstances`: a condição de tag entra numa ação só, por resiliência e não por threat model — nenhum dos gatilhos acima disparou. `RunInstances` e os `Describe` seguem sem escopo por tag, pelos motivos originais.
- **EC2 wildcard puro (sem condições)** — rejeitado: por ~2 linhas de condição compra-se teto de custo real. Defensável num TCC, mas a InstanceType allowlist tem retorno alto pelo esforço quase nulo.
- **Chave SSH via S3** — rejeitado: chave privada parada num objeto S3 durante o experimento, sem ganho sobre SSM SecureString.
- **Chave SSH via `scp` manual** — rejeitado: passo manual que se repete a cada `resume.py` que recrie o Orquestrador; contraria o "provisiona uma vez" da ADR-0009.
- **systemd pro Orquestrador** — rejeitado: o auto-restart contraria o gate humano da ADR-0012 (reiniciaria no mesmo erro); e é mais setup.
- **`nohup` + arquivo de log** — rejeitado: sobrevive à desconexão mas não permite reattach interativo pra intervir ao vivo.

## Consequences

- O Terraform cria: 4 roles + instance profiles (a emenda do `masters`), a policy `orchestrator` com as condições de EC2 — região, `InstanceType`, `CreateAction` e a tag `role` no terminate —, o key pair, e o parâmetro SSM SecureString da chave privada.
- A role `orchestrator` acumula três papéis de credencial: lançar/terminar instâncias (EC2), mover artefatos (S3), e ler a chave SSH (SSM/KMS).
- O **hop limit do IMDS no encode** foi resolvido na ADR-0018: a Execução roda dentro do container, então o `aws s3 cp` também — logo o `run-instances` do encode usa **hop limit 2** (`HttpPutResponseHopLimit=2`).
- A validação de fumaça é pré-requisito operacional antes de disparar a campanha.
