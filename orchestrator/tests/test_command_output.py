# O que se assere aqui é a leitura da saída, nunca o argv que a produziu
# (ADR-0022). Cada caso é uma falha silenciosa concreta — um id de instância
# perdido é uma máquina faturando sem dono, uma chave sumida de uma listagem é um
# bloco que o `resume.py` acha incompleto e refaz.

from __future__ import annotations

import json

import pytest
from command_output import (
    CloudInitStatus,
    OutputError,
    parse_caller_identity,
    parse_cloud_init_status,
    parse_describe_instances,
    parse_get_parameter,
    parse_list_objects,
    parse_run_instances,
)
from conftest import (
    CALLER_ARN,
    make_caller_identity_payload,
    make_describe_payload,
    make_described_instance,
    make_launched_instance,
    make_list_objects_payload,
    make_run_instances_payload,
    make_s3_object,
)

PARAMETER_VALUE = "not-a-real-key"


class TestRunInstances:
    def test_returns_the_launched_instance_id(self):
        assert parse_run_instances(make_run_instances_payload(make_launched_instance())) == (
            "i-0123456789abcdef0"
        )

    def test_rejects_a_launch_without_instances(self):
        with pytest.raises(OutputError, match="veio 0"):
            parse_run_instances(make_run_instances_payload())

    def test_rejects_more_than_one_instance(self):
        # O adaptador lança uma por chamada e devolve um id. Se a resposta trouxer
        # duas, pegar a primeira em silêncio deixa a segunda faturando sem que
        # ninguém guarde o id para terminá-la.
        payload = make_run_instances_payload(
            make_launched_instance("i-0123456789abcdef0"),
            make_launched_instance("i-0fedcba9876543210"),
        )

        with pytest.raises(OutputError, match="2"):
            parse_run_instances(payload)

    def test_rejects_an_instance_without_id(self):
        instance = make_launched_instance()
        del instance["InstanceId"]

        with pytest.raises(OutputError, match="InstanceId"):
            parse_run_instances(make_run_instances_payload(instance))

    def test_rejects_output_that_is_not_json(self):
        with pytest.raises(OutputError, match="JSON"):
            parse_run_instances("An error occurred (UnauthorizedOperation)")


class TestDescribeInstances:
    def test_pending_has_no_public_ip_yet(self):
        payload = make_describe_payload([make_described_instance(state="pending", public_ip=None)])

        (instance,) = parse_describe_instances(payload)

        assert instance.instance_id == "i-0123456789abcdef0"
        assert instance.state == "pending"
        assert instance.public_ip is None
        assert instance.private_ip == "10.0.1.42"

    def test_running_without_public_ip(self):
        payload = make_describe_payload([make_described_instance(state="running", public_ip=None)])

        (instance,) = parse_describe_instances(payload)

        assert instance.state == "running"
        assert instance.public_ip is None

    def test_running_carries_both_addresses(self):
        payload = make_describe_payload(
            [
                make_described_instance(
                    state="running", public_ip="54.210.1.2", private_ip="10.0.1.42"
                )
            ]
        )

        (instance,) = parse_describe_instances(payload)

        assert instance.state == "running"
        assert instance.public_ip == "54.210.1.2"
        assert instance.private_ip == "10.0.1.42"

    def test_running_without_private_ip(self):
        payload = make_describe_payload([make_described_instance(state="running", private_ip=None)])

        (instance,) = parse_describe_instances(payload)

        assert instance.state == "running"
        assert instance.private_ip is None

    def test_terminated(self):
        payload = make_describe_payload(
            [make_described_instance(state="terminated", public_ip=None)]
        )

        (instance,) = parse_describe_instances(payload)

        assert instance.state == "terminated"

    def test_no_reservations_is_an_empty_list(self):
        assert parse_describe_instances(make_describe_payload()) == []

    def test_instances_of_every_reservation(self):
        payload = make_describe_payload(
            [make_described_instance("i-0123456789abcdef0")],
            [make_described_instance("i-0fedcba9876543210")],
        )

        assert [instance.instance_id for instance in parse_describe_instances(payload)] == [
            "i-0123456789abcdef0",
            "i-0fedcba9876543210",
        ]

    def test_rejects_a_public_ip_that_is_not_a_string(self):
        # O campo é o único opcional, e o jeito natural de o ler é um `.get()` nu
        # — que deixa qualquer coisa truthy passar por "tem IP" no predicado de
        # prontidão.
        instance = make_described_instance()
        instance["PublicIpAddress"] = ["54.210.1.2"]

        with pytest.raises(OutputError, match="PublicIpAddress"):
            parse_describe_instances(make_describe_payload([instance]))

    def test_rejects_a_private_ip_that_is_not_a_string(self):
        instance = make_described_instance()
        instance["PrivateIpAddress"] = ["10.0.1.42"]

        with pytest.raises(OutputError, match="PrivateIpAddress"):
            parse_describe_instances(make_describe_payload([instance]))

    def test_rejects_an_instance_without_state(self):
        instance = make_described_instance()
        del instance["State"]

        with pytest.raises(OutputError, match="State"):
            parse_describe_instances(make_describe_payload([instance]))


