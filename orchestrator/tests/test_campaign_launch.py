# O núcleo puro do `run` (D7, D13 e D18 da Spec 4). Os três alvos falham em
# silêncio e custam mais do que uma instância: a guarda que deixa passar um
# `runs/` povoado escreve o piloto dentro da campanha, ou a campanha duas vezes
# no mesmo bucket; a projeção do `--slices` que sobe o `canonical.json` reescreve
# o registro do Experimento com a fatia reduzida da retomada; e a decisão de
# bootstrap que dispara duas arquiteturas quando a terceira falhou paga dois
# dias de dado que a próxima campanha descarta.

from __future__ import annotations

import re
from typing import Any

import pytest
from campaign_launch import (
    CANONICAL_KEY,
    Bootstrap,
    CampaignError,
    abort_reasons,
    full_campaign,
    refuse_populated_runs,
    resumed_campaign,
    slice_key,
)
from command_output import S3Object, TruncatedListing
from conftest import real_config, real_pilot_config
from scenario_plan import build_canonical_plan, build_instance_slices

PREFLIGHT_PREFIX = "runs/preflight/"

DECLARED = ("c7g", "c7i", "c7a")


def listed(*keys: str) -> list[S3Object]:
    return [S3Object(key=key, size=4096) for key in keys]


def refusal(*keys: str) -> str | None:
    return refuse_populated_runs(listed(*keys), preflight_prefix=PREFLIGHT_PREFIX)


class TestTheGuardOverRuns:
    def test_an_empty_listing_passes(self):
        assert refusal() is None

    def test_the_trace_the_preflight_keeps_passes(self):
        assert (
            refusal(
                f"{PREFLIGHT_PREFIX}i-0123456789abcdef0/perf.json",
                f"{PREFLIGHT_PREFIX}i-0123456789abcdef0/perf.stderr.txt",
                f"{PREFLIGHT_PREFIX}self-check/probe.txt",
            )
            is None
        )

    def test_an_execution_is_refused_naming_its_key(self):
        key = "runs/libx264_1080p_720p_bbb_c7g_rep1_9f0c4a2e/meta.json"

        assert key in (refusal(key) or "")

    def test_an_execution_beside_the_preflight_trace_is_still_refused(self):
        key = "runs/libx264_1080p_720p_bbb_c7g_rep1_9f0c4a2e/meta.json"

        assert key in (refusal(f"{PREFLIGHT_PREFIX}i-0123456789abcdef0/perf.json", key) or "")

    def test_a_key_that_only_shares_the_word_preflight_is_an_execution(self):
        key = "runs/preflight-old/meta.json"

        assert key in (refusal(key) or "")

    def test_every_execution_is_named_at_once(self):
        keys = ("runs/a/meta.json", "runs/b/meta.json", "runs/c/perf.json")

        message = refusal(*keys) or ""

        assert all(key in message for key in keys)

    def test_a_truncated_listing_counts_as_populated(self):
        truncated = TruncatedListing("list-objects-v2: página truncada")

        message = refuse_populated_runs(truncated, preflight_prefix=PREFLIGHT_PREFIX)

        assert message is not None
        assert "truncada" in message


class TestTheFullCampaign:
    def test_the_canonical_and_one_slice_per_declared_instance_go_up(self):
        campaign = full_campaign(real_config())

        assert set(campaign.uploads) == {CANONICAL_KEY, *(slice_key(each) for each in DECLARED)}

    def test_the_canonical_is_the_plan_of_the_definition(self):
        config = real_config()

        assert full_campaign(config).uploads[CANONICAL_KEY] == build_canonical_plan(config)

    def test_every_declared_instance_is_launched_in_declaration_order(self):
        campaign = full_campaign(real_config())

        assert tuple(each.instance.id for each in campaign.slices) == DECLARED

    def test_each_instance_consumes_the_slice_of_its_architecture(self):
        config = real_config()
        expected = build_instance_slices(build_canonical_plan(config))

        for each in full_campaign(config).slices:
            assert each.key == slice_key(each.instance.id)
            assert each.plan == expected[each.instance.id]
            assert campaign_upload(config, each.key) == each.plan

    def test_the_totals_are_those_of_the_slice(self):
        # Os totais são os que a linha de progresso do `watch` divide: sobre o
        # piloto são os 6 blocos e 36 runs que o README ilustra.
        campaign = full_campaign(real_pilot_config())

        assert {(each.block_count, each.runs_total) for each in campaign.slices} == {(6, 36)}

    def test_the_slice_is_launched_by_the_declared_record(self):
        campaign = full_campaign(real_config())

        assert {each.instance.instance_type for each in campaign.slices} == {
            "c7g.xlarge",
            "c7i.xlarge",
            "c7a.xlarge",
        }


def campaign_upload(config: Any, key: str) -> dict[str, Any]:
    return full_campaign(config).uploads[key]


def reduced(instance: str, blocks: int) -> dict[str, Any]:
    """A fatia reduzida que o `resume.py` escreve: os primeiros blocos da original."""
    whole = build_instance_slices(build_canonical_plan(real_config()))[instance]
    return {**whole, "blocks": whole["blocks"][:blocks]}


