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
from experiment_config import CpuOptions, InstanceRecord
from infra_config import parse_infra
from instance_launch import (
    ENCODE_ROLE,
    LaunchError,
    encode_name,
    encode_tags,
    encode_target,
    instance_target,
)


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
        strange = InstanceRecord(
            id="c7x", instance_type="c7x.xlarge", arch="riscv", cpu_options=None
        )
        config = replace(real_config(), instances=(strange,))

        with pytest.raises(LaunchError, match="riscv"):
            encode_target(config, amis(), "c7x.xlarge")


def two_arms():
    """Os dois braços da topologia: o mesmo tipo, e só o `CpuOptions` os separa."""
    return replace(
        real_config(),
        instances=tuple(
            InstanceRecord(
                id=f"c7i-{name}",
                instance_type="c7i.2xlarge",
                arch="x86_64",
                cpu_options=CpuOptions(core_count=cores, threads_per_core=threads),
            )
            for name, cores, threads in (("smt", 2, 2), ("nosmt", 4, 1))
        ),
    )


class TestTwoInstancesOfTheSameType:
    def test_the_type_alone_is_refused_naming_both_ids(self):
        # Escolher um dos dois em silêncio lança o outro braço com o `CpuOptions`
        # errado, e o preflight confere a topologia de quem não vai medir.
        with pytest.raises(LaunchError) as raised:
            encode_target(two_arms(), amis(), "c7i.2xlarge")

        assert "c7i-smt" in str(raised.value) and "c7i-nosmt" in str(raised.value)

    @pytest.mark.parametrize(("instance", "threads"), [("c7i-smt", 2), ("c7i-nosmt", 1)])
    def test_the_id_picks_the_arm_with_its_own_topology(self, instance, threads):
        target = instance_target(two_arms(), amis(), instance)

        assert target.instance.id == instance
        assert target.instance.cpu_options is not None
        assert target.instance.cpu_options.threads_per_core == threads
        assert target.image_id == amis().encode_amd64

    def test_an_id_the_definition_does_not_declare_is_refused_naming_the_declared_ones(self):
        with pytest.raises(LaunchError, match="c7i-nosmt"):
            instance_target(two_arms(), amis(), "c7i-ht")


class TestTheTagsOfALaunchedInstance:
    def test_the_role_is_the_one_the_terminate_policy_authorizes(self):
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
