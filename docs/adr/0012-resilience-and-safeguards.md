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

## Retomada semi-automática

Quando algo falha (instância morre, cenários falham), a retomada é **semi-automática**:

1. Um script `resume.py` lista `s3://bucket/runs/`, parseia os `meta.json`, identifica quais cenários/replicações já completaram.
2. Gera novo `scenarios.json` sem os cenários completos.
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
