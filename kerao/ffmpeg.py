import dataclasses
import hashlib
import json
import shutil
from pathlib import Path

from common import (
    ARTIFACT_FIELDS,
    SLICES,
    BuildError,
    Check,
    Slice,
    counterexample,
    listed,
)
from framework import delivered_framework, framework_binary

FFMPEG_TAG = "n9.0.2-kerao.1"
FFMPEG_COMMIT = "ae49c6ac4a2409553ca0f9a00f9e252cbce632a0"
LINKED = ("libavutil", "libavcodec")
ARTIFACT_JSON = "artifact.json"
RECORD_SHAPE = {
    **dict.fromkeys(ARTIFACT_FIELDS, object),
    "libraries": [str],
    "repository": str,
    "tag": str,
    "commit": str,
    "mode": str,
    "frameworks": [
        {"name": str, "slices": [{"identifier": str, "path": str, "sha256": str}]}
    ],
}
INPUT_FIELDS = ("repository", "tag", "commit", "mode", "frameworks")


@dataclasses.dataclass(frozen=True)
class FFmpeg:
    """The output directory of the FFmpeg fork's build and its artifact.json."""

    root: Path
    record: dict

    @property
    def libraries(self) -> tuple[str, ...]:
        return tuple(self.record["libraries"])


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shape_problems(value: object, shape: object, where: str) -> list[str]:
    """Where value departs from shape: a type, [shape] for a list of shape, or
    {key: shape} for an object with at least those keys."""
    if isinstance(shape, dict):
        if not isinstance(value, dict):
            return [f"{where} is not an object"]
        return [
            problem
            for key, inner in shape.items()
            for problem in (
                shape_problems(value[key], inner, f"{where}.{key}")
                if key in value
                else [f"{where} has no {key}"]
            )
        ]
    if isinstance(shape, list):
        if not isinstance(value, list):
            return [f"{where} is not a list"]
        return [
            problem
            for index, item in enumerate(value)
            for problem in shape_problems(item, shape[0], f"{where}[{index}]")
        ]
    return [] if isinstance(value, shape) else [f"{where} is not a {shape.__name__}"]


def check_ffmpeg_record(record: object) -> Check:
    """record is the parsed artifact.json of an FFmpeg output."""
    problems = shape_problems(record, RECORD_SHAPE, ARTIFACT_JSON)
    return Check("FFmpeg artifact.json", not problems, listed(problems))


def read_ffmpeg(root: Path) -> FFmpeg:
    path = root / ARTIFACT_JSON
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise BuildError(f"cannot read {path}: {error}") from error
    check = check_ffmpeg_record(record)
    if not check.ok:
        raise BuildError(f"{root}: {check.detail}")
    return FFmpeg(root=root, record=record)


def slice_dir(ffmpeg: FFmpeg, slice_: Slice, name: str) -> Path:
    return ffmpeg.root / f"{name}.xcframework" / slice_.identifier


def ffmpeg_binary(root: Path, slice_: Slice, name: str) -> Path:
    """The binary of library name for slice_ that the build links and loads."""
    return framework_binary(delivered_framework(root, slice_, name), slice_.layout)


def compile_args(ffmpeg: FFmpeg, slice_: Slice) -> list[str]:
    """Flags that find FFmpeg's headers and the frameworks libplacebo links."""
    return [
        f"-I{ffmpeg.root / 'include'}",
        *(f"-F{slice_dir(ffmpeg, slice_, name)}" for name in LINKED),
    ]


def rpath_args(ffmpeg: FFmpeg, slice_: Slice) -> list[str]:
    """Link flags that let a program built in place load every FFmpeg framework."""
    return [
        f"-Wl,-rpath,{slice_dir(ffmpeg, slice_, name)}" for name in ffmpeg.libraries
    ]


def check_ffmpeg_tag(ffmpeg: FFmpeg) -> Check:
    tag = ffmpeg.record["tag"]
    return Check(
        "FFmpeg tag",
        tag == FFMPEG_TAG,
        tag if tag == FFMPEG_TAG else f"{tag}, pinned {FFMPEG_TAG}",
    )


def check_ffmpeg_commit(ffmpeg: FFmpeg) -> Check:
    commit = ffmpeg.record["commit"]
    return Check(
        "FFmpeg commit",
        commit == FFMPEG_COMMIT,
        commit if commit == FFMPEG_COMMIT else f"{commit}, pinned {FFMPEG_COMMIT}",
    )


