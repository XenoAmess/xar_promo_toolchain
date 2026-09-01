from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from xar_promo.audio import (  # noqa: E402
    AudioMixSpec,
    AudioMixValidationError,
    AudioStem,
    DuckWindow,
    plan_audio_mix,
    validate_audio_mix,
)
from xar_promo.render import (  # noqa: E402
    RenderOptions,
    build_filtergraph,
    plan_render,
    plan_render_multitrack,
)
from xar_promo.model import new_project_config  # noqa: E402
from xar_promo.pipeline import (  # noqa: E402
    PipelineDependencies,
    PipelineDraft,
    SegmentDraft,
    run_pipeline,
)
from xar_promo.process import run_command  # noqa: E402
from xar_promo.sources import (  # noqa: E402
    VIDEO,
    VisualProbeResult,
    VisualSource,
)


class AudioModelTests(unittest.TestCase):
    def test_stem_and_mix_normalize_sequences_and_mapping(self) -> None:
        stem = AudioStem(
            "narration",
            Path("audio/narration.wav"),
            trim_start_seconds=1,
            trim_duration_seconds=4,
            delay_seconds=0.25,
            gain_db=-3,
            fade_in_seconds=0.2,
            fade_out_seconds=0.4,
            duck_windows=[DuckWindow(2, 3, -6)],  # type: ignore[arg-type]
        )
        spec = AudioMixSpec([stem], duration_seconds=5)  # type: ignore[arg-type]
        self.assertIsInstance(spec.stems, tuple)
        self.assertEqual(spec.stems[0].stem_id, "narration")
        self.assertEqual(spec.to_mapping()["sample_rate"], 48_000)
        self.assertEqual(spec.to_mapping()["stems"][0]["trim_duration_seconds"], "4.000000")

    def test_invalid_windows_and_target_bounds_are_rejected(self) -> None:
        with self.assertRaises(AudioMixValidationError):
            DuckWindow(2, 1)
        with self.assertRaises(AudioMixValidationError):
            AudioStem(
                "music",
                Path("music.wav"),
                duck_windows=(DuckWindow(0, 2), DuckWindow(1, 3)),
            )
        spec = AudioMixSpec(
            (
                AudioStem("music", Path("music.wav"), duck_windows=(DuckWindow(4, 6),)),
            ),
            duration_seconds=5,
        )
        with self.assertRaisesRegex(AudioMixValidationError, "duck window"):
            plan_audio_mix(spec)

        with self.assertRaises(AudioMixValidationError):
            AudioStem("overflow", Path("audio.wav"), delay_seconds=10**400)

    def test_mix_duration_must_match_render_duration(self) -> None:
        spec = AudioMixSpec((AudioStem("voice", Path("voice.wav")),), duration_seconds=4)
        with self.assertRaisesRegex(AudioMixValidationError, "does not match"):
            validate_audio_mix(spec, duration_seconds=5)
        with self.assertRaises(AudioMixValidationError):
            plan_audio_mix(AudioMixSpec((AudioStem("voice", Path("voice.wav")),)))

    def test_channel_contract_rejects_layouts_without_portable_adelay_support(self) -> None:
        with self.assertRaisesRegex(AudioMixValidationError, "supported range 1..8"):
            AudioMixSpec(
                (AudioStem("voice", Path("voice.wav")),),
                channels=9,
            )


class AudioPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.options = RenderOptions(width=640, height=360, fps=30, duration_seconds=8)

    def test_filtergraph_is_deterministic_and_contains_all_operations(self) -> None:
        spec = AudioMixSpec(
            (
                AudioStem(
                    "voice",
                    Path("raw/voice.wav"),
                    trim_start_seconds=1.25,
                    trim_duration_seconds=4,
                    delay_seconds=0.5,
                    gain_db=2,
                    fade_in_seconds=0.25,
                    fade_out_seconds=0.5,
                ),
                AudioStem(
                    "music",
                    Path("raw/music.wav"),
                    gain_db=-4,
                    duck_windows=(DuckWindow(2, 5, -12),),
                ),
            ),
            duration_seconds=8,
        )
        first = plan_audio_mix(spec, input_start_index=1)
        second = plan_audio_mix(spec, input_start_index=1)
        self.assertEqual(first, second)
        graph = first.filtergraph
        for expected in (
            "[1:a]",
            "[2:a]",
            "atrim=start=1.250000:duration=4.000000",
            "adelay=500.000000|500.000000",
            "volume=2.000000dB",
            "afade=t=in",
            "afade=t=out",
            "between(t,2.000000,5.000000)",
            "amix=inputs=2:duration=longest:dropout_transition=0",
            "[a]",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, graph)
        mapping = first.to_mapping()
        self.assertEqual(mapping["filtergraph"], graph)
        self.assertEqual(mapping["input_start_index"], 1)

    def test_fade_out_uses_clipped_effective_end_when_trim_exceeds_target(self) -> None:
        """A long stem must fade at the render boundary, not after it."""

        spec = AudioMixSpec(
            (
                AudioStem(
                    "music",
                    Path("music.wav"),
                    trim_duration_seconds=30,
                    delay_seconds=0.5,
                    fade_out_seconds=0.25,
                ),
            ),
            duration_seconds=2,
        )
        graph = plan_audio_mix(spec).filtergraph
        self.assertIn("afade=t=out:st=1.750000:d=0.250000", graph)

    def test_multichannel_delay_uses_explicit_layout_and_every_channel(self) -> None:
        spec = AudioMixSpec(
            (AudioStem("surround", Path("surround.wav"), delay_seconds=0.5),),
            duration_seconds=2,
            channels=6,
        )
        graph = plan_audio_mix(spec).filtergraph
        self.assertIn("aformat=sample_fmts=fltp:channel_layouts=5.1", graph)
        self.assertIn("adelay=" + "|".join(["500.000000"] * 6), graph)
        self.assertNotIn("adelay:all", graph)

    def test_standalone_ffmpeg_argv_retains_graph_and_input_order(self) -> None:
        spec = AudioMixSpec(
            (AudioStem("voice", Path("voice.wav")),), duration_seconds=2
        )
        plan = plan_audio_mix(spec)
        argv = plan.ffmpeg_argv(
            ffmpeg=Path("tools/ffmpeg"), output=Path("mixed.m4a")
        )
        self.assertEqual(argv[0], "tools/ffmpeg")
        self.assertIn("-filter_complex", argv)
        self.assertEqual(argv[argv.index("-filter_complex") + 1], plan.filtergraph)
        self.assertEqual(argv[argv.index("-map") + 1], "[a]")
        self.assertEqual(argv[-1], "mixed.m4a")

    def test_standalone_aac_rejects_known_incompatible_output_suffixes(self) -> None:
        spec = AudioMixSpec(
            (AudioStem("voice", Path("voice.wav")),), duration_seconds=2
        )
        plan = plan_audio_mix(spec)
        for suffix in (".wav", ".mp3"):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(
                AudioMixValidationError, "incompatible with output suffix"
            ):
                plan.ffmpeg_argv(ffmpeg="ffmpeg", output=f"mixed{suffix}")

    def test_standalone_non_iso_container_does_not_receive_faststart(self) -> None:
        spec = AudioMixSpec(
            (AudioStem("voice", Path("voice.wav")),), duration_seconds=2
        )
        plan = plan_audio_mix(spec)
        argv = plan.ffmpeg_argv(
            ffmpeg="ffmpeg", output="mixed.wav", audio_codec="pcm_s16le"
        )
        self.assertNotIn("-movflags", argv)

    def test_standalone_argv_rejects_embedded_input_offset(self) -> None:
        spec = AudioMixSpec(
            (AudioStem("voice", Path("voice.wav")),), duration_seconds=2
        )
        plan = plan_audio_mix(spec, input_start_index=1)
        with self.assertRaisesRegex(AudioMixValidationError, "input_start_index=0"):
            plan.ffmpeg_argv(ffmpeg="ffmpeg", output="mixed.m4a")

    def test_render_plan_adds_ordered_stem_inputs_and_keeps_filtergraph_auditable(self) -> None:
        spec = AudioMixSpec(
            (
                AudioStem("voice", Path("raw/voice.wav")),
                AudioStem("game", Path("raw/game.wav"), delay_seconds=0.1),
            ),
            duration_seconds=8,
        )
        plan = plan_render(
            ffmpeg=Path("tools/ffmpeg"),
            video_input=Path("raw/gameplay.mkv"),
            # This legacy argument is intentionally ignored by the opt-in
            # branch; the stem paths become the additional inputs.
            audio_input=Path("raw/legacy.wav"),
            partial_output=Path("partial/video.partial.mp4"),
            final_output=Path("output/video.mp4"),
            audit_directory=Path("audit"),
            options=self.options,
            audio_mix=spec,
        )
        argv = plan.commands[0].spec.argv
        self.assertEqual(
            argv[argv.index("-i") : argv.index("-filter_complex")],
            ("-i", "raw/gameplay.mkv", "-i", "raw/voice.wav", "-i", "raw/game.wav"),
        )
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("[1:a]", graph)
        self.assertIn("[2:a]", graph)
        self.assertEqual(argv[argv.index("-map", argv.index("-filter_complex")) + 1], "[v]")
        self.assertEqual(argv[argv.index("-map", argv.index("-filter_complex")) + 3], "[a]")

    def test_legacy_plan_render_remains_byte_compatible_without_mix(self) -> None:
        arguments = {
            "ffmpeg": Path("tools/ffmpeg"),
            "video_input": Path("raw/gameplay.mkv"),
            "audio_input": Path("raw/narration.wav"),
            "partial_output": Path("partial/video.partial.mp4"),
            "final_output": Path("output/video.mp4"),
            "audit_directory": Path("audit"),
            "options": self.options,
        }
        legacy = plan_render(**arguments)
        graph = build_filtergraph(self.options)
        argv = legacy.commands[0].spec.argv
        self.assertEqual(argv[argv.index("-filter_complex") + 1], graph)
        self.assertEqual(
            argv[argv.index("-i") : argv.index("-filter_complex")],
            ("-i", "raw/gameplay.mkv", "-i", "raw/narration.wav"),
        )


