"""Project-neutral, deterministic multi-track audio planning.

The audio layer deliberately describes *how* already-produced audio files are
assembled.  It does not acquire media, inspect a game, choose music, or make a
 loudness/rights judgement.  A caller supplies immutable stems and the render
 layer turns the resulting filtergraph into an auditable, shell-free FFmpeg
 command.

``DuckWindow`` is intentionally a small deterministic primitive rather than a
side-chain compressor.  It attenuates a stem during explicitly named output
time windows.  This makes plans reproducible even when no live microphone or
analysis stream is available; projects that need semantic voice detection can
prepare those windows outside this package.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .errors import PromoToolchainError
from .process import command_token


class AudioMixError(PromoToolchainError):
    """An audio mix declaration cannot form a deterministic plan."""


class AudioMixValidationError(AudioMixError):
    """A typed audio stem or mix specification is invalid."""


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_DB = 120.0
_EPSILON = 1e-6


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise AudioMixValidationError(
            f"{label} must be a portable identifier of at most 128 characters"
        )
    return value


def _finite_number(
    value: object,
    label: str,
    *,
    allow_zero: bool = True,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioMixValidationError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise AudioMixValidationError(f"{label} must be a finite number")
    lower = 0.0 if minimum is None else minimum
    if (not allow_zero and result <= 0.0) or result < lower:
        qualifier = "greater than zero" if not allow_zero else "non-negative"
        raise AudioMixValidationError(f"{label} must be {qualifier}")
    if maximum is not None and result > maximum:
        raise AudioMixValidationError(f"{label} must be <= {maximum:g}")
    return 0.0 if abs(result) < _EPSILON else result


def _duration(value: object, label: str) -> float:
    return _finite_number(value, label, allow_zero=False)


def _path(value: object, label: str) -> Path:
    try:
        raw = os.fspath(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise AudioMixValidationError(f"{label} must be a path") from exc
    if isinstance(raw, bytes):
        raise AudioMixValidationError(f"{label} must be a text path")
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise AudioMixValidationError(f"{label} must be a non-empty NUL-free path")
    return Path(raw)


def _integer(value: object, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AudioMixValidationError(f"{label} must be an integer >= {minimum}")
    return value


def _format_seconds(value: float) -> str:
    """Use one stable six-decimal representation in every filtergraph."""

    # Avoid ``-0.000000`` after rounding a very small negative value.  Values
    # have already been range checked by the public constructors.
    normalized = 0.0 if abs(value) < _EPSILON else value
    return f"{normalized:.6f}"


def _format_db(value: float) -> str:
    return f"{value:.6f}"


@dataclass(frozen=True, slots=True)
class DuckWindow:
    """An explicit output interval during which a stem is attenuated.

    ``gain_db`` must be zero or negative.  The interval is expressed on the
    final mix timeline (after the stem's trim and delay), and is therefore
    independent of source-media duration probing.
    """

    start_seconds: float
    end_seconds: float
    gain_db: float = -12.0

    def __post_init__(self) -> None:
        start = _finite_number(self.start_seconds, "duck window start")
        end = _finite_number(self.end_seconds, "duck window end", allow_zero=False)
        if end <= start:
            raise AudioMixValidationError(
                "duck window end_seconds must be greater than start_seconds"
            )
        gain = _finite_number(
            self.gain_db,
            "duck window gain_db",
            minimum=-_MAX_DB,
            maximum=0.0,
        )
        object.__setattr__(self, "start_seconds", start)
        object.__setattr__(self, "end_seconds", end)
        object.__setattr__(self, "gain_db", gain)

    def to_mapping(self) -> dict[str, object]:
        return {
            "start_seconds": _format_seconds(self.start_seconds),
            "end_seconds": _format_seconds(self.end_seconds),
            "gain_db": _format_db(self.gain_db),
        }


# The longer spelling is useful to callers and keeps the public vocabulary
# self-documenting without introducing a second implementation.
DuckingWindow = DuckWindow


@dataclass(frozen=True, slots=True)
class AudioStem:
    """One source audio file and deterministic transformations for it.

    ``trim_start_seconds`` and ``trim_duration_seconds`` address the source
    timeline.  ``delay_seconds`` and duck windows address the output timeline.
    A missing trim duration means the stem is allowed to run until the mix
    duration; it is padded or clipped by the final mix graph.
    """

    stem_id: str
    path: Path
    trim_start_seconds: float = 0.0
    trim_duration_seconds: float | None = None
    delay_seconds: float = 0.0
    gain_db: float = 0.0
    fade_in_seconds: float = 0.0
    fade_out_seconds: float = 0.0
    duck_windows: tuple[DuckWindow, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.stem_id, "audio stem_id")
        object.__setattr__(self, "path", _path(self.path, "audio stem path"))
        trim_start = _finite_number(
            self.trim_start_seconds, "audio stem trim_start_seconds"
        )
        trim_duration = self.trim_duration_seconds
        if trim_duration is not None:
            trim_duration = _duration(
                trim_duration, "audio stem trim_duration_seconds"
            )
        delay = _finite_number(self.delay_seconds, "audio stem delay_seconds")
        gain = _finite_number(
            self.gain_db,
            "audio stem gain_db",
            minimum=-_MAX_DB,
            maximum=_MAX_DB,
        )
        fade_in = _finite_number(self.fade_in_seconds, "audio stem fade_in_seconds")
        fade_out = _finite_number(self.fade_out_seconds, "audio stem fade_out_seconds")
        if trim_duration is not None and fade_in > trim_duration:
            raise AudioMixValidationError(
                "audio stem fade_in_seconds cannot exceed trim_duration_seconds"
            )
        if trim_duration is not None and fade_out > trim_duration:
            raise AudioMixValidationError(
                "audio stem fade_out_seconds cannot exceed trim_duration_seconds"
            )
        try:
            windows = tuple(self.duck_windows)
        except TypeError as exc:
            raise AudioMixValidationError(
                "audio stem duck_windows must be a sequence of DuckWindow values"
            ) from exc
        for index, window in enumerate(windows):
            if not isinstance(window, DuckWindow):
                raise AudioMixValidationError(
                    f"audio stem duck_windows[{index}] must be a DuckWindow"
                )
        # Canonicalize declaration order.  The windows are non-overlapping, so
        # sorting cannot change their meaning and makes an equivalent spec
        # produce byte-identical filtergraphs regardless of input ordering.
        ordered = sorted(
            windows,
            key=lambda item: (item.start_seconds, item.end_seconds, item.gain_db),
        )
        for previous, current in zip(ordered, ordered[1:]):
            if current.start_seconds < previous.end_seconds:
                raise AudioMixValidationError(
                    f"audio stem duck windows overlap: {previous.start_seconds:g}.."
                    f"{previous.end_seconds:g} and {current.start_seconds:g}.."
                    f"{current.end_seconds:g}"
                )
        object.__setattr__(self, "trim_start_seconds", trim_start)
        object.__setattr__(self, "trim_duration_seconds", trim_duration)
        object.__setattr__(self, "delay_seconds", delay)
        object.__setattr__(self, "gain_db", gain)
        object.__setattr__(self, "fade_in_seconds", fade_in)
        object.__setattr__(self, "fade_out_seconds", fade_out)
        object.__setattr__(self, "duck_windows", tuple(ordered))

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "stem_id": self.stem_id,
            "path": self.path.as_posix(),
            "trim_start_seconds": _format_seconds(self.trim_start_seconds),
            "delay_seconds": _format_seconds(self.delay_seconds),
            "gain_db": _format_db(self.gain_db),
            "fade_in_seconds": _format_seconds(self.fade_in_seconds),
            "fade_out_seconds": _format_seconds(self.fade_out_seconds),
            "duck_windows": [item.to_mapping() for item in self.duck_windows],
        }
        if self.trim_duration_seconds is not None:
            result["trim_duration_seconds"] = _format_seconds(
                self.trim_duration_seconds
            )
        return result


@dataclass(frozen=True, slots=True)
class AudioMixSpec:
    """An ordered set of stems normalized to one output duration."""

    stems: tuple[AudioStem, ...]
    duration_seconds: float | None = None
    sample_rate: int = 48_000
    channels: int = 2
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            stems = tuple(self.stems)
        except TypeError as exc:
            raise AudioMixValidationError("audio mix stems must be a sequence") from exc
        if not stems:
            raise AudioMixValidationError("audio mix requires at least one stem")
        if any(not isinstance(item, AudioStem) for item in stems):
            raise AudioMixValidationError("audio mix stems must contain AudioStem values")
        ids = [item.stem_id for item in stems]
        if len(ids) != len(set(ids)):
            raise AudioMixValidationError("audio mix stem_id values must be unique")
        duration = self.duration_seconds
        if duration is not None:
            duration = _duration(duration, "audio mix duration_seconds")
        sample_rate = _integer(self.sample_rate, "audio mix sample_rate")
        channels = _integer(self.channels, "audio mix channels")
        if channels > 32:
            raise AudioMixValidationError("audio mix channels must be <= 32")
        if not isinstance(self.metadata, Mapping):
            raise AudioMixValidationError("audio mix metadata must be a mapping")
        metadata: dict[str, object] = {}
        for key, value in self.metadata.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                raise AudioMixValidationError("audio mix metadata keys must be NUL-free text")
            metadata[key] = value
        try:
            metadata = json.loads(
                json.dumps(metadata, ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AudioMixValidationError(
                "audio mix metadata must contain only JSON values"
            ) from exc
        object.__setattr__(self, "stems", stems)
        object.__setattr__(self, "duration_seconds", duration)
        object.__setattr__(self, "sample_rate", sample_rate)
        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "metadata", MappingProxyType(metadata))

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "stems": [item.to_mapping() for item in self.stems],
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }
        if self.duration_seconds is not None:
            result["duration_seconds"] = _format_seconds(self.duration_seconds)
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result


@dataclass(frozen=True, slots=True)
class AudioMixPlan:
    """Pure plan data consumed by :mod:`xar_promo.render`.

    The filtergraph is retained separately from the final FFmpeg argv so a
    project can preserve it as an audit sidecar before executing the command.
    ``input_paths`` are ordered exactly as the corresponding ``[N:a]`` labels.
    """

    spec: AudioMixSpec
    duration_seconds: float
    filtergraph: str
    input_paths: tuple[Path, ...]
    input_start_index: int = 0
    output_label: str = "a"
    sample_rate: int = 48_000
    channels: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.spec, AudioMixSpec):
            raise AudioMixValidationError("audio mix plan spec must be an AudioMixSpec")
        duration = _duration(self.duration_seconds, "audio mix plan duration_seconds")
        if not isinstance(self.filtergraph, str) or not self.filtergraph.strip():
            raise AudioMixValidationError("audio mix plan filtergraph must be non-empty")
        if isinstance(self.input_start_index, bool) or not isinstance(
            self.input_start_index, int
        ) or self.input_start_index < 0:
            raise AudioMixValidationError("audio mix plan input_start_index must be >= 0")
        _identifier(self.output_label, "audio mix plan output_label")
        try:
            paths = tuple(_path(item, "audio mix input path") for item in self.input_paths)
        except TypeError as exc:
            raise AudioMixValidationError(
                "audio mix plan input_paths must be a sequence"
            ) from exc
        if paths != tuple(item.path for item in self.spec.stems):
            raise AudioMixValidationError(
                "audio mix plan input_paths must match spec stem paths in order"
            )
        sample_rate = _integer(self.sample_rate, "audio mix plan sample_rate")
        channels = _integer(self.channels, "audio mix plan channels")
        if channels > 32:
            raise AudioMixValidationError("audio mix plan channels must be <= 32")
        object.__setattr__(self, "duration_seconds", duration)
        object.__setattr__(self, "input_paths", paths)
        object.__setattr__(self, "sample_rate", sample_rate)
        object.__setattr__(self, "channels", channels)

    @property
    def input_arguments(self) -> tuple[str, ...]:
        """Return the ordered ``-i`` argument pairs for an FFmpeg command."""

        result: list[str] = []
        for path in self.input_paths:
            result.extend(("-i", command_token(path)))
        return tuple(result)

    def ffmpeg_argv(
        self,
        *,
        ffmpeg: str | os.PathLike[str],
        output: str | os.PathLike[str],
        audio_codec: str = "aac",
        audio_bitrate: str = "192k",
        overwrite: bool = False,
    ) -> tuple[str, ...]:
        """Build a standalone, shell-free FFmpeg audio-mix command.

        Renderers that also have a video stream normally use
        :func:`xar_promo.render.plan_render`, which combines this graph with
        the video graph.  This helper is provided for audio-only pre-mixes and
        for audit tooling; callers can pass the returned argv directly to
        :class:`~xar_promo.process.CommandSpec` and retain it in the normal
        process audit directory.
        """

        try:
            executable = command_token(ffmpeg)
            destination = command_token(output)
        except (TypeError, ValueError) as exc:
            raise AudioMixValidationError("ffmpeg and output must be paths or text") from exc
        if not isinstance(executable, str) or not executable or "\x00" in executable:
            raise AudioMixValidationError("ffmpeg must be non-empty NUL-free text")
        if not isinstance(destination, str) or not destination or "\x00" in destination:
            raise AudioMixValidationError("output must be non-empty NUL-free text")
        for label, value in (("audio_codec", audio_codec), ("audio_bitrate", audio_bitrate)):
            if not isinstance(value, str) or not value or "\x00" in value:
                raise AudioMixValidationError(f"{label} must be non-empty NUL-free text")
        if not isinstance(overwrite, bool):
            raise AudioMixValidationError("overwrite must be boolean")
        argv: list[str] = [
            executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if overwrite else "-n",
        ]
        argv.extend(self.input_arguments)
        argv.extend(
            (
                "-filter_complex",
                self.filtergraph,
                "-map",
                f"[{self.output_label}]",
                "-t",
                _format_seconds(self.duration_seconds),
                "-vn",
                "-c:a",
                audio_codec,
                "-b:a",
                audio_bitrate,
                "-ar",
                str(self.sample_rate),
                "-ac",
                str(self.channels),
                "-movflags",
                "+faststart",
                destination,
            )
        )
        return tuple(argv)

    def to_mapping(self) -> dict[str, object]:
        return {
            "duration_seconds": _format_seconds(self.duration_seconds),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "input_start_index": self.input_start_index,
            "output_label": self.output_label,
            "input_paths": [path.as_posix() for path in self.input_paths],
            "filtergraph": self.filtergraph,
            "spec": self.spec.to_mapping(),
        }


def _validate_target_duration(spec: AudioMixSpec, duration: float) -> float:
    target = _duration(duration, "audio mix target duration_seconds")
    if spec.duration_seconds is not None and not math.isclose(
        spec.duration_seconds, target, rel_tol=0.0, abs_tol=_EPSILON
    ):
        raise AudioMixValidationError(
            "audio mix duration_seconds does not match the render duration"
        )
    for stem in spec.stems:
        available = max(0.0, target - stem.delay_seconds)
        if stem.trim_duration_seconds is not None:
            available = min(available, stem.trim_duration_seconds)
        if stem.fade_in_seconds > available + _EPSILON:
            raise AudioMixValidationError(
                f"audio stem {stem.stem_id!r} fade_in_seconds exceeds target timeline"
            )
        if stem.fade_out_seconds > available + _EPSILON:
            raise AudioMixValidationError(
                f"audio stem {stem.stem_id!r} fade_out_seconds exceeds target timeline"
            )
        for window in stem.duck_windows:
            if window.end_seconds > target + _EPSILON:
                raise AudioMixValidationError(
                    f"audio stem {stem.stem_id!r} duck window exceeds mix duration"
                )
    return target


def validate_audio_mix(spec: AudioMixSpec, *, duration_seconds: float) -> AudioMixSpec:
    """Validate a mix against a render duration without building a command."""

    if not isinstance(spec, AudioMixSpec):
        raise AudioMixValidationError("audio mix must be an AudioMixSpec")
    _validate_target_duration(spec, duration_seconds)
    return spec


def _channel_format(channels: int) -> str:
    if channels == 1:
        return "aformat=sample_fmts=fltp:channel_layouts=mono"
    if channels == 2:
        return "aformat=sample_fmts=fltp:channel_layouts=stereo"
    # FFmpeg's generic aformat does not require us to invent a channel-layout
    # name for arbitrary project output.  The final ``-ac`` option handles the
    # requested count while sample format remains deterministic.
    return "aformat=sample_fmts=fltp"


def _build_filtergraph(
    spec: AudioMixSpec,
    *,
    duration_seconds: float,
    sample_rate: int,
    channels: int,
    input_start_index: int,
    output_label: str,
) -> str:
    target = _validate_target_duration(spec, duration_seconds)
    rate = _integer(sample_rate, "audio mix sample_rate")
    channel_count = _integer(channels, "audio mix channels")
    if isinstance(input_start_index, bool) or not isinstance(input_start_index, int):
        raise AudioMixValidationError("audio mix input_start_index must be an integer")
    if input_start_index < 0:
        raise AudioMixValidationError("audio mix input_start_index must be >= 0")
    _identifier(output_label, "audio mix output_label")

    chains: list[str] = []
    labels: list[str] = []
    target_text = _format_seconds(target)
    for offset, stem in enumerate(spec.stems):
        input_index = input_start_index + offset
        label = f"xar_mix_{offset}"
        labels.append(f"[{label}]")
        filters = [
            "atrim="
            + f"start={_format_seconds(stem.trim_start_seconds)}"
            + (
                f":duration={_format_seconds(stem.trim_duration_seconds)}"
                if stem.trim_duration_seconds is not None
                else ""
            ),
            "asetpts=PTS-STARTPTS",
        ]
        if stem.delay_seconds > 0.0:
            # ``adelay`` accepts one millisecond value per channel.  Emit the
            # exact same value for every requested output channel instead of
            # relying on version-specific ``all`` options.
            delay_value = _format_seconds(stem.delay_seconds * 1000.0)
            filters.append("adelay=" + "|".join([delay_value] * channel_count))
        if stem.gain_db != 0.0:
            filters.append(f"volume={_format_db(stem.gain_db)}dB")
        if stem.fade_in_seconds > 0.0:
            filters.append(
                "afade=t=in:"
                f"st={_format_seconds(stem.delay_seconds)}:"
                f"d={_format_seconds(stem.fade_in_seconds)}"
            )
        if stem.fade_out_seconds > 0.0:
            # The rendered stem ends at the earlier of its declared trim end
            # and the mix boundary (after delay).  Using the raw trim duration
            # here would place the fade beyond the output and silently disable
            # it whenever a long source is clipped by the target duration.
            available = max(0.0, target - stem.delay_seconds)
            if stem.trim_duration_seconds is not None:
                available = min(available, stem.trim_duration_seconds)
            effective_end = stem.delay_seconds + available
            fade_start = max(
                stem.delay_seconds,
                effective_end - stem.fade_out_seconds,
            )
            filters.append(
                "afade=t=out:"
                f"st={_format_seconds(fade_start)}:"
                f"d={_format_seconds(stem.fade_out_seconds)}"
            )
        for window in stem.duck_windows:
            # Quoting the enable expression is libavfilter syntax, not shell
            # quoting; CommandSpec still executes with shell=False.
            filters.append(
                "volume=enable='between(t,"
                f"{_format_seconds(window.start_seconds)},"
                f"{_format_seconds(window.end_seconds)})':"
                f"volume={_format_db(window.gain_db)}dB"
            )
        filters.extend(
            (
                f"aresample={rate}:async=0",
                _channel_format(channel_count),
                "apad",
                f"atrim=duration={target_text}",
                "asetpts=N/SR/TB",
            )
        )
        chains.append(f"[{input_index}:a]" + ",".join(filters) + f"[{label}]")

    if len(labels) == 1:
        chains.append(f"{labels[0]}anull[{output_label}]")
    else:
        chains.append(
            "".join(labels)
            + f"amix=inputs={len(labels)}:duration=longest:"
            "dropout_transition=0,"
            f"aresample={rate}:async=0,"
            + _channel_format(channel_count)
            + f",apad,atrim=duration={target_text},asetpts=N/SR/TB[{output_label}]"
        )
    return ";".join(chains)


def plan_audio_mix(
    spec: AudioMixSpec,
    *,
    duration_seconds: float | None = None,
    sample_rate: int | None = None,
    channels: int | None = None,
    input_start_index: int = 0,
    output_label: str = "a",
) -> AudioMixPlan:
    """Build a deterministic, auditable audio filtergraph description.

    ``duration_seconds`` is required when the spec did not declare one.  When
    both values are present they must agree exactly to six-decimal tolerance;
    this prevents a project from silently rendering a different timeline than
    the one it reviewed.
    """

    if not isinstance(spec, AudioMixSpec):
        raise AudioMixValidationError("audio mix must be an AudioMixSpec")
    target = spec.duration_seconds if duration_seconds is None else duration_seconds
    if target is None:
        raise AudioMixValidationError(
            "audio mix duration_seconds is required either on the spec or the plan"
        )
    effective_rate = spec.sample_rate if sample_rate is None else sample_rate
    effective_channels = spec.channels if channels is None else channels
    filtergraph = _build_filtergraph(
        spec,
        duration_seconds=target,
        sample_rate=effective_rate,
        channels=effective_channels,
        input_start_index=input_start_index,
        output_label=output_label,
    )
    return AudioMixPlan(
        spec=spec,
        duration_seconds=float(target),
        filtergraph=filtergraph,
        input_paths=tuple(item.path for item in spec.stems),
        input_start_index=input_start_index,
        output_label=output_label,
        sample_rate=int(effective_rate),
        channels=int(effective_channels),
    )


__all__ = [
    "AudioMixError",
    "AudioMixPlan",
    "AudioMixSpec",
    "AudioMixValidationError",
    "AudioStem",
    "DuckWindow",
    "DuckingWindow",
    "plan_audio_mix",
    "validate_audio_mix",
]
