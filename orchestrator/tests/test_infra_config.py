# O arquivo de infra é o que diz em que subnet, com que perfil e sob que AMI as
# efêmeras sobem. Um campo ausente aqui não estoura no parser fraco: vira um
# `run-instances` com `--subnet-id` vazio, ou pior, uma instância de pé no lugar
# errado — o erro que só aparece depois de a instância estar faturando.

from __future__ import annotations

import pytest
from conftest import make_infra
from infra_config import InfraError, parse_infra

SECTIONS = ("security_groups", "instance_profiles", "amis", "buckets")

NESTED_FIELDS = [
    ("security_groups", "orchestrator"),
    ("security_groups", "ephemeral"),
    ("instance_profiles", "orchestrator"),
    ("instance_profiles", "encode"),
    ("instance_profiles", "judge"),
    ("instance_profiles", "masters"),
    ("amis", "orchestrator"),
    ("amis", "encode_amd64"),
    ("amis", "encode_arm64"),
    ("buckets", "campaign"),
    ("buckets", "pilot"),
]


def message(payload) -> str:
    with pytest.raises(InfraError) as raised:
        parse_infra(payload)
    return str(raised.value)


class TestTheFileTheBootstrapWrites:
    def test_the_documented_shape_is_accepted(self):
        infra = parse_infra(make_infra())

        assert infra.subnet_id == "subnet-0a1b2c3d4e5f60718"
        assert infra.key_pair_name == "transcoding-bench"
        assert infra.ssh_private_key_parameter_name == (
            "/transcoding-bench/orchestrator/ssh-private-key"
        )

    def test_the_fields_prepare_masters_launches_with(self):
        infra = parse_infra(make_infra())

        assert infra.security_groups.ephemeral == "sg-0b2c3d4e5f6071829"
        assert infra.instance_profiles.masters == "transcoding-bench-masters"
        assert infra.amis.encode_arm64 == "ami-0246d714afcc1d494"
        assert infra.buckets.campaign == "transcoding-bench-123456789012-campaign"
        assert infra.buckets.pilot == "transcoding-bench-123456789012-pilot"

    def test_a_payload_that_is_not_an_object_is_rejected(self):
        assert message([]) != ""

    def test_a_key_the_terraform_added_later_is_ignored(self):
        # Recusar chave desconhecida amarraria um `apply` novo a um checkout novo
        # do Orquestrador: o arquivo é gerado pelo Terraform deste repositório, e
        # a chave a mais que ele passe a emitir não deixa nada silenciosamente errado.
        assert parse_infra(make_infra(region="us-east-1")).subnet_id != ""


class TestMissingFields:
    @pytest.mark.parametrize(
        "key",
        [
            "subnet_id",
            "security_groups",
            "instance_profiles",
            "key_pair_name",
            "amis",
            "buckets",
            "ssh_private_key_parameter_name",
        ],
    )
    def test_a_missing_top_level_key_is_rejected_naming_it(self, key):
        payload = make_infra()
        del payload[key]

        assert key in message(payload)

    @pytest.mark.parametrize(("section", "field"), NESTED_FIELDS)
    def test_a_missing_nested_key_is_rejected_naming_it(self, section, field):
        # Cada um destes é lido em algum subcomando. Conferir só o nível de cima
        # deixaria a ausência chegar como `KeyError` no meio do laço, depois do
        # lançamento.
        payload = make_infra()
        del payload[section][field]

        assert f"{section}.{field}" in message(payload)

    @pytest.mark.parametrize("section", SECTIONS)
    def test_a_section_that_is_not_an_object_is_rejected(self, section):
        assert section in message(make_infra(**{section: "sg-0b2c3d4e5f6071829"}))


class TestValuesThatAreNotIdentifiers:
    @pytest.mark.parametrize(("section", "field"), NESTED_FIELDS)
    def test_an_empty_nested_value_is_rejected_naming_it(self, section, field):
        payload = make_infra()
        payload[section][field] = ""

        assert f"{section}.{field}" in message(payload)

    def test_an_empty_top_level_value_is_rejected_naming_it(self):
        assert "subnet_id" in message(make_infra(subnet_id=""))

    def test_a_value_that_is_not_a_string_is_rejected(self):
        # O `null` do `jq` sobre um output que o Terraform não produziu chega
        # aqui como `None`, e `f"--subnet-id {None}"` é um argv plausível.
        assert "subnet_id" in message(make_infra(subnet_id=None))
