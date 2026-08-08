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
valida a configuração já parseada (função pura, é onde os testes batem) e
`generate_scenarios.py` é o CLI que lê o TOML do disco e traduz erro em código de
saída. O `conftest.py` deste nível é o que torna o núcleo importável pelos testes
sem `pyproject.toml` nem `sys.path` manipulado.

    python -m venv .venv
    .venv/bin/pip install -r orchestrator/requirements-dev.txt
    .venv/bin/python -m pytest orchestrator/
    .venv/bin/python orchestrator/generate_scenarios.py --config config/experiment.toml
