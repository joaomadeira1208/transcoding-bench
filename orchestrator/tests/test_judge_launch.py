# O núcleo puro do `judge` (D16 a D18 da Spec 5). Os alvos falham em silêncio e
# custam o Pass ou a evidência da campanha: a AMI da arquitetura errada sobe um
# Juiz que não roda a imagem; um perfil que não seja o `judge` sobe sem permissão
# de escrever `quality/results/`; uma tag `role` que não seja `judge` deixa o Juiz
# interminável pela própria policy que o lançou (ADR-0016); e um arquivo de estado
# com o bucket de outro lançamento faria a vigilância procurar o marcador do Juiz
# no bucket errado até o prazo estourar.

from __future__ import annotations

import json
import shlex
from dataclasses import replace

import pytest
from campaign_state import CampaignState, parse_state, serialize_state
from conftest import make_campaign_state, make_infra, make_quality_plan, real_pilot_config
from experiment_config import JudgeRecord
from infra_config import parse_infra
from instance_launch import IMDS_HOP_LIMIT, LaunchError
from judge_launch import (
    JUDGE_NAME,
    JUDGE_ROLE,
    JUDGE_VOLUME_SIZE_GB,
    QUALITY_PLAN_KEY,
    JudgeError,
    judge_dispatch_command,
    judge_launch,
    judge_state,
    launched_judge,
)
from quality_plan import PlanError, check_plan
from status_check import Role
from vigilance import Vigilance

COMMIT = "1" * 40
BUCKET = "transcoding-bench-123456789012-pilot"
INSTANCE_ID = "i-0aaaaaaaaaaaaaaaa"


def infra():
    return parse_infra(make_infra())


def launch(config=None):
    return judge_launch(
        config or real_pilot_config(),
        infra(),
        commit=COMMIT,
        bucket=BUCKET,
        manifest_key="masters/manifest.json",
        masters_prefix="masters/",
    )


def plan(outputs: int = 3):
    whole = make_quality_plan()
    return {**whole, "outputs": [whole["outputs"][0]] * outputs}


def judge(**overrides):
    return launched_judge(
        INSTANCE_ID,
        **{
            "instance_type": "c7i.4xlarge",
            "plan": plan(),
            "commit": COMMIT,
            "total_timeout": 86400,
            **overrides,
        },
    )


class TestTheLaunchOfTheJudge:
    def test_the_type_is_the_one_the_definition_declares(self):
        assert launch().instance_type == real_pilot_config().quality.judge.instance_type

    def test_the_x86_judge_gets_the_x86_image_and_no_new_one(self):
        assert launch().image_id == infra().amis.encode_amd64

    def test_an_arm_judge_would_get_the_arm_image(self):
        config = real_pilot_config()
        arm = replace(
            config,
            quality=replace(
                config.quality, judge=JudgeRecord(instance_type="c7g.4xlarge", arch="arm64")
            ),
        )

        assert (launch(arm).instance_type, launch(arm).image_id) == (
            "c7g.4xlarge",
            infra().amis.encode_arm64,
        )

    def test_an_arch_with_no_ami_is_refused_naming_it(self):
        config = real_pilot_config()
        odd = replace(
            config,
            quality=replace(config.quality, judge=JudgeRecord("m7i.4xlarge", "riscv64")),
        )

        with pytest.raises(LaunchError, match="riscv64"):
            launch(odd)

    def test_the_profile_is_the_judge_one(self):
        assert launch().instance_profile == infra().instance_profiles.judge

    def test_the_volume_holds_the_pass_of_the_campaign(self):
        assert (launch().volume_size_gb, JUDGE_VOLUME_SIZE_GB) == (100, 100)

    def test_the_aws_of_the_container_reaches_the_imds(self):
        assert launch().imds_hop_limit == IMDS_HOP_LIMIT == 2

    def test_the_tags_are_the_three_the_terminate_policy_reads(self):
        assert launch().tags == {
            "Name": "transcoding-bench-judge",
            "role": "judge",
            "commit": COMMIT,
        }
        assert (JUDGE_NAME, JUDGE_ROLE) == ("transcoding-bench-judge", "judge")

    def test_it_goes_up_in_the_network_the_terraform_made_for_the_ephemeral_ones(self):
        chosen = infra()

        assert (launch().subnet_id, launch().security_group_id, launch().key_name) == (
            chosen.subnet_id,
            chosen.security_groups.ephemeral,
            chosen.key_pair_name,
        )

    def test_the_user_data_runs_the_bootstrap_of_the_judge(self):
        assert "/home/ubuntu/transcoding-bench/judge/bootstrap.sh" in launch().user_data


