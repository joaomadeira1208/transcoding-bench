# O núcleo puro do lançamento de uma Instância de encode. Os dois alvos falham em
# silêncio e custam uma instância faturando: a AMI da arquitetura errada sobe um
# binário que a instância recusa sem que o tipo pedido tenha mudado, e uma tag
# `role` que não seja `encode` deixa a instância interminável pela própria policy
# que a lançou (ADR-0016) — o pesquisador só descobre pela fatura.

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from conftest import make_infra, real_config
from experiment_config import InstanceRecord
from infra_config import parse_infra
from instance_launch import ENCODE_ROLE, LaunchError, encode_name, encode_tags, encode_target


def amis():
    return parse_infra(make_infra()).amis


class TestTheAmiOfTheRequestedType:
    def test_the_arm_type_gets_the_arm_image(self):
        target = encode_target(real_config(), amis(), "c7g.xlarge")

        assert target.instance.arch == "arm64"
        assert target.image_id == amis().encode_arm64

    def test_the_two_x86_types_get_the_x86_image(self):
        # `x86_64` no `experiment.toml` e `amd64` no arquivo de infra: são dois
        # vocabulários, e a tradução entre eles mora nesta função.
        for instance_type in ("c7i.xlarge", "c7a.xlarge"):
            target = encode_target(real_config(), amis(), instance_type)

            assert target.instance.arch == "x86_64"
            assert target.image_id == amis().encode_amd64

    def test_the_slice_is_named_by_the_short_id_of_the_type(self):
        # A fatia que o bootstrap baixa é `scenarios/{id}.json`: derivar o id do
        # tipo em vez de lê-lo do TOML daria um `plan-key` que não existe.
        assert encode_target(real_config(), amis(), "c7g.xlarge").instance.id == "c7g"

    def test_every_declared_instance_type_resolves_to_an_image(self):
        for instance in real_config().instances:
            assert encode_target(real_config(), amis(), instance.instance_type).image_id

    def test_a_type_the_toml_does_not_declare_is_refused_naming_the_declared_ones(self):
        with pytest.raises(LaunchError, match=re.escape("c7g.xlarge")):
            encode_target(real_config(), amis(), "c7q.xlarge")

    def test_an_arch_with_no_ami_is_refused_naming_the_arch(self):
        strange = InstanceRecord(id="c7x", instance_type="c7x.xlarge", arch="riscv")
        config = replace(real_config(), instances=(strange,))

        with pytest.raises(LaunchError, match="riscv"):
            encode_target(config, amis(), "c7x.xlarge")


class TestTheTagsOfALaunchedInstance:
    def test_the_role_is_the_one_the_terminate_policy_authorizes(self):
        # A policy da ADR-0016 condiciona o `TerminateInstances` a `role=encode`:
        # qualquer outro valor sobe uma instância que quem a lançou não derruba.
        assert encode_tags(name="qualquer", commit="abc1234")["role"] == ENCODE_ROLE

    def test_the_commit_is_the_sha_the_instance_clones(self):
        assert encode_tags(name="qualquer", commit="abc1234")["commit"] == "abc1234"

    def test_the_name_is_the_one_the_caller_chose(self):
        assert encode_tags(name="transcoding-bench-preflight", commit="abc1234")["Name"] == (
            "transcoding-bench-preflight"
        )

    def test_there_are_no_other_tags(self):
        assert set(encode_tags(name="qualquer", commit="abc1234")) == {"Name", "role", "commit"}

    def test_the_campaign_name_carries_the_short_id_of_the_architecture(self):
        arm = encode_target(real_config(), amis(), "c7g.xlarge").instance

        assert encode_name(arm) == "transcoding-bench-encode-c7g"

    def test_the_three_architectures_get_three_distinct_names(self):
        # O `Name` é o que separa as três numa listagem de órfãos (D14 da Spec 4);
        # um nome só para as três deixaria o pesquisador sem saber qual terminar.
        names = {encode_name(instance) for instance in real_config().instances}

        assert len(names) == len(real_config().instances)
