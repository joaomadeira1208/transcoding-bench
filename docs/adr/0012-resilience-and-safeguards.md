# Resiliência e salvaguardas operacionais

O experimento leva ~46h por instância (~2 dias). Três camadas de proteção garantem que falhas não desperdiçam dinheiro nem tempo de forma catastrófica.

## Camada 1 — Timeout por run

Cada invocação de `run_scenario.sh` é envolvida por `timeout`:
```
timeout 4h bash run_scenario.sh ...
```
O encode mais longo estimado (libx265, 4K→4K, ~10 min de vídeo) leva ~60 min. Timeout de 4h é generoso — se estourar, o run é marcado como falho e o loop segue pro próximo cenário.

## Camada 2 — Timeout total da instância

`run_all.sh` registra timestamp de início. Antes de cada cenário, checa se o tempo total excedeu **72h** (esperado ~46h). Se sim, faz upload do que tem pro S3 e para. Evita instância zumbi rodando indefinidamente.

## Camada 3 — Budget alert AWS

Terraform configura um AWS Budget com teto de **$150**. Se o custo acumulado ultrapassar, o proprietário recebe email. Não mata instâncias automaticamente, mas alerta.

Estimativa de custo normal do experimento: ~$70 de compute + ~$4 de S3 + instância do Orquestrador + Juiz ≈ ~$85. Teto de $150 dá margem pra um re-run completo.

**Emenda: o teto mede custo bruto.** A estimativa acima é de consumo — o que o experimento gasta —, e é sobre ela que os $150 foram calibrados. O default do AWS Budgets mede outra coisa: `include_credit` e `include_refund` vêm `true`, e o teto passa a valer sobre o custo **líquido**, já descontado o crédito. A conta tem crédito promocional ativo, então com o default o alerta ficaria mudo exatamente durante o smoke e o piloto, que são as duas janelas em que ele serviria pra alguma coisa — e voltaria a falar só quando o crédito acabasse, com o consumo real já bem acima do teto.

Por isso o `aws_budgets_budget` declara o bloco `cost_types` por inteiro, com `include_credit = false` e `include_refund = false`. É o teto medindo o que a estimativa mediu. O efeito colateral aceito é que o valor do alerta não é a fatura: se o crédito cobrir tudo, o email chega e nada é cobrado. Trocar em favor do líquido é decidir que o alerta serve pra prever a fatura em vez de vigiar o consumo — o que este ADR não quer, porque o dano que ele existe pra evitar (uma instância esquecida por 46 h) é consumo, e o crédito é finito.

## Gate humano antes da campanha

**Emenda.** As três camadas acima protegem a campanha em andamento. Antes de ela começar há um gate que nenhuma camada substitui: a preparação dos masters (ADR-0014) é uma execução própria, e a campanha só é disparada depois que o pesquisador confere o `masters/manifest.json` (ADR-0011) contra as ADRs 0004 e 0023 e aprova. É o mesmo instinto da retomada semi-automática abaixo e do gate de hotfix da ADR-0021: o que custa dois dias de compute se estiver errado passa por um humano uma vez.

**Emenda: o segundo gate é o piloto.** Depois dos masters e do smoke AWS, roda um piloto — a campanha em escopo menor, pelo mesmo código (ADR-0022) — e a campanha só é disparada depois que o pesquisador confere a checklist do piloto e a registra num relatório commitado. O piloto é também onde os limites desta ADR ganham um número: o tempo por bloco medido nele, extrapolado para a matriz inteira, tem de caber em 60 h por arquitetura (margem sobre o teto de 72 h) e no orçamento de $150; se não couber, a decisão é tomada antes, e não pelo timeout na hora 40. Um hotfix entre o piloto e a campanha segue as classes da ADR-0021: classe 1 repete o piloto, classe 2 repete só o smoke AWS. **Emenda:** o smoke AWS saiu da escada (ADR-0022) e o degrau que a classe 2 repete é o `preflight`.

## Retomada semi-automática

Quando algo falha (instância morre, cenários falham), a retomada é **semi-automática**:

1. Um script `resume.py` lista `s3://bucket/runs/`, parseia os `meta.json`, identifica quais cenários completaram — completude é por **bloco**: as 5 reps com `exit_code == 0` (ADR-0019).
2. Gera novo `scenarios.json` sem os cenários completos. Bloco parcial volta **inteiro** (warm-up novo + 5 reps, novos `run_id`): re-executar só as reps faltantes numa instância recém-criada rodaria a frio, perdendo a estabilização que o warm-up garante (ADR-0003). Os runs do bloco interrompido ficam no S3 como registro forense; os leitores deduplicam por `scenario_id` ("último `started_at` vence", ADR-0019).
3. Se a instância morreu: `aws ec2 run-instances` recria (ou `terraform apply` se infra base foi afetada).
4. Orquestrador dispara `run_all.sh` com o `scenarios.json` reduzido.

Intervenção humana é necessária pra avaliar se o erro é transitório (vale retentar) ou permanente (precisa investigar). Isso é intencional — evita retry loops automáticos que repetem o mesmo erro.