class TestThePlanTheJudgeReads:
    def test_the_reader_the_judge_calls_first_refuses_a_plan_without_outputs(self):
        with pytest.raises(PlanError, match="outputs"):
            check_plan(json.dumps({**make_quality_plan(), "outputs": []}))


class TestTheJudgeAsTheStateFileGuardsIt:
    def test_it_is_a_judge_named_after_itself(self):
        entry = judge()

        assert (entry.role, entry.instance) == (Role.JUDGE, "judge")

    def test_it_reads_the_status_objects_of_the_judge(self):
        keys = judge().status_keys

        assert (keys.done, keys.progress) == ("status/judge_done", "status/judge_progress")

    def test_it_is_born_bootstrapping_without_pid_and_without_marker(self):
        entry = judge()

        assert (entry.state, entry.pid, entry.outcome) == (Vigilance.BOOTSTRAPPING, None, None)

    def test_its_runs_are_the_outputs_of_the_plan(self):
        assert judge(plan=plan(outputs=13)).runs_total == 13

    def test_it_carries_the_type_the_commit_and_the_cap_of_its_own_launch(self):
        entry = judge(instance_type="c7i.4xlarge", commit="2" * 40, total_timeout=7200)

        assert (entry.instance_id, entry.instance_type, entry.commit, entry.total_timeout) == (
            INSTANCE_ID,
            "c7i.4xlarge",
            "2" * 40,
            7200,
        )


class TestTheStateFileTheJudgeIsAppendedTo:
    def test_without_a_file_it_is_born_for_the_judge_alone(self):
        state = judge_state(
            None, bucket=BUCKET, config_path="config/pilot.toml", commit=COMMIT, total_timeout=86400
        )

        assert state == CampaignState(
            bucket=BUCKET,
            config_path="config/pilot.toml",
            commit=COMMIT,
            total_timeout=86400,
            slice_keys=(),
            instances=(),
        )

    def test_the_file_of_the_campaign_is_kept_as_it_was(self):
        campaign = parse_state(make_campaign_state(bucket=BUCKET))

        state = judge_state(
            campaign,
            bucket=BUCKET,
            config_path="config/pilot.toml",
            commit=COMMIT,
            total_timeout=86400,
        )

        assert state == campaign

    def test_the_file_of_another_bucket_is_refused_naming_both(self):
        campaign = parse_state(make_campaign_state(bucket="transcoding-bench-1-campaign"))

        with pytest.raises(JudgeError) as refused:
            judge_state(
                campaign,
                bucket=BUCKET,
                config_path="config/pilot.toml",
                commit=COMMIT,
                total_timeout=86400,
            )

        assert "transcoding-bench-1-campaign" in str(refused.value)
        assert BUCKET in str(refused.value)

    def test_the_born_file_round_trips_with_the_judge_in_it(self):
        born = judge_state(
            None, bucket=BUCKET, config_path="config/pilot.toml", commit=COMMIT, total_timeout=86400
        )
        state = replace(born, instances=(judge(),))

        assert parse_state(json.loads(serialize_state(state))) == state


class TestThePlanTheDispatchNames:
    def test_it_is_the_name_the_bootstrap_saved_the_uploaded_plan_under(self):
        # O `bootstrap.sh` salva o plano pelo basename da chave que baixou, e o
        # `launch_container.sh` só recebe o nome: os dois lados têm de concordar.
        command = judge_dispatch_command(
            repo_dir="/home/ubuntu/transcoding-bench",
            work_dir="/home/ubuntu/work",
            bucket=BUCKET,
            commit=COMMIT,
            instance_id=INSTANCE_ID,
            instance_type="c7i.4xlarge",
            output_timeout=7200,
            total_timeout=86400,
        )
        launched = shlex.split(command[-1].split("setsid nohup ", 1)[1].split(" </dev/null")[0])

        assert f"--plan-key {QUALITY_PLAN_KEY}" in launch().user_data
        assert launched[launched.index("--plan") + 1] == QUALITY_PLAN_KEY.rsplit("/", 1)[1]