def check_ffmpeg_mode(ffmpeg: FFmpeg) -> Check:
    mode = ffmpeg.record["mode"]
    return Check(
        "FFmpeg mode",
        mode == "release",
        mode if mode == "release" else f"{mode}, a release build needs release",
    )


def recorded_binaries(ffmpeg: FFmpeg) -> dict[tuple[str, str], list[dict]]:
    """The slice entries of FFmpeg's artifact.json by library and slice identifier."""
    recorded: dict[tuple[str, str], list[dict]] = {}
    for framework in ffmpeg.record["frameworks"]:
        for entry in framework["slices"]:
            key = (framework["name"], entry["identifier"])
            recorded.setdefault(key, []).append(entry)
    return recorded


def binary_problems(
    ffmpeg: FFmpeg, slice_: Slice, name: str, entries: list[dict]
) -> list[str]:
    """entries are the records of library name in slice_."""
    binary = ffmpeg_binary(ffmpeg.root, slice_, name)
    if len(entries) != 1:
        return [f"{name} {slice_.identifier} has {len(entries)} records, not 1"]
    path = entries[0]["path"]
    if ffmpeg.root / path != binary:
        linked = binary.relative_to(ffmpeg.root)
        return [f"{name} {slice_.identifier} records {path}, not {linked}"]
    if not binary.is_file():
        return [f"no {path}"]
    if sha256(binary) != entries[0]["sha256"]:
        return [f"{path} differs from its sha256"]
    return []


def check_ffmpeg_binaries(ffmpeg: FFmpeg) -> Check:
    """FFmpeg's libraries must include LINKED, and its artifact.json must record
    exactly one binary per library and slice, the one the build links and
    loads, with its SHA-256, and no other binary."""
    recorded = recorded_binaries(ffmpeg)
    expected = [(slice_, name) for name in ffmpeg.libraries for slice_ in SLICES]
    keys = {(name, slice_.identifier) for slice_, name in expected}
    problems = [
        f"libraries has no {name}" for name in LINKED if name not in ffmpeg.libraries
    ]
    problems += [
        f"{name} {identifier} is not a listed library in a slice of the build"
        for name, identifier in recorded
        if (name, identifier) not in keys
    ]
    problems += [
        problem
        for slice_, name in expected
        for problem in binary_problems(
            ffmpeg, slice_, name, recorded.get((name, slice_.identifier), [])
        )
    ]
    return Check(
        "FFmpeg binaries",
        not problems,
        listed(problems) or f"{len(expected)} match their sha256",
    )


def preconditions(ffmpeg: FFmpeg, mode: str) -> list[Check]:
    checks = [
        check_ffmpeg_tag(ffmpeg),
        check_ffmpeg_commit(ffmpeg),
        check_ffmpeg_binaries(ffmpeg),
    ]
    if mode == "release":
        checks.append(check_ffmpeg_mode(ffmpeg))
    return checks


def inputs(ffmpeg: FFmpeg) -> list[dict]:
    """The identity of the FFmpeg artifact a build used, for artifact.json."""
    return [{field: ffmpeg.record[field] for field in INPUT_FIELDS}]


def fake_ffmpeg(tmp: Path, **fields: object) -> FFmpeg:
    """An FFmpeg output with a binary per framework and slice, recorded at the pins."""
    root = tmp / "ffmpeg"
    frameworks = []
    for name in ("libavutil", "libavcodec", "libswresample"):
        slices = []
        for slice_ in SLICES:
            binary = ffmpeg_binary(root, slice_, name)
            binary.parent.mkdir(parents=True)
            binary.write_bytes(f"{name} {slice_.identifier}".encode())
            slices.append(
                {
                    "identifier": slice_.identifier,
                    "path": str(binary.relative_to(root)),
                    "sha256": sha256(binary),
                }
            )
        frameworks.append({"name": name, "slices": slices})
    record = {
        "libraries": [framework["name"] for framework in frameworks],
        "repository": "https://github.com/keraolabs/FFmpeg",
        "tag": FFMPEG_TAG,
        "commit": FFMPEG_COMMIT,
        "mode": "release",
        "dirty": False,
        "built_at": "2026-11-01T16:11:11Z",
        "xcode_build": "27A266a",
        "sdks": {},
        "tools": {},
        "deps": [],
        "inputs": [],
        "frameworks": frameworks,
        "configure": {},
        "unverified": [],
        **fields,
    }
    (root / ARTIFACT_JSON).write_text(json.dumps(record))
    return read_ffmpeg(root)


