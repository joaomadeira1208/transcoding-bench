# analysis/tests/fixtures/

As saídas **cruas** que o `/usr/bin/time`, o `perf`, o `pidstat` e o FFmpeg de
verdade escreveram dentro da imagem de medição, um conjunto por encoder. Elas são
a âncora dos parsers de `run_artifacts.py`: uma fixture escrita à mão seria o
autor do parser adivinhando o que a ferramenta emite, e valida o Python contra o
Python (ADR-0022).

Quem as produz é a camada de aceite do `smoke/`, e é de lá que se as regenera:

    .venv-smoke/bin/python -m pytest smoke/ --docker \
        --capture-dir=analysis/tests/fixtures

A hora de fazer isso é quando um pin do `docker/Dockerfile` muda — é o que faz
uma saída mudar de forma.

Os contadores de hardware do `perf.json` vêm `<not supported>`: quem captura é o
Mac, e o Docker não expõe a PMU ao guest. É o texto real de um evento
indisponível, que é justamente o que o parser precisa atravessar; se cada evento
retorna valor em cada arquitetura, e se os pares abrem em grupo, é pergunta do
`preflight` (ADR-0022) — o aceite passa os eventos soltos, porque um grupo cujo
líder não abre é fatal para o `perf stat`.

A allowlist do `.gitignore` admite `.json`, `.txt` e `.log` sob um diretório
`fixtures/` (ADR-0017), e é por isso que o `output.mkv` da mesma captura não está
aqui.

## `campaign_meta.json`

O `meta.json` que o `run_scenario.sh` escreveu na **primeira Execução real do
projeto** — a `rep1` do primeiro bloco da `c7a` no piloto, em `0233e0f`. É a
âncora do contrato cross-language que a ADR-0022 pede: o `meta.json` é escrito
por bash montando JSON à mão e lido pelo modelo pydantic, e uma fixture escrita
em Python valida o Python contra o Python. O warm-up do mesmo bloco não serve —
ele tem `warmup: true`, e a âncora tem de exercer o caminho que a análise
consome.

Não se a regenera com o `smoke/`: ela vem de uma campanha, e a próxima só se ela
mudar de forma — isto é, se o `schema_version` subir.

Quem a lê é o `test_meta_agreement.py` de cada papel: o deste, pelo `load_meta`
estrito, e o do `orchestrator/`, pelo caminho, pelo `check_meta`. Um arquivo que
nenhum teste abre não ancora nada.

**O `instance_id` é o único campo trocado**, pelo mesmo `i-0123456789abcdef0` que
as factories usam, seguindo o scrub de `orchestrator/tests/fixtures/README.md`.
O modelo o declara `NonEmptyStr`, sem formato, então o placeholder exercita a
mesma validação; o que a âncora prova — os nomes, a ordem e os tipos que o bash
escreveu, `warmup` booleano à frente de todos — não passa por ele. Todo o resto
do arquivo é o que a Instância gravou.
