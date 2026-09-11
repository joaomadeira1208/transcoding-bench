# O núcleo puro do `prepare-masters`. Os dois alvos falham em silêncio: um plano
# que chega partido ao `prepare.sh` derruba a preparação de imediato, mas um
# plano que chega **inteiro e errado** prepara seis Masters errados; e um espelho
# conferido frouxamente deixa o bucket do piloto com um Master a menos, que só
# aparece quando a Execução do piloto não acha o arquivo.

from __future__ import annotations

import json

from command_output import S3Object
from masters_launch import (
    IMAGE_VERSIONS_FILE,
    SCRIPTS_MOUNT,
    WORK_MOUNT,
    mirror_differences,
    prepare_masters_command,
)

BUCKET = "transcoding-bench-123456789012-campaign"
REPO_DIR = "/home/ubuntu/transcoding-bench"
WORK_DIR = "/home/ubuntu/work"

PLAN = json.dumps({"schema_version": "1", "videos": [{"video": "bbb", "master": {}}]})


def command(**overrides: str) -> list[str]:
    arguments = {"plan": PLAN, "bucket": BUCKET, "repo_dir": REPO_DIR, "work_dir": WORK_DIR}
    return prepare_masters_command(**(arguments | overrides))


def value_after(argv: list[str], flag: str) -> str:
    assert argv.count(flag) == 1, flag
    return argv[argv.index(flag) + 1]


def mounts(argv: list[str]) -> list[str]:
    return [argv[index + 1] for index, token in enumerate(argv) if token == "-v"]


class TestThePlanInTheArgv:
    def test_the_plan_travels_whole_as_a_single_argument(self):
        # O papel `masters` não tem `GetObject` (ADR-0016): o plano só chega ao
        # container pelo argv, e partido ele vira `jq` lendo o primeiro `{`.
        assert value_after(command(), "--plan") == PLAN

    def test_the_plan_is_the_only_argument_that_carries_it(self):
        argv = command()

        assert [token for token in argv if PLAN in token] == [PLAN]

    def test_a_plan_with_spaces_is_still_one_argument(self):
        plan = json.dumps({"videos": [{"video": "big buck bunny"}]}, indent=2)

        assert value_after(command(plan=plan), "--plan") == plan


class TestWhereTheCommandRuns:
    def test_the_script_comes_from_the_mounted_clone(self):
        argv = command()

        assert f"{REPO_DIR}/masters:{SCRIPTS_MOUNT}:ro" in mounts(argv)
        assert f"{SCRIPTS_MOUNT}/prepare.sh" in argv

    def test_the_work_dir_is_mounted_writable_and_named_by_its_mount_point(self):
        # O `--work-dir` é o caminho **de dentro** do container: passar o do host
        # faria a preparação escrever num diretório que o `docker run` não montou,
        # e os seis Masters morreriam com o container.
        argv = command()

        assert f"{WORK_DIR}:{WORK_MOUNT}" in mounts(argv)
        assert value_after(argv, "--work-dir") == WORK_MOUNT

    def test_the_versions_file_is_the_one_the_image_wrote(self):
        assert value_after(command(), "--versions-file") == IMAGE_VERSIONS_FILE

    def test_the_container_does_not_survive_the_run(self):
        assert "--rm" in command()


class TestTheCampaignBucket:
    def test_the_bucket_is_passed_as_given(self):
        assert value_after(command(), "--bucket") == BUCKET

    def test_the_pilot_bucket_would_be_passed_as_given_too(self):
        # A preparação sobe uma vez, no bucket que receber; é o `s3 sync` do
        # subcomando que leva os seis ao piloto.
        assert value_after(command(bucket="pilot"), "--bucket") == "pilot"


def objects(**sizes: int) -> list[S3Object]:
    return [S3Object(key=f"masters/{name}.mkv", size=size) for name, size in sizes.items()]


class TestTheMirrorOfTheTwoPrefixes:
    def test_two_identical_listings_agree(self):
        assert mirror_differences(objects(a=10, b=20), objects(a=10, b=20)) == ()

    def test_the_order_of_the_listing_does_not_matter(self):
        assert mirror_differences(objects(a=10, b=20), objects(b=20, a=10)) == ()

    def test_two_empty_listings_agree(self):
        # Vacuidade que só é aceitável porque quem chama já sabe que a preparação
        # subiu os seis: o checker do manifesto é o que conta os Masters.
        assert mirror_differences([], []) == ()

    def test_an_object_missing_from_the_destination_is_named(self):
        differences = mirror_differences(objects(a=10, b=20), objects(a=10))

        assert len(differences) == 1
        assert "masters/b.mkv" in differences[0]

    def test_an_object_only_in_the_destination_is_named(self):
        # O `s3 sync` não apaga: um Master de uma preparação anterior fica no
        # piloto, e é o único sinal de que os dois prefixos não são o mesmo.
        differences = mirror_differences(objects(a=10), objects(a=10, b=20))

        assert len(differences) == 1
        assert "masters/b.mkv" in differences[0]

    def test_a_size_that_differs_is_named_with_both_sizes(self):
        differences = mirror_differences(objects(a=10), objects(a=11))

        assert len(differences) == 1
        assert "masters/a.mkv" in differences[0]
        assert "10" in differences[0] and "11" in differences[0]

    def test_every_divergence_is_reported_at_once(self):
        differences = mirror_differences(objects(a=10, b=20, c=30), objects(a=11, c=30, d=40))

        assert len(differences) == 3
