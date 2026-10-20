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

## `vmaf.json`

O log JSON que o `libvmaf` v3.1.0 da imagem — o `libvmaf` do `versions.json`
dela — escreveu no aceite do Juiz: o clip `testsrc2` de 5 s contra ele mesmo,
escalado para a própria geometria pelo filtergraph que o `run_quality.sh`
montou, com o modelo da definição e o `float_ssim`. É a âncora do parser do log
do `libvmaf` pela mesma razão das outras: até aqui ele só existia na forma que
o shim do `ffmpeg` inventou.

E a forma inventada diverge em dois pontos: o `version` do log é o commit do
`libvmaf` (`6375a4b`), não a tag, e cada frame traz as features `integer_*` além
do `vmaf` e do `float_ssim`. O primeiro frame fica abaixo de 100 porque o
`integer_motion` dele é zero, e o pooled fica em 99,98.

Sai do mesmo `--capture-dir`, e a hora de regenerá-lo é o bump do `libvmaf` no
`docker/Dockerfile` — o aceite falha enquanto este parágrafo nomear outra versão
que não a da imagem.

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

## `judge.json`

O `judge.json` que o `run_quality.sh` escreveu no **Pass do piloto**, em
`c7aff85` — o do `libx264_1080p_720p_tos_c7i_rep1`. É o segundo arquivo do
repositório escrito por um script de shell e lido por um modelo `pydantic`, e a
âncora existe pelo mesmo motivo da de `campaign_meta.json`. Dos treze, este é
um bitstream que a `c7i` e a `c7a` compartilham, e por isso o `shared_by` dele
traz dez Replicações de duas arquiteturas: é a forma que a retenção lê para
apagar as cópias.

Quem o lê é o `test_judge_agreement.py` de cada papel: o deste, pelo
`load_judgement`, e o do `orchestrator/`, pelo caminho, pelo `check_judgement`.
Os inválidos dos dois testes são esta âncora com um campo trocado, e não mais
um válido escrito à mão em cada papel.

Mesmo scrub: o `instance_id` é o único campo trocado, pelo
`i-0123456789abcdef0` das factories. E a mesma regra de regeneração: só com um
Pass novo, quando o `schema_version` do `judge.json` subir.
