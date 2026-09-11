# Smoke do `encode/fetch_masters.sh`: o download dos Masters e a guarda contra
# Master corrompido, com o shim do `aws` no sentido bucket → disco (ADR-0022).

from __future__ import annotations

from typing import Any

import pytest
from conftest import (
    BUCKET,
    EXPERIMENT_TOML,
    MASTERS_PREFIX,
    Fetch,
    master_bytes,
    validate_manifest_with_cli,
)


def names(manifest: dict[str, Any]) -> list[str]:
    return [master["name"] for master in manifest["masters"]]


@pytest.fixture(scope="session")
def fetched(fetch_masters) -> Fetch:
    return fetch_masters()


@pytest.fixture(scope="session")
def corrupt_master(masters_manifest: dict[str, Any]) -> str:
    """O segundo do manifesto: há um Master antes dele e outros depois."""
    return names(masters_manifest)[1]


@pytest.fixture(scope="session")
def fetched_with_a_corrupt_master(fetch_masters, corrupt_master: str) -> Fetch:
    return fetch_masters(corrupt=corrupt_master)


class TestTheManifest:
    def test_the_contract_of_the_orchestrator_accepts_it(self, fetched):
        result = validate_manifest_with_cli(fetched.manifest_path, EXPERIMENT_TOML)

        assert result.returncode == 0, result.stderr


class TestDownload:
    def test_the_script_succeeds(self, fetched):
        assert fetched.returncode == 0, fetched.stderr

    def test_every_master_of_the_manifest_lands_in_the_destination(self, fetched):
        assert fetched.downloaded() == set(names(fetched.manifest))

    def test_what_landed_is_what_the_bucket_had(self, fetched):
        for name in names(fetched.manifest):
            assert (fetched.dest / name).read_bytes() == master_bytes(name)

    def test_one_s3_cp_per_object_listed_in_the_manifest(self, fetched):
        expected = [
            ["s3", "cp", f"s3://{BUCKET}/{MASTERS_PREFIX}{name}", str(fetched.dest / name)]
            for name in names(fetched.manifest)
        ]

        assert fetched.argv("aws") == expected


class TestCorruptMaster:
    def test_it_exits_non_zero_naming_the_master(
        self, fetched_with_a_corrupt_master, corrupt_master
    ):
        assert fetched_with_a_corrupt_master.returncode != 0
        assert corrupt_master in fetched_with_a_corrupt_master.stderr

    def test_no_other_master_is_named(self, fetched_with_a_corrupt_master, corrupt_master):
        named = [
            name
            for name in names(fetched_with_a_corrupt_master.manifest)
            if name in fetched_with_a_corrupt_master.stderr
        ]

        assert named == [corrupt_master]

    def test_nothing_past_it_is_downloaded(self, fetched_with_a_corrupt_master, corrupt_master):
        listed = names(fetched_with_a_corrupt_master.manifest)
        expected = set(listed[: listed.index(corrupt_master) + 1])

        assert fetched_with_a_corrupt_master.downloaded() == expected
