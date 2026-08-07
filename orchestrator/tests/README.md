# orchestrator/tests/

Testes do papel `orchestrator/` (pytest), co-localizados com o papel que os
possui (ADR-0017). Rodam no Mac e no CI, nunca nas instâncias.

Ganha teste o que pode falhar **em silêncio** (ADR-0022) — cardinalidade e
unicidade do plano, shuffle com seed, fatiamento, formação da `scenario_id`,
completude por bloco, filtro `warmup == false`. Invariantes escritas à mão como
default; golden inline no `.py` só onde congelar *é* o requisito.
