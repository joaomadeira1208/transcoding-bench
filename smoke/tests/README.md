# smoke/tests/

O seam do `smoke/`: um só, cobrindo os `run_*.sh`, a forma do `meta.json`, o
layout de `runs/{run_id}/` e os prefixos da ADR-0011 (ADR-0022).

Dois módulos, um por script: `test_run_scenario.py` dirige uma Execução e
`test_run_all.py` dirige o laço sobre um plano de um bloco. A asserção central
é a do argv do FFmpeg, e o porquê dela está no cabeçalho do módulo que a faz.

O `test_acceptance.py` é de outra natureza e por isso fica de fora do default:
ele builda a imagem e roda as ferramentas de verdade dentro dela, e só é coletado
com `--docker` (ver o README do papel).