def with_fields(ffmpeg: FFmpeg, **fields: object) -> FFmpeg:
    return dataclasses.replace(ffmpeg, record={**ffmpeg.record, **fields})


@counterexample
def ffmpeg_artifact_of_another_tag(tmp: Path) -> tuple[Check, Check]:
    return (
        check_ffmpeg_tag(fake_ffmpeg(tmp / "good")),
        check_ffmpeg_tag(fake_ffmpeg(tmp / "bad", tag="n9.0.2-7-g3f1da3281d")),
    )


@counterexample
def ffmpeg_artifact_of_another_commit(tmp: Path) -> tuple[Check, Check]:
    return (
        check_ffmpeg_commit(fake_ffmpeg(tmp / "good")),
        check_ffmpeg_commit(fake_ffmpeg(tmp / "bad", commit="0" * 40)),
    )


@counterexample
def ffmpeg_artifact_built_in_dev_mode(tmp: Path) -> tuple[Check, Check]:
    return (
        check_ffmpeg_mode(fake_ffmpeg(tmp / "good")),
        check_ffmpeg_mode(fake_ffmpeg(tmp / "bad", mode="dev")),
    )


@counterexample
def ffmpeg_binary_replaced(tmp: Path) -> tuple[Check, Check]:
    ffmpeg = fake_ffmpeg(tmp)
    good = check_ffmpeg_binaries(ffmpeg)
    entry = ffmpeg.record["frameworks"][0]["slices"][0]
    (ffmpeg.root / entry["path"]).write_bytes(b"another build")
    return good, check_ffmpeg_binaries(ffmpeg)


@counterexample
def ffmpeg_framework_record_without_slices(tmp: Path) -> tuple[Check, Check]:
    record = fake_ffmpeg(tmp).record
    return (
        check_ffmpeg_record(record),
        check_ffmpeg_record({**record, "frameworks": [{}]}),
    )


@counterexample
def ffmpeg_artifact_without_unverified(tmp: Path) -> tuple[Check, Check]:
    record = fake_ffmpeg(tmp).record
    without = {key: value for key, value in record.items() if key != "unverified"}
    return check_ffmpeg_record(record), check_ffmpeg_record(without)


@counterexample
def ffmpeg_linked_library_unrecorded(tmp: Path) -> tuple[Check, Check]:
    ffmpeg = fake_ffmpeg(tmp)
    bad = with_fields(
        ffmpeg,
        libraries=[name for name in ffmpeg.libraries if name != LINKED[0]],
        frameworks=[
            framework
            for framework in ffmpeg.record["frameworks"]
            if framework["name"] != LINKED[0]
        ],
    )
    return check_ffmpeg_binaries(ffmpeg), check_ffmpeg_binaries(bad)


@counterexample
def ffmpeg_slice_unrecorded(tmp: Path) -> tuple[Check, Check]:
    ffmpeg = fake_ffmpeg(tmp)
    first, *rest = ffmpeg.record["frameworks"]
    bad = with_fields(
        ffmpeg, frameworks=[{**first, "slices": first["slices"][:-1]}, *rest]
    )
    return check_ffmpeg_binaries(ffmpeg), check_ffmpeg_binaries(bad)


@counterexample
def ffmpeg_binary_recorded_at_another_path(tmp: Path) -> tuple[Check, Check]:
    ffmpeg = fake_ffmpeg(tmp)
    good = check_ffmpeg_binaries(ffmpeg)
    first, *rest = ffmpeg.record["frameworks"]
    entry, *others = first["slices"]
    linked = ffmpeg.root / entry["path"]
    copy = ffmpeg.root / "verified" / linked.name
    copy.parent.mkdir()
    shutil.copy(linked, copy)
    linked.write_bytes(b"another build")
    moved = {**entry, "path": str(copy.relative_to(ffmpeg.root))}
    bad = with_fields(
        ffmpeg, frameworks=[{**first, "slices": [moved, *others]}, *rest]
    )
    return good, check_ffmpeg_binaries(bad)


@counterexample
def ffmpeg_binary_of_an_unlisted_library(tmp: Path) -> tuple[Check, Check]:
    ffmpeg = fake_ffmpeg(tmp)
    frameworks = ffmpeg.record["frameworks"]
    unlisted = {**frameworks[0], "name": "libpostproc"}
    bad = with_fields(ffmpeg, frameworks=[*frameworks, unlisted])
    return check_ffmpeg_binaries(ffmpeg), check_ffmpeg_binaries(bad)
