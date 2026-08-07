# orchestrator/

Python que roda na t3.micro (ADR-0017): a maquinaria que age sobre a spec de
`config/`. Gera o plano canônico de Cenários e suas fatias por arquitetura,
dispara as Instâncias e decide o que retomar.

Runtime é **stdlib-only** (mais a AWS CLI por `subprocess`): `requirements.txt`
lista só o runtime e o bootstrap da instância nunca instala o `-dev`, de modo que
um `import pandas` acidental quebre no ambiente limpo do CI.

Nomes já cravados por ADR: `orchestrator.py`, `resume.py`, `quality_triage.py`.
Testes em `tests/`, rodando só no Mac e no CI.
