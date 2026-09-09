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

**Emenda: o segundo gate é o piloto.** Depois dos masters e do smoke AWS, roda um piloto — a campanha em escopo menor, pelo mesmo código (ADR-0022) — e a campanha só é disparada depois que o pesquisador confere a checklist do piloto e a registra num relatório commitado. O piloto é também onde os limites desta ADR ganham um número: o tempo por bloco medido nele, extrapolado para a matriz inteira, tem de caber em 60 h por arquitetura (margem sobre o teto de 72 h) e no orçamento de $150; se não couber, a decisão é tomada antes, e não pelo timeout na hora 40. Um hotfix entre o piloto e a campanha segue as classes da ADR-0021: classe 1 repete o piloto, classe 2 repete só o smoke AWS.

## Retomada semi-automática

Quando algo falha (instância morre, cenários falham), a retomada é **semi-automática**:

1. Um script `resume.py` lista `s3://bucket/runs/`, parseia os `meta.json`, identifica quais cenários completaram — completude é por **bloco**: as 5 reps com `exit_code == 0` (ADR-0019).
2. Gera novo `scenarios.json` sem os cenários completos. Bloco parcial volta **inteiro** (warm-up novo + 5 reps, novos `run_id`): re-executar só as reps faltantes numa instância recém-criada rodaria a frio, perdendo a estabilização que o warm-up garante (ADR-0003). Os runs do bloco interrompido ficam no S3 como registro forense; os leitores deduplicam por `scenario_id` ("último `started_at` vence", ADR-0019).
3. Se a instância morreu: `aws ec2 run-instances` recria (ou `terraform apply` se infra base foi afetada).
4. Orquestrador dispara `run_all.sh` com o `scenarios.json` reduzido.

Intervenção humana é necessária pra avaliar se o erro é transitório (vale retentar) ou permanente (precisa investigar). Isso é intencional — evita retry loops automáticos que repetem o mesmo erro.

## Considered Options

- **Sem retomada** — rejeitado: 2 dias de experimento sem rede de segurança é arriscado. Refazer tudo por causa de uma falha no cenário 30 é desperdício.
- **Retomada totalmente automática** — rejeitado: complexidade desproporcional pra algo que roda uma vez. Risco de retry infinito se o erro for permanente. Custo de compute dos re-runs é idêntico ao semi-automático — a diferença é só tempo de reação humana, não dinheiro.
- **Timeout mais agressivo (2h por run)** — rejeitado: não há certeza dos tempos em 4 vCPU; margem generosa evita falsos positivos que desperdiçam runs válidos.

## Consequences

- Orquestrador termina instâncias via `aws ec2 terminate-instances` como último passo automático de cada fase. As três camadas são fallback pro caso de o próprio orquestrador falhar.
- `resume.py` depende do S3 como fonte de verdade do progresso — design consistente com ADR-0011 (upload imediato após cada run).
- O custo de uma falha não-detectada por 12h (ex.: instância zumbi idle overnight) é ~$2 (0.17/h × 12h). Inconveniente, não catastrófico.