class AudioPipelineTests(unittest.TestCase):
    def test_pipeline_records_opted_in_stems_as_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            visual_path = root / "visual.mkv"
            narration_path = root / "narration.wav"
            game_path = root / "game.wav"
            visual_path.write_bytes(b"visual")
            narration_path.write_bytes(b"narration")
            game_path.write_bytes(b"game")
            spec = AudioMixSpec(
                (
                    AudioStem("narration", narration_path),
                    AudioStem("game", game_path, delay_seconds=0.1),
                ),
                duration_seconds=1.0,
            )
            segment = SegmentDraft(
                segment_id="opening",
                visual_source=VisualSource(
                    source_id="opening-source",
                    kind=VIDEO,
                    path=visual_path,
                    origin="audio-test",
                ),
                render_options=RenderOptions(320, 180, 24, 1.0),
                subtitles={"en": "opening"},
                prepared_narration=narration_path,
                audio_mix=spec,
            )

            def audited_runner(specification, *, audit_directory):
                def complete(argv, **_kwargs):
                    partial = specification.partial_artifacts[0]
                    effective = partial if partial.is_absolute() else specification.cwd / partial
                    effective.parent.mkdir(parents=True, exist_ok=True)
                    effective.write_bytes(b"rendered")
                    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

                return run_command(specification, audit_directory=audit_directory, run=complete)

            result = run_pipeline(
                PipelineDraft(
                    config=new_project_config(
                        "audio-pipeline-test",
                        "Audio pipeline test",
                        "en",
                        ["en"],
                        "adapter",
                        "preset",
                    ),
                    segments=(segment,),
                    deliverable_relative_path=Path("deliverables/final.mp4"),
                    deliverable_artifact_id="deliverable",
                    deliverable_media_type="video/mp4",
                ),
                workdir=root / "attempt",
                dependencies=PipelineDependencies(
                    ffmpeg="ffmpeg",
                    subtitle_renderer=lambda *_args, **_kwargs: "[Events]\n",
                    command_runner=audited_runner,
                    visual_probe=lambda _path: VisualProbeResult(
                        media_type="video/x-matroska", width=320, height=180
                    ),
                ),
            )
            self.assertTrue(result.succeeded, result.failure)
            self.assertEqual(
                {item.artifact_id for item in result.artifacts if item.role == "audio-stem"},
                {"audio.opening.narration", "audio.opening.game"},
            )

    def test_pipeline_pins_relative_stems_and_supports_multitrack_planner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            visual_path = root / "visual.mkv"
            narration_path = root / "narration.wav"
            game_path = root / "raw" / "game.wav"
            visual_path.write_bytes(b"visual")
            narration_path.write_bytes(b"narration")
            game_path.parent.mkdir()
            game_path.write_bytes(b"game")
            spec = AudioMixSpec(
                (
                    AudioStem("narration", Path("narration.wav")),
                    AudioStem("game", Path("raw/game.wav"), delay_seconds=0.1),
                ),
                duration_seconds=1.0,
            )
            segment = SegmentDraft(
                segment_id="opening",
                visual_source=VisualSource(
                    source_id="opening-source",
                    kind=VIDEO,
                    path=visual_path,
                    origin="audio-relative-test",
                ),
                render_options=RenderOptions(320, 180, 24, 1.0),
                subtitles={"en": "opening"},
                prepared_narration=narration_path,
                audio_mix=spec,
            )
            planned: list[AudioMixSpec] = []

            def planner(**kwargs):
                planned.append(kwargs["audio_mix"])
                return plan_render_multitrack(**kwargs)

            def audited_runner(specification, *, audit_directory):
                def complete(argv, **_kwargs):
                    partial = specification.partial_artifacts[0]
                    effective = partial if partial.is_absolute() else specification.cwd / partial
                    effective.parent.mkdir(parents=True, exist_ok=True)
                    effective.write_bytes(b"rendered")
                    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

                return run_command(specification, audit_directory=audit_directory, run=complete)

            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                result = run_pipeline(
                    PipelineDraft(
                        config=new_project_config(
                            "audio-relative-test",
                            "Audio relative test",
                            "en",
                            ["en"],
                            "adapter",
                            "preset",
                        ),
                        segments=(segment,),
                        deliverable_relative_path=Path("deliverables/final.mp4"),
                        deliverable_artifact_id="deliverable",
                        deliverable_media_type="video/mp4",
                    ),
                    workdir=root / "attempt",
                    dependencies=PipelineDependencies(
                        ffmpeg="ffmpeg",
                        subtitle_renderer=lambda *_args, **_kwargs: "[Events]\n",
                        command_runner=audited_runner,
                        visual_probe=lambda _path: VisualProbeResult(
                            media_type="video/x-matroska", width=320, height=180
                        ),
                        render_planner=planner,
                    ),
                )
            finally:
                os.chdir(old_cwd)
            self.assertTrue(result.succeeded, result.failure)
            self.assertEqual(1, len(planned))
            self.assertTrue(all(stem.path.is_absolute() for stem in planned[0].stems))
            argv = next(
                item
                for item in result.artifacts
                if item.artifact_id == "audio.opening.game"
            )
            self.assertEqual(game_path.resolve(), argv.path)


if __name__ == "__main__":
    unittest.main()