class TestListObjects:
    def test_empty_output_is_no_objects(self):
        # A CLI v2 não imprime nada quando o prefixo não casa com objeto algum:
        # a saída vazia é resposta legítima, e um `json.loads` nu levanta aqui.
        assert parse_list_objects("") == []

    def test_payload_without_contents_is_no_objects(self):
        assert parse_list_objects(make_list_objects_payload()) == []

    def test_one_page_of_keys_and_sizes(self):
        payload = make_list_objects_payload(
            make_s3_object("runs/9f0c4a2e/meta.json", 1462),
            make_s3_object("runs/9f0c4a2e/time.json", 233),
        )

        objects = parse_list_objects(payload)

        assert [(item.key, item.size) for item in objects] == [
            ("runs/9f0c4a2e/meta.json", 1462),
            ("runs/9f0c4a2e/time.json", 233),
        ]

    def test_a_prefix_that_matches_partially_comes_back_whole(self):
        # `--prefix` é casamento de string, não de caminho: `runs/1` traz
        # `runs/10/` junto. O parser devolve o que a API devolveu — filtrar aqui
        # esconderia do chamador que o prefixo dele precisa terminar em `/`.
        payload = make_list_objects_payload(
            make_s3_object("runs/1/meta.json"),
            make_s3_object("runs/10/meta.json"),
        )

        assert [item.key for item in parse_list_objects(payload)] == [
            "runs/1/meta.json",
            "runs/10/meta.json",
        ]

    def test_rejects_a_truncated_page(self):
        payload = make_list_objects_payload(make_s3_object("runs/1/meta.json"), IsTruncated=True)

        with pytest.raises(OutputError, match="--page-size"):
            parse_list_objects(payload)

    def test_rejects_a_page_with_a_continuation_token(self):
        payload = make_list_objects_payload(
            make_s3_object("runs/1/meta.json"), NextToken="eyJNYXJrZXIi"
        )

        with pytest.raises(OutputError, match="--max-items"):
            parse_list_objects(payload)

    def test_rejects_an_object_without_size(self):
        entry = make_s3_object("runs/1/meta.json")
        del entry["Size"]

        with pytest.raises(OutputError, match="Size"):
            parse_list_objects(make_list_objects_payload(entry))


class TestGetParameter:
    def test_returns_the_decrypted_value(self):
        payload = json.dumps(
            {
                "Parameter": {
                    "Name": "/transcoding-bench/ssh-private-key",
                    "Type": "SecureString",
                    "Value": PARAMETER_VALUE,
                    "Version": 1,
                    "LastModifiedDate": "2026-09-07T12:00:00+00:00",
                    "ARN": "arn:aws:ssm:us-east-1:123456789012:parameter/x",
                    "DataType": "text",
                }
            }
        )

        assert parse_get_parameter(payload) == PARAMETER_VALUE

    def test_rejects_a_payload_without_the_parameter(self):
        with pytest.raises(OutputError, match="Parameter"):
            parse_get_parameter(json.dumps({}))

    def test_error_does_not_echo_the_value(self):
        payload = json.dumps({"Parameter": {"Value": 42}})

        with pytest.raises(OutputError) as error:
            parse_get_parameter(payload)

        assert "42" not in str(error.value)


class TestCallerIdentity:
    def test_returns_the_arn_of_the_identity(self):
        assert parse_caller_identity(make_caller_identity_payload()) == CALLER_ARN

    def test_rejects_a_payload_without_the_arn(self):
        payload = json.loads(make_caller_identity_payload())
        del payload["Arn"]

        with pytest.raises(OutputError, match="Arn"):
            parse_caller_identity(json.dumps(payload))

    def test_rejects_an_arn_that_is_not_a_string(self):
        with pytest.raises(OutputError, match="Arn"):
            parse_caller_identity(make_caller_identity_payload(Arn=None))


class TestCloudInitStatus:
    def test_done(self):
        assert parse_cloud_init_status("status: done\n") is CloudInitStatus.DONE

    def test_degraded_done_is_done(self):
        assert parse_cloud_init_status("status: degraded done\n") is CloudInitStatus.DONE

    def test_running(self):
        assert parse_cloud_init_status("status: running\n") is CloudInitStatus.RUNNING

    def test_error_with_the_detail_lines(self):
        output = "status: error\nerror: Unexpected error while running command.\n"

        assert parse_cloud_init_status(output) is CloudInitStatus.ERROR

    def test_not_run_is_still_running(self):
        assert parse_cloud_init_status("status: not run\n") is CloudInitStatus.RUNNING

    def test_rejects_disabled(self):
        with pytest.raises(OutputError, match="desabilitado"):
            parse_cloud_init_status("status: disabled\n")

    def test_rejects_a_status_it_does_not_know(self):
        with pytest.raises(OutputError, match="futuro"):
            parse_cloud_init_status("status: futuro\n")

    def test_rejects_output_without_a_status_line(self):
        with pytest.raises(OutputError, match="status"):
            parse_cloud_init_status("Permission denied (publickey).\n")
