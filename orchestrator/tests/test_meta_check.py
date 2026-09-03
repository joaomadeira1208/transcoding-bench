# Testes do checador stdlib do `meta.json`.
#
# Ele existe porque `resume.py` e `quality_triage.py` são stdlib-only por desenho
# (ADR-0017) e não podem importar o modelo pydantic do `analysis/`: é regra
# duplicada, não código compartilhado, exatamente como a ADR-0019 já faz com o
# dedup — verificação independente é o que se compra com a duplicação.
#
# Os cinco campos da tabela da ADR-0022 são verificados por **presença, tipo e
# valor**, e cada metade tem um modo de falha concreto. Presença: o jeito natural
# de escrever o filtro é `m.get("warmup")`, e com o campo ausente isso vira
# `None` → falsy → o warm-up entra na retomada como Replicação. Tipo: quem
# escreve o arquivo é bash montando JSON à mão, e `"warmup": "false"` é uma
# string truthy que produz o mesmo desastre passando por qualquer checagem de
# presença.

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import ABSENT, make_meta_json
from meta_check import MetaError, check_meta


class TestAccepts:
    def test_valid_meta_comes_back_parsed(self):
        # O checador devolve o dict porque quem o chama vai justamente ler o
        # arquivo: um checador que só levantasse erro obrigaria o chamador a
        # fazer o `json.loads` de novo, e a segunda leitura é onde alguém
        # esqueceria de checar.
        meta = check_meta(make_meta_json())

        assert meta["scenario_id"] == "libx264_2160p_1080p_bbb_c7g_rep1"
        assert meta["warmup"] is False
        assert meta["exit_code"] == 0

    def test_warmup_true(self):
        assert check_meta(make_meta_json(warmup=True))["warmup"] is True

    def test_failed_run(self):
        # Run falho é dado, não erro de contrato: ele sobe com `exit_code != 0`
        # e o `resume.py` o trata como não-completo (ADR-0019).
        assert check_meta(make_meta_json(exit_code=137))["exit_code"] == 137

    def test_offset_other_than_utc(self):
        meta = check_meta(make_meta_json(started_at="2026-08-08T07:00:00-03:00"))

        assert datetime.fromisoformat(meta["started_at"]) == datetime(2026, 8, 8, 10, 0, tzinfo=UTC)


class TestRejects:
    def test_warmup_as_string(self):
        with pytest.raises(MetaError, match="warmup"):
            check_meta(make_meta_json(warmup="false"))

    def test_warmup_as_number(self):
        with pytest.raises(MetaError, match="warmup"):
            check_meta(make_meta_json(warmup=0))

    def test_warmup_absent(self):
        with pytest.raises(MetaError, match="warmup"):
            check_meta(make_meta_json(warmup=ABSENT))

    def test_exit_code_as_string(self):
        with pytest.raises(MetaError, match="exit_code"):
            check_meta(make_meta_json(exit_code="0"))

    def test_exit_code_as_bool(self):
        # `isinstance(True, int)` é verdadeiro em Python: um checador escrito com
        # `isinstance` aceitaria `"exit_code": true` e o `resume.py` compararia
        # `True != 0` como "falhou". A checagem é por tipo exato.
        with pytest.raises(MetaError, match="exit_code"):
            check_meta(make_meta_json(exit_code=True))

    def test_exit_code_absent(self):
        with pytest.raises(MetaError, match="exit_code"):
            check_meta(make_meta_json(exit_code=ABSENT))

    def test_unknown_schema_version(self):
        with pytest.raises(MetaError, match="schema_version"):
            check_meta(make_meta_json(schema_version="2"))

    def test_schema_version_as_number(self):
        # O bash escreve `"1"` literal; um `1` sem aspas é o arquivo mudando de
        # forma sem mudar de versão.
        with pytest.raises(MetaError, match="schema_version"):
            check_meta(make_meta_json(schema_version=1))

    def test_schema_version_absent(self):
        with pytest.raises(MetaError, match="schema_version"):
            check_meta(make_meta_json(schema_version=ABSENT))

    def test_empty_scenario_id(self):
        with pytest.raises(MetaError, match="scenario_id"):
            check_meta(make_meta_json(scenario_id=""))

    def test_scenario_id_absent(self):
        with pytest.raises(MetaError, match="scenario_id"):
            check_meta(make_meta_json(scenario_id=ABSENT))

    def test_naive_started_at(self):
        # A dedup "último `started_at` vence" ordena instantes; sem offset a
        # informação para normalizar já se perdeu, e comparar naïve com aware
        # levanta `TypeError` lá na frente — ou, pior, dois naïves de fuso
        # desconhecido devolvem uma ordem inventada (ADR-0022).
        with pytest.raises(MetaError, match="started_at"):
            check_meta(make_meta_json(started_at="2026-08-08T10:00:00"))

    def test_unparseable_started_at(self):
        with pytest.raises(MetaError, match="started_at"):
            check_meta(make_meta_json(started_at="ontem"))

    def test_started_at_absent(self):
        with pytest.raises(MetaError, match="started_at"):
            check_meta(make_meta_json(started_at=ABSENT))

    def test_malformed_json(self):
        # O bash monta este arquivo à mão: JSON quebrado é modo de falha real, e
        # um `json.JSONDecodeError` vazando para o chamador não diria qual dos
        # 972 objetos do S3 está corrompido.
        with pytest.raises(MetaError):
            check_meta("{")

    def test_json_that_is_not_an_object(self):
        with pytest.raises(MetaError):
            check_meta("[]")
