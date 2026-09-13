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
retorna valor em cada arquitetura é pergunta do `preflight` (ADR-0022).

**Duas linhas do `perf.json` foram editadas à mão**, e não capturadas: os nomes
`L1-dcache-loads` e `L1-dcache-load-misses`, que a ADR-0006 emendada pôs no lugar
de `cache-references` e `cache-misses`. O campo `event` do `perf stat -j` é eco do
que o `-e` pediu, e o resto da linha — o `<not supported>`, o `event-runtime`, o
`pcnt-running` — segue como a ferramenta o escreveu. A regeneração pela camada de
aceite está bloqueada pelo #85, e é lá que esta nota é removida.

A allowlist do `.gitignore` admite `.json`, `.txt` e `.log` sob um diretório
`fixtures/` (ADR-0017), e é por isso que o `output.mkv` da mesma captura não está
aqui.
