# orchestrator/tests/

Testes do papel `orchestrator/` (pytest), co-localizados com o papel que os
possui (ADR-0017). Rodam no Mac e no CI, nunca nas instâncias.

Ganha teste o que pode falhar **em silêncio** (ADR-0022) — cardinalidade e
unicidade do plano, shuffle com seed, fatiamento, formação da `scenario_id`,
completude por bloco, filtro `warmup == false`, a guarda de subconjunto entre as
duas definições de `config/`, a checagem do `meta.json` e do
`masters/manifest.json` que o bash escreve à mão, o veredito sobre os dois
objetos de `status/` e a linha de progresso que sai deles, o núcleo do
`preflight` (o veredito sobre o `perf stat`, a lista de eventos que ele pede e a
tabela), o do lançamento (a AMI da arquitetura pedida e as tags da instância), a
precedência da decisão de vigilância, a leitura do arquivo de estado, o núcleo
do `run` (as duas guardas — o `runs/` povoado e o arquivo de estado com
instância de pé —, a projeção do `--slices` e a decisão depois dos bootstraps),
o parser do PID que volta do disparo, o núcleo da vigilância (o prazo do
Orquestrador, o veredito que decide o código de saída, o resumo final e a linha
de cada poll) e o núcleo do triage do Pass de qualidade (o agrupamento por
Cenário, a contagem de bitstreams distintos, a escolha determinística do
representante, a célula divergente e o leitor do plano que o Juiz e a retenção
usam).
Invariantes escritas à mão como default; golden inline no `.py` só onde congelar
*é* o requisito.
