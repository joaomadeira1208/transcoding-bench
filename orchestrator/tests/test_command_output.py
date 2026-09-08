# Payloads escritos à mão no formato documentado da AWS CLI v2, até o primeiro
# preflight capturar os reais (ADR-0022): o que se assere é a leitura, nunca o
# argv que a produziu. Cada caso aqui é uma falha silenciosa concreta — um id de
# instância perdido é uma máquina faturando sem dono, uma chave sumida de uma
# listagem é um bloco que o `resume.py` acha incompleto e refaz.

from __future__ import annotations

import json
from typing import Any

import pytest
from command_output import (
    CloudInitStatus,
    OutputError,
    parse_cloud_init_status,
    parse_describe_instances,
    parse_get_parameter,
    parse_list_objects,
    parse_run_instances,
)


def run_instances_payload(*instances: dict[str, Any]) -> str:
    return json.dumps(
        {
            "Groups": [],
            "Instances": list(instances),
            "OwnerId": "123456789012",
            "ReservationId": "r-0a1b2c3d4e5f60718",
        }
    )


def launched_instance(instance_id: str = "i-0123456789abcdef0") -> dict[str, Any]:
    return {
        "AmiLaunchIndex": 0,
        "ImageId": "ami-0abcdef1234567890",
        "InstanceId": instance_id,
        "InstanceType": "c7g.xlarge",
        "KeyName": "transcoding-bench",
        "LaunchTime": "2026-09-07T12:00:00+00:00",
        "PrivateIpAddress": "10.0.1.42",
        "State": {"Code": 0, "Name": "pending"},
        "SubnetId": "subnet-0a1b2c3d4e5f60718",
    }


def describe_payload(*reservations: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "Reservations": [
                {
                    "Groups": [],
                    "Instances": instances,
                    "OwnerId": "123456789012",
                    "ReservationId": "r-0a1b2c3d4e5f60718",
                }
                for instances in reservations
            ]
        }
    )


def described_instance(
    instance_id: str = "i-0123456789abcdef0",
    state: str = "running",
    public_ip: str | None = "54.210.1.2",
) -> dict[str, Any]:
    instance = {
        "ImageId": "ami-0abcdef1234567890",
        "InstanceId": instance_id,
        "InstanceType": "c7g.xlarge",
        "KeyName": "transcoding-bench",
        "LaunchTime": "2026-09-07T12:00:00+00:00",
        "PrivateIpAddress": "10.0.1.42",
        "State": {"Code": 16, "Name": state},
        "StateTransitionReason": "",
        "SubnetId": "subnet-0a1b2c3d4e5f60718",
    }
    if public_ip is not None:
        instance["PublicIpAddress"] = public_ip
    return instance


def list_objects_payload(*contents: dict[str, Any], **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "Contents": list(contents),
        "IsTruncated": False,
        "KeyCount": len(contents),
        "MaxKeys": 1000,
        "Name": "transcoding-bench-runs",
        "Prefix": "runs/",
    }
    return json.dumps(payload | overrides)


PARAMETER_VALUE = "not-a-real-key"


def s3_object(key: str, size: int = 4096) -> dict[str, Any]:
    return {
        "Key": key,
        "LastModified": "2026-09-07T12:00:00+00:00",
        "ETag": '"9f0c4a2e6b414d5f8a372f1c8de0b7a4"',
        "Size": size,
        "StorageClass": "STANDARD",
    }


class TestRunInstances:
    def test_returns_the_launched_instance_id(self):
        assert parse_run_instances(run_instances_payload(launched_instance())) == (
            "i-0123456789abcdef0"
        )

    def test_rejects_a_launch_without_instances(self):
        with pytest.raises(OutputError, match="veio 0"):
            parse_run_instances(run_instances_payload())

    def test_rejects_more_than_one_instance(self):
        # O adaptador lança uma por chamada e devolve um id. Se a resposta trouxer
        # duas, pegar a primeira em silêncio deixa a segunda faturando sem que
        # ninguém guarde o id para terminá-la.
        payload = run_instances_payload(
            launched_instance("i-0123456789abcdef0"),
            launched_instance("i-0fedcba9876543210"),
        )

        with pytest.raises(OutputError, match="2"):
            parse_run_instances(payload)

    def test_rejects_an_instance_without_id(self):
        instance = launched_instance()
        del instance["InstanceId"]

        with pytest.raises(OutputError, match="InstanceId"):
            parse_run_instances(run_instances_payload(instance))

    def test_rejects_output_that_is_not_json(self):
        with pytest.raises(OutputError, match="JSON"):
            parse_run_instances("An error occurred (UnauthorizedOperation)")


