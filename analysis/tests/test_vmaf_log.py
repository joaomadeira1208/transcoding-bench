# O log do `libvmaf` é texto de ferramenta lido em Python, e o parser tem dois
# modos de falha calados: um frame sem `float_ssim` entrando como zero ou sumindo
# da média, e um log truncado lido até onde deu, que encolhe a série sem que nada
# estoure. O caso feliz roda contra a captura real (D24a); a factory fica com as
# variações que o `libvmaf` não escreve sob encomenda.

from __future__ import annotations

import json

import pytest
from conftest import CAPTURES, CLIP_FRAMES, make_vmaf_log
from run_artifacts import ArtifactError
from vmaf_log import parse_vmaf_log

VMAF_CAPTURE = CAPTURES / "vmaf.json"


class TestTheRealLog:
    def test_every_frame_the_libvmaf_compared_is_counted(self):
        log = parse_vmaf_log(VMAF_CAPTURE.read_text(encoding="utf-8"))

        assert log.frames == CLIP_FRAMES

    def test_the_per_frame_series_is_the_one_the_libvmaf_wrote(self):
        raw = json.loads(VMAF_CAPTURE.read_text(encoding="utf-8"))
        log = parse_vmaf_log(VMAF_CAPTURE.read_text(encoding="utf-8"))

        assert log.vmaf == tuple(frame["metrics"]["vmaf"] for frame in raw["frames"])
        assert log.ssim == tuple(frame["metrics"]["float_ssim"] for frame in raw["frames"])

    def test_the_series_agrees_with_the_pooled_mean_the_libvmaf_reports(self):
        # A ponte entre a série e o resumo do próprio `libvmaf`: ler a feature
        # errada (`integer_motion`, digamos) daria uma série plausível e outra média.
        raw = json.loads(VMAF_CAPTURE.read_text(encoding="utf-8"))
        log = parse_vmaf_log(VMAF_CAPTURE.read_text(encoding="utf-8"))

        assert sum(log.vmaf) / log.frames == pytest.approx(
            raw["pooled_metrics"]["vmaf"]["mean"], abs=1e-5
        )


class TestRefusals:
    def test_a_frame_without_float_ssim_is_refused_naming_the_frame(self):
        raw = json.loads(make_vmaf_log())
        del raw["frames"][1]["metrics"]["float_ssim"]

        with pytest.raises(ArtifactError, match=r"frame 1: .*float_ssim"):
            parse_vmaf_log(json.dumps(raw))

    def test_a_frame_without_vmaf_is_refused_naming_the_frame(self):
        raw = json.loads(make_vmaf_log())
        del raw["frames"][2]["metrics"]["vmaf"]

        with pytest.raises(ArtifactError, match=r"frame 2: .*vmaf"):
            parse_vmaf_log(json.dumps(raw))

    @pytest.mark.parametrize("value", ["91.5", None, True, float("nan")])
    def test_a_score_that_is_not_a_finite_number_is_refused(self, value):
        raw = json.loads(make_vmaf_log())
        raw["frames"][0]["metrics"]["vmaf"] = value

        with pytest.raises(ArtifactError, match=r"frame 0: .*vmaf"):
            parse_vmaf_log(json.dumps(raw))

    def test_a_truncated_log_is_refused_as_truncated(self):
        raw = make_vmaf_log()

        with pytest.raises(ArtifactError, match="truncado"):
            parse_vmaf_log(raw[: len(raw) // 2])

    def test_an_empty_log_is_refused(self):
        with pytest.raises(ArtifactError, match="vazio"):
            parse_vmaf_log("")

    @pytest.mark.parametrize("frames", [[], None, "120"])
    def test_a_log_without_a_frame_series_is_refused(self, frames):
        with pytest.raises(ArtifactError, match="frames"):
            parse_vmaf_log(json.dumps({"version": "6375a4b", "frames": frames}))

    def test_a_log_that_is_not_an_object_is_refused(self):
        with pytest.raises(ArtifactError, match="objeto"):
            parse_vmaf_log("[]")
