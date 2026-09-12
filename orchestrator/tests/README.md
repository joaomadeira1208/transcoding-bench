# orchestrator/tests/

Testes do papel `orchestrator/` (pytest), co-localizados com o papel que os
possui (ADR-0017). Rodam no Mac e no CI, nunca nas instâncias.

Ganha teste o que pode falhar **em silêncio** (ADR-0022) — cardinalidade e
unicidade do plano, shuffle com seed, fatiamento, formação da `scenario_id`,
completude por bloco, filtro `warmup == false`, a guarda de subconjunto entre as
duas definições de `config/`, a checagem do `meta.json` e do
`masters/manifest.json` que o bash escreve à mão, o núcleo do `preflight` (o
veredito sobre o `perf stat`, a lista de eventos que ele pede e a tabela) e o do
lançamento (a AMI da arquitetura pedida e as tags da instância). Invariantes
escritas à mão como default; golden inline no `.py` só onde congelar *é* o
requisito.
