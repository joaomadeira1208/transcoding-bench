# smoke/tests/

O seam do `smoke/`: um só, cobrindo os scripts do `encode/` que rodam com shims,
a forma do `meta.json`, o layout de `runs/{run_id}/` e os prefixos da ADR-0011
(ADR-0022).

Um módulo por script: `test_run_scenario.py` dirige uma Execução,
`test_run_all.py` dirige o laço sobre um plano de um bloco e
`test_fetch_masters.py` dirige o download dos Masters contra o manifesto. A
asserção central é a do argv — do FFmpeg nos dois primeiros, e do `aws` no
terceiro, porque o papel do encode não tem `ListBucket` na matriz da ADR-0016 e
por isso o manifesto **é** a lista. O manifesto que aquele módulo monta passa
pela CLI do contrato antes de ser usado: um campo renomeado lá quebra o teste em
vez de deixá-lo verde contra uma forma que a preparação não escreve mais. O
`test_pilot_block.py` repete o que aquele laço assere, com o plano do piloto no
lugar do da campanha, e acrescenta o argv contra o `config/pilot.toml` e a
consolidação da árvore que o bloco produziu.

O `test_acceptance.py` é de outra natureza e por isso fica de fora do default:
ele builda a imagem e roda as ferramentas de verdade dentro dela, e só é coletado
com `--docker` (ver o README do papel).
