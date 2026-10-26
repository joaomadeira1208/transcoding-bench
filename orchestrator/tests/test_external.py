# O `CpuOptions` é desenho experimental (ADR-0026), e o `run-instances` sem ele
# não falha: sobe a `c7i.2xlarge` com os 4 núcleos x 2 threads do tipo, e o braço
# que devia isolar o SMT mede outra topologia sem nada acusar.

from __future__ import annotations

import json
from collections.abc import Sequence

import external
import pytest
from conftest import captured
from experiment_config import CpuOptions


@pytest.fixture
def launched_argv(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **_: object) -> str:
        calls.append(list(argv))
        return captured("run-instances.json")

    monkeypatch.setattr(external, "_run", fake_run)

    def _launch(cpu_options: CpuOptions | None) -> list[str]:
        external.run_instances(
            instance_type="c7i.2xlarge",
            image_id="ami-025d99823a4caad37",
            subnet_id="subnet-0a1b2c3d4e5f60718",
            security_group_id="sg-0b2c3d4e5f6071829",
            instance_profile="transcoding-bench-encode",
            key_name="transcoding-bench",
            user_data="#!/bin/bash\n",
            volume_size_gb=200,
            imds_hop_limit=2,
            tags={"Name": "transcoding-bench-encode-c7i-nosmt"},
            cpu_options=cpu_options,
        )
        (argv,) = calls
        return argv

    return _launch


def option(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


class TestTheCpuOptionsOfTheLaunch:
    def test_the_declared_topology_goes_to_the_run_instances(self, launched_argv):
        argv = launched_argv(CpuOptions(core_count=4, threads_per_core=1))

        assert json.loads(option(argv, "--cpu-options")) == {"CoreCount": 4, "ThreadsPerCore": 1}

    def test_without_it_the_type_launches_with_its_own(self, launched_argv):
        assert "--cpu-options" not in launched_argv(None)
