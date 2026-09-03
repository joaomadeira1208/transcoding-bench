# smoke/tests/

O seam do `smoke/`: um só, cobrindo os `run_*.sh`, a forma do `meta.json`, o
layout de `runs/{run_id}/` e os prefixos da ADR-0011 (ADR-0022).

Dois módulos, um por script: `test_run_scenario.py` dirige uma Execução e
`test_run_all.py` dirige o laço sobre um plano de um bloco. A asserção central
é a do argv do FFmpeg, e o porquê dela está no cabeçalho do módulo que a faz.
