# smoke/tests/

O seam do `smoke/`: um só, cobrindo os `run_*.sh`, o `masters/prepare.sh`, a
forma do `meta.json` e do `masters/manifest.json`, o layout de `runs/{run_id}/` e
os prefixos da ADR-0011 (ADR-0022).

Um módulo por script: `test_run_scenario.py` dirige uma Execução,
`test_run_all.py` dirige o laço sobre um plano de um bloco e
`test_prepare_masters.py` dirige a preparação dos seis Masters, do download ao
manifesto. A asserção central é a do argv, e o porquê dela está no cabeçalho do
módulo que a faz. O `test_pilot_block.py` repete o que aquele laço assere, com o
plano do piloto no lugar do da campanha, e acrescenta o argv contra o
`config/pilot.toml` e a consolidação da árvore que o bloco produziu.

O `test_acceptance.py` é de outra natureza e por isso fica de fora do default:
ele builda a imagem e roda as ferramentas de verdade dentro dela, e só é coletado
com `--docker` (ver o README do papel).
