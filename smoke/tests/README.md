# smoke/tests/

O seam do `smoke/`: um só, cobrindo os scripts do `encode/` que rodam com shims,
o `masters/prepare.sh`, a forma do `meta.json` e do `masters/manifest.json`, o
layout de `runs/{run_id}/` e os prefixos da ADR-0011 (ADR-0022).

Um módulo por script: `test_run_scenario.py` dirige uma Execução,
`test_run_all.py` dirige o laço sobre um plano de um bloco, `test_fetch_masters.py`
dirige o download dos Masters contra o manifesto e `test_prepare_masters.py`
dirige a preparação desses mesmos seis, do download ao manifesto. A asserção
central é a do argv — do FFmpeg nos dois primeiros e na preparação, e do `aws` no
`test_fetch_masters.py`, porque o papel do encode não tem `ListBucket` na matriz
da ADR-0016 e por isso o manifesto **é** a lista.

Os dois módulos dos Masters fecham o contrato pelos dois lados, e pela mesma CLI:
o `test_fetch_masters.py` monta um manifesto e o confere antes de usá-lo — um
campo renomeado lá quebra o teste em vez de deixá-lo verde contra uma forma que a
preparação não escreve mais —, e o `test_prepare_masters.py` confere o manifesto
que o bash de verdade escreveu. Cada um contra a sua spec, que é por que a CLI do
contrato recebe o config do chamador.

Dois módulos dirigem um papel que não é o do encode, os dois sobre o bucket falso
que o laço acabou de escrever. O `test_resume.py` dirige o `resume.py` do
Orquestrador, e a asserção central ali é a do argv do `s3 sync` e a do relatório
— quais blocos voltam e por quê. O `test_quality_triage.py` dirige o
`quality_triage.py` sobre um bucket que três laços encheram, um por arquitetura,
e a asserção central é a do plano — quantos bitstreams distintos cada Cenário
deu, qual Execução representa cada um e quais compartilham (ver o README do
papel).

O `test_pilot_block.py` repete o que aquele laço assere, com o plano do piloto no
lugar do da campanha, e acrescenta o argv contra o `config/pilot.toml` e a
consolidação da árvore que o bloco produziu.

O `test_acceptance.py` é de outra natureza e por isso fica de fora do default:
ele builda a imagem e roda as ferramentas de verdade dentro dela, e só é coletado
com `--docker` (ver o README do papel).
