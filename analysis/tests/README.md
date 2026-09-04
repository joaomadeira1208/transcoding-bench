# analysis/tests/

Testes do papel `analysis/` (pytest), co-localizados com o papel que os possui
(ADR-0017). Rodam no Mac e no CI, nunca nas instâncias.

Ganha teste o que pode falhar **em silêncio** (ADR-0022). No contrato do
`meta.json` isso é o arquivo inteiro: `"warmup": "false"` coagido para `False`
põe o warm-up na média, um `started_at` sem offset faz a dedup ordenar strings, e
um `schema_version` desconhecido passando batido mistura duas formas de arquivo
na mesma tabela. Nenhum deles estoura sozinho, então cada um tem um teste de
rejeição próprio.

A factory do `conftest.py` é âncora fraca por construção — é Python validando
Python. A âncora forte do contrato cross-language é um `meta.json` que o bash
produziu, e ela chega com o smoke (ADR-0022); o `test_meta_agreement.py`, que
mora igual aqui e no `orchestrator/`, é o que garante enquanto isso que os dois
leitores não divergem sobre o que é um arquivo válido.

Na tabela consolidada, o critério aponta para as mesmas quatro regras: warm-up
filtrado pela string em vez do campo, dedup ordenando timestamps como texto,
denominador zero virando `NaN` no meio de uma média, e um run falho sumindo da
tabela em vez de aparecer com o seu `exit_code`. Nenhuma delas estoura; todas
mudam o que o artigo reporta.

Os parsers dos artefatos de instrumentação são testados contra a factory pelo
mesmo motivo e com a mesma ressalva: uma chave renomeada no format string do
`/usr/bin/time` ou um `%CPU` que mudou de coluna no `pidstat` esvaziam uma coluna
sem derrubar nada. A âncora forte deles é o `smoke/`, que consolida a árvore
recém-escrita pelo `run_all.sh`.
