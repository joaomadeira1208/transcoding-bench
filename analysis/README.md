# analysis/

Python que roda no Mac do pesquisador (ADR-0017): o lado que **lê** o que as
Instâncias produziram. Hoje é o contrato do `meta.json`; o `consolidate.py`, que
projeta uma árvore `runs/` em Parquet, e os parsers dos artefatos de
instrumentação chegam com os próprios tickets.

O papel repete o seam do `orchestrator/`: núcleo puro (`run_meta.py` — o modelo
do `meta.json`, que recebe bytes e devolve estrutura) e casca fina
(`validate_meta.py` — o CLI que lê um arquivo do disco e traduz erro em código de
saída). O `conftest.py` deste nível é o que torna o núcleo importável pelos
testes sem `pyproject.toml` nem `sys.path` manipulado.

O runtime tem `pydantic` e nada mais: o venv é próprio do papel, e é isso que faz
o job de CI dele ter valor — uma dependência não declarada quebra no ambiente
limpo em vez de passar porque a máquina do pesquisador tinha o pacote.

    python -m venv .venv-analysis
    .venv-analysis/bin/pip install -r analysis/requirements-dev.txt
    .venv-analysis/bin/python -m pytest analysis/
    .venv-analysis/bin/python analysis/validate_meta.py runs/<run_id>/meta.json

O nome do venv é outro que o do `orchestrator/` de propósito: os dois papéis
rodam em ambientes separados (ADR-0017), e um `.venv` só serviria a um deles.

## O `meta.json`

É o artefato mais perigoso do repositório: atravessa a fronteira de linguagem no
sentido mais frágil possível — bash montando JSON à mão, Python lendo — e não há
módulo compartilhável entre as pontas (ADR-0019). O que existe são **três
verificações independentes** do mesmo contrato, de propósito:

- o modelo `pydantic` estrito daqui, validando os bytes crus;
- o checador em stdlib pura do `orchestrator/` (`meta_check.py`), que cobre os
  cinco campos sobre os quais aquele papel decide;
- o `validate_meta.py`, que é por onde o `smoke/` valida — como caixa-preta — o
  `meta.json` que o bash acabou de escrever.

O `meta.schema.json` commitado é gerado do modelo e vai para o anexo do artigo;
um teste regenera e compara, porque um schema no anexo divergindo do modelo que
valida é falha silenciosa de documentação. Para regenerá-lo:

    .venv-analysis/bin/python analysis/validate_meta.py --emit-schema \
        > analysis/meta.schema.json
