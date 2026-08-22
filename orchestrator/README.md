# orchestrator/

Python que roda na t3.micro (ADR-0017): a maquinaria que age sobre a spec de
`config/`. Gera o plano canônico de Cenários e suas fatias por arquitetura,
dispara as Instâncias e decide o que retomar.

Runtime é **stdlib-only** (mais a AWS CLI por `subprocess`): `requirements.txt`
lista só o runtime e o bootstrap da instância nunca instala o `-dev`, de modo que
um `import pandas` acidental quebre no ambiente limpo do CI.

Nomes já cravados por ADR: `orchestrator.py`, `resume.py`, `quality_triage.py`.
Testes em `tests/`, rodando só no Mac e no CI.

O gerador do plano está partido em núcleo puro e casca: `experiment_config.py`
valida a configuração já parseada e `scenario_plan.py` a transforma no plano
canônico (as duas são funções puras, e é nelas que os testes batem);
`generate_scenarios.py` é o CLI que lê o TOML do disco, escreve os artefatos e
traduz erro em código de saída. Uma invocação emite os quatro: `canonical.json`,
o registro do Experimento, e uma fatia por arquitetura (`c7g.json`, `c7i.json`,
`c7a.json`), que é o que cada Instância de encode consome — ela roda todo bloco
do arquivo que recebeu, sem predicado de seleção no bash (ADR-0019). O
`conftest.py` deste nível é o que torna o núcleo importável pelos testes sem
`pyproject.toml` nem `sys.path` manipulado.

    python -m venv .venv
    .venv/bin/pip install -r orchestrator/requirements-dev.txt
    .venv/bin/python -m pytest orchestrator/
    .venv/bin/python orchestrator/generate_scenarios.py \
        --config config/experiment.toml --out build/scenarios

O diretório de saída é argumento porque o plano é artefato de runtime (ADR-0017):
ele não entra no repositório, e gerá-lo de novo a partir do mesmo TOML produz
bytes idênticos.