class TestTheResumedCampaign:
    def test_only_the_slices_present_go_up(self):
        slices = {"c7g": reduced("c7g", 2), "c7a": reduced("c7a", 1)}

        campaign = resumed_campaign(real_config(), slices)

        assert set(campaign.uploads) == {slice_key("c7g"), slice_key("c7a")}

    def test_the_canonical_is_not_touched(self):
        campaign = resumed_campaign(real_config(), {"c7g": reduced("c7g", 2)})

        assert CANONICAL_KEY not in campaign.uploads

    def test_only_those_architectures_are_launched(self):
        campaign = resumed_campaign(real_config(), {"c7a": reduced("c7a", 1)})

        assert tuple(each.instance.id for each in campaign.slices) == ("c7a",)

    def test_the_launch_order_is_the_declared_one_and_not_the_directory_one(self):
        slices = {"c7a": reduced("c7a", 1), "c7g": reduced("c7g", 1)}

        campaign = resumed_campaign(real_config(), slices)

        assert tuple(each.instance.id for each in campaign.slices) == ("c7g", "c7a")

    def test_the_slice_that_goes_up_is_the_reduced_one(self):
        plan = reduced("c7g", 2)

        campaign = resumed_campaign(real_config(), {"c7g": plan})

        assert campaign.uploads[slice_key("c7g")] == plan
        assert campaign.slices[0].plan == plan

    def test_the_totals_are_those_of_the_reduced_slice_and_not_of_the_definition(self):
        campaign = resumed_campaign(real_config(), {"c7g": reduced("c7g", 2)})

        assert campaign.slices[0].block_count == 2
        assert campaign.slices[0].runs_total == 12

    def test_a_directory_without_slices_is_refused(self):
        with pytest.raises(CampaignError, match="nenhuma fatia"):
            resumed_campaign(real_config(), {})

    def test_an_architecture_the_definition_does_not_declare_is_refused_naming_it(self):
        with pytest.raises(CampaignError, match=r"c7q.*c7a, c7g, c7i"):
            resumed_campaign(real_config(), {"c7q": reduced("c7g", 1)})

    def test_the_canonical_left_in_the_directory_is_refused_as_an_undeclared_id(self):
        # O `resume.py` não o escreve, mas o `generate_scenarios.py` escreve: um
        # `--slices build/scenarios` subiria o canônico de volta como fatia.
        with pytest.raises(CampaignError, match="canonical"):
            resumed_campaign(real_config(), {"canonical": build_canonical_plan(real_config())})

    def test_a_slice_whose_blocks_belong_to_another_architecture_is_refused(self):
        # A fatia `c7g.json` cheia de blocos do c7i lança uma c7g.xlarge que
        # encoda a matriz do c7i e escreve `instance = "c7i"` em cada meta.json.
        with pytest.raises(CampaignError, match=re.escape("c7g.json") + r".*c7i"):
            resumed_campaign(real_config(), {"c7g": reduced("c7i", 1)})

    @pytest.mark.parametrize("payload", [[], "c7g", {"schema_version": "1"}, {"blocks": {}}])
    def test_a_file_that_is_not_a_slice_is_refused_naming_it(self, payload):
        with pytest.raises(CampaignError, match=re.escape("c7g.json")):
            resumed_campaign(real_config(), {"c7g": payload})


def outcomes(**statuses: Bootstrap) -> dict[str, Bootstrap]:
    return {"c7g": Bootstrap.DONE, "c7i": Bootstrap.DONE, "c7a": Bootstrap.DONE, **statuses}


class TestTheDecisionAfterTheBootstraps:
    def test_the_three_concluded_is_dispatch(self):
        assert abort_reasons(outcomes()) == ()

    def test_one_cloud_init_in_error_aborts_naming_it(self):
        reasons = abort_reasons(outcomes(c7i=Bootstrap.ERROR))

        assert len(reasons) == 1
        assert "c7i" in reasons[0]

    def test_one_timeout_aborts_naming_it(self):
        reasons = abort_reasons(outcomes(c7a=Bootstrap.TIMEOUT))

        assert len(reasons) == 1
        assert "c7a" in reasons[0]

    def test_the_architecture_the_run_did_not_wait_for_is_named_as_such(self):
        # O `run` para de esperar na primeira falha: a terceira não foi
        # provada, e o relatório não pode dizer que ela concluiu.
        reasons = abort_reasons(outcomes(c7i=Bootstrap.ERROR, c7a=Bootstrap.NOT_AWAITED))

        assert len(reasons) == 2
        assert "c7i" in reasons[0]
        assert "c7a" in reasons[1] and Bootstrap.NOT_AWAITED.value in reasons[1]

    def test_the_concluded_ones_are_not_in_the_reasons(self):
        reasons = abort_reasons(outcomes(c7i=Bootstrap.ERROR))

        assert not any("c7g" in reason or "c7a" in reason for reason in reasons)

    def test_no_architecture_at_all_is_not_dispatch(self):
        assert abort_reasons({}) != ()
