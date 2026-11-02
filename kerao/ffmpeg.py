import dataclasses
import hashlib
import json
from pathlib import Path

from common import SLICES, BuildError, Check, Slice, counterexample, listed

FFMPEG_TAG = "n9.0.2-kerao.1"
FFMPEG_COMMIT = "ae49c6ac4a2409553ca0f9a00f9e252cbce632a0"
LINKED = ("libavutil", "libavcodec")
ARTIFACT_JSON = "artifact.json"
RECORD_SHAPE = {
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


def check_ffmpeg_binaries(ffmpeg: FFmpeg) -> Check:
    """Every binary FFmpeg's artifact.json lists must have its recorded SHA-256."""
    entries = [
        entry
        for framework in ffmpeg.record["frameworks"]
        for entry in framework["slices"]
    ]
    problems = []
    for entry in entries:
        binary = ffmpeg.root / entry["path"]
        if not binary.is_file():
            problems.append(f"no {entry['path']}")
        elif sha256(binary) != entry["sha256"]:
            problems.append(f"{entry['path']} differs from its sha256")
    return Check(
        "FFmpeg binaries",
        bool(entries) and not problems,
        listed(problems) or f"{len(entries)} match their sha256",
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
            path = f"{name}.xcframework/{slice_.identifier}/{name}.framework/{name}"
            (root / path).parent.mkdir(parents=True)
            (root / path).write_bytes(f"{name} {slice_.identifier}".encode())
            slices.append(
                {
                    "identifier": slice_.identifier,
                    "path": path,
                    "sha256": sha256(root / path),
                }
            )
        frameworks.append({"name": name, "slices": slices})
    record = {
        "libraries": [framework["name"] for framework in frameworks],
        "repository": "https://github.com/keraolabs/FFmpeg",
        "tag": FFMPEG_TAG,
        "commit": FFMPEG_COMMIT,
        "mode": "release",
        "frameworks": frameworks,
        **fields,
    }
    (root / ARTIFACT_JSON).write_text(json.dumps(record))
    return read_ffmpeg(root)


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