**Emenda: a forma da retomada.** O passo 1 acima dizia "lista `s3://bucket/runs/`", e a listagem é justamente o que a retomada não faz: o `runs/` de uma campanha passa de mil objetos, e o parser do `list-objects-v2` recusa páginas truncadas por desenho (ADR-0019). No lugar dela, um `aws s3 sync` bucket→disco filtrado em `*/meta.json` baixa todos os `meta.json` de uma vez, e **o diretório sincronizado é a enumeração**. Cada arquivo passa pelo checador do `meta.json` do Orquestrador antes de entrar na decisão, e um inválido derruba a retomada nomeando a chave no bucket — deixar passar um `warmup` mal escrito faria um warm-up entrar como Replicação, e um `exit_code` mal escrito faria uma falha contar como sucesso (ADR-0019/0022).

A Execução cujo upload a morte da instância interrompeu antes do `meta.json` **não aparece**: o filtro não traz os outros artefatos dela, e sem arquivo nenhum não há diretório local. Isso não é perda, e sim a decisão certa tomada de graça — uma Execução sem `meta.json` não pode estar completa, e o bloco dela já cai em pendente por ausência. O que o leitor ainda faz, quando encontra um diretório de run sem o arquivo, é ignorá-lo com um aviso em vez de estourar: a alternativa é a árvore inteira virar erro de leitura por causa de um objeto que não decide nada.

**A retomada decide; ela não executa.** São dois comandos, e a separação é o gate humano:

```
python orchestrator/resume.py --config config/experiment.toml \
    --bucket <campanha> --out ~/work/resume [--exclude-commit <sha>]...
python orchestrator/orchestrator.py --infra ~/work/infra.json run \
    --config config/experiment.toml --bucket <campanha> --slices ~/work/resume
```

O primeiro imprime, por arquitetura, os blocos completos e os pendentes com o motivo — **ausente**, **parcial**, **com falha**, **excluído por commit** — e escreve em `--out` uma fatia reduzida por arquitetura com pendência. Arquitetura sem pendência não ganha arquivo, e nada pendente é status zero com diretório vazio: "não há o que retomar" é um resultado, não um erro. Isso faz do `--out` um diretório novo a cada retomada, e o CLI recusa um que já contenha fatias: o segundo comando sobe toda arquitetura presente no diretório, e a fatia deixada pela retomada anterior mandaria refazer justamente a arquitetura que desta vez saiu completa — com o relatório ao lado dizendo que não há o que retomar. Ele **não lança nada** — entre saber o que falta e pagar por isso há um humano lendo o relatório, pelo mesmo instinto do gate do manifesto e do gate do piloto.

**`--exclude-commit` é a porta do hotfix classe 1.** O `meta.json` registra o SHA de cada Execução, e a ADR-0021 define que um fix que toca o caminho de medição contamina o que já rodou nele. A flag, repetível, torna pendente todo bloco com qualquer Replicação **vencedora** naquele commit. Sem ela, invalidar blocos depois de um hotfix seria apagar objetos à mão no console — o que apaga também a evidência forense de que eles existiram.

O valor tem de ser o SHA **completo**, os 40 dígitos que o `meta.json` registra, e o CLI recusa qualquer outra coisa antes de sincronizar. A comparação é de igualdade sobre aquele campo, e a abreviação de sete dígitos que o `git log --oneline` mostra — que é de onde o pesquisador copia o SHA — não casaria com Execução nenhuma: a retomada sairia com status zero declarando completos exatamente os blocos que o hotfix contaminou. Recusar alto o argumento é a única janela em que esse erro é detectável.

**A fatia reduzida sobrescreve `scenarios/{id}.json`, e o `canonical.json` não é tocado.** A fatia significa "o que esta arquitetura deve rodar agora", e é exatamente isso que a retomada recalcula; o registro do Experimento é o canônico, e reescrevê-lo apagaria a matriz contra a qual a completude é medida. Nomes por tentativa (`{id}-resume-2.json`) foram rejeitados: o `meta.json` de cada Execução já carrega o que rodou e em que commit, e um nome novo por retomada só compraria um `--plan-key` variável para o bash da instância.

## Considered Options

- **Sem retomada** — rejeitado: 2 dias de experimento sem rede de segurança é arriscado. Refazer tudo por causa de uma falha no cenário 30 é desperdício.
- **Retomada totalmente automática** — rejeitado: complexidade desproporcional pra algo que roda uma vez. Risco de retry infinito se o erro for permanente. Custo de compute dos re-runs é idêntico ao semi-automático — a diferença é só tempo de reação humana, não dinheiro.
- **Timeout mais agressivo (2h por run)** — rejeitado: não há certeza dos tempos em 4 vCPU; margem generosa evita falsos positivos que desperdiçam runs válidos.

## Consequences

- Orquestrador termina instâncias via `aws ec2 terminate-instances` como último passo automático de cada fase. As três camadas são fallback pro caso de o próprio orquestrador falhar.
- `resume.py` depende do S3 como fonte de verdade do progresso — design consistente com ADR-0011 (upload imediato após cada run).
- O custo de uma falha não-detectada por 12h (ex.: instância zumbi idle overnight) é ~$2 (0.17/h × 12h). Inconveniente, não catastrófico.
