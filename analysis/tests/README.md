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