class TestDescribeInstances:
    def test_pending_has_no_public_ip_yet(self):
        payload = describe_payload([described_instance(state="pending", public_ip=None)])

        (state,) = parse_describe_instances(payload)

        assert state.instance_id == "i-0123456789abcdef0"
        assert state.state == "pending"
        assert state.public_ip is None

    def test_running_without_public_ip(self):
        # Existe: a instância vira `running` e o endereço aparece na chamada
        # seguinte. É o caso que faz "pronta" exigir as duas metades.
        payload = describe_payload([described_instance(state="running", public_ip=None)])

        (state,) = parse_describe_instances(payload)

        assert state.state == "running"
        assert state.public_ip is None

    def test_running_with_public_ip(self):
        payload = describe_payload([described_instance(state="running", public_ip="54.210.1.2")])

        (state,) = parse_describe_instances(payload)

        assert state.state == "running"
        assert state.public_ip == "54.210.1.2"

    def test_terminated(self):
        payload = describe_payload([described_instance(state="terminated", public_ip=None)])

        (state,) = parse_describe_instances(payload)

        assert state.state == "terminated"

    def test_no_reservations_is_an_empty_list(self):
        # A consulta logo depois do `run-instances` pode não achar a instância
        # ainda; ausência é resposta, não erro.
        assert parse_describe_instances(describe_payload()) == []

    def test_instances_of_every_reservation(self):
        payload = describe_payload(
            [described_instance("i-0123456789abcdef0")],
            [described_instance("i-0fedcba9876543210")],
        )

        assert [state.instance_id for state in parse_describe_instances(payload)] == [
            "i-0123456789abcdef0",
            "i-0fedcba9876543210",
        ]

    def test_rejects_a_public_ip_that_is_not_a_string(self):
        # O campo é o único opcional, e o jeito natural de o ler é um `.get()` nu
        # — que deixa qualquer coisa truthy passar por "tem IP" no predicado de
        # prontidão.
        instance = described_instance()
        instance["PublicIpAddress"] = ["54.210.1.2"]

        with pytest.raises(OutputError, match="PublicIpAddress"):
            parse_describe_instances(describe_payload([instance]))

    def test_rejects_an_instance_without_state(self):
        instance = described_instance()
        del instance["State"]

        with pytest.raises(OutputError, match="State"):
            parse_describe_instances(describe_payload([instance]))


class TestListObjects:
    def test_empty_output_is_no_objects(self):
        # A CLI v2 não imprime nada quando o prefixo não casa com objeto algum:
        # a saída vazia é resposta legítima, e um `json.loads` nu levanta aqui.
        assert parse_list_objects("") == []

    def test_payload_without_contents_is_no_objects(self):
        assert parse_list_objects(list_objects_payload()) == []

    def test_one_page_of_keys_and_sizes(self):
        payload = list_objects_payload(
            s3_object("runs/9f0c4a2e/meta.json", 1462),
            s3_object("runs/9f0c4a2e/time.json", 233),
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
        payload = list_objects_payload(
            s3_object("runs/1/meta.json"),
            s3_object("runs/10/meta.json"),
        )

        assert [item.key for item in parse_list_objects(payload)] == [
            "runs/1/meta.json",
            "runs/10/meta.json",
        ]

    def test_rejects_a_truncated_page(self):
        # A premissa fica escrita aqui: a CLI v2 agrega as páginas sozinha, então
        # `IsTruncated` verdadeiro só aparece se alguém acrescentar `--page-size`
        # ou `--max-items` ao argv — e daí a listagem passa a perder chaves em
        # silêncio, que é como um bloco completo vira incompleto no `resume.py`.
        payload = list_objects_payload(s3_object("runs/1/meta.json"), IsTruncated=True)

        with pytest.raises(OutputError, match="--page-size"):
            parse_list_objects(payload)

    def test_rejects_a_page_with_a_continuation_token(self):
        # A outra face da mesma premissa: com `--max-items` a CLI v2 devolve um
        # `NextToken` em vez de marcar `IsTruncated`.
        payload = list_objects_payload(s3_object("runs/1/meta.json"), NextToken="eyJNYXJrZXIi")

        with pytest.raises(OutputError, match="--max-items"):
            parse_list_objects(payload)

    def test_rejects_an_object_without_size(self):
        entry = s3_object("runs/1/meta.json")
        del entry["Size"]

        with pytest.raises(OutputError, match="Size"):
            parse_list_objects(list_objects_payload(entry))


class TestGetParameter:
    # O valor real é a chave privada de SSH; aqui ele é um marcador, porque um
    # bloco PEM committado no repositório é o que o `gitleaks` existe para barrar.
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
        # O valor é a chave privada SSH (ADR-0016): um parser que a interpolasse
        # na mensagem a derramaria no log do Orquestrador.
        payload = json.dumps({"Parameter": {"Value": 42}})

        with pytest.raises(OutputError) as error:
            parse_get_parameter(payload)

        assert "42" not in str(error.value)


class TestCloudInitStatus:
    def test_done(self):
        assert parse_cloud_init_status("status: done\n") is CloudInitStatus.DONE

    def test_degraded_done_is_done(self):
        # O Ubuntu 24.04 emite `degraded done` quando o boot concluiu com erro
        # recuperável: é um lançamento saudável, e recusá-lo derruba a instância
        # que acabou de subir.
        assert parse_cloud_init_status("status: degraded done\n") is CloudInitStatus.DONE

    def test_running(self):
        assert parse_cloud_init_status("status: running\n") is CloudInitStatus.RUNNING

    def test_error_with_the_detail_lines(self):
        output = "status: error\nerror: Unexpected error while running command.\n"

        assert parse_cloud_init_status(output) is CloudInitStatus.ERROR

    def test_not_run_is_still_running(self):
        # O estado de antes de o cloud-init começar: para quem espera, é o mesmo
        # que rodando.
        assert parse_cloud_init_status("status: not run\n") is CloudInitStatus.RUNNING

    def test_rejects_disabled(self):
        # Esperar por um cloud-init desabilitado é esperar até o timeout por um
        # user-data que ninguém vai rodar.
        with pytest.raises(OutputError, match="desabilitado"):
            parse_cloud_init_status("status: disabled\n")

    def test_rejects_a_status_it_does_not_know(self):
        with pytest.raises(OutputError, match="futuro"):
            parse_cloud_init_status("status: futuro\n")

    def test_rejects_output_without_a_status_line(self):
        with pytest.raises(OutputError, match="status"):
            parse_cloud_init_status("Permission denied (publickey).\n")
