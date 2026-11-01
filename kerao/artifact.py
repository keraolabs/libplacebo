import datetime
import json
from pathlib import Path

import checkout
import ffmpeg
from common import FRAMEWORKS, SLICES, Check, Slice, counterexample, listed
from deps import DEPS
from framework import delivered_framework, framework_binary
from toolchain import FAKE_TOOLCHAIN, TOOLS, Toolchain

REPOSITORY = "https://github.com/keraolabs/libplacebo"
DEP_FIELDS = ("name", "repository", "tag", "commit")
ARTIFACT_FIELDS = (
    "libraries",
    "repository",
    "tag",
    "commit",
    "mode",
    "dirty",
    "built_at",
    "xcode_build",
    "sdks",
    "tools",
    "deps",
    "inputs",
    "frameworks",
    "configure",
    "unverified",
)


def framework_records(output: Path, slices: tuple[Slice, ...]) -> list[dict]:
    records = []
    for name in FRAMEWORKS:
        binaries = [
            (
                slice_,
                framework_binary(
                    delivered_framework(output, slice_, name), slice_.layout
                ),
            )
            for slice_ in slices
        ]
        records.append(
            {
                "name": name,
                "slices": [
                    {
                        "identifier": slice_.identifier,
                        "path": str(binary.relative_to(output)),
                        "sha256": ffmpeg.sha256(binary),
                    }
                    for slice_, binary in binaries
                ],
            }
        )
    return records


def artifact_record(
    mode: str,
    source: checkout.Source,
    toolchain: Toolchain,
    inputs: list[dict],
    frameworks: list[dict],
    configure: dict[str, dict],
    unverified: list[str],
) -> dict:
    """configure maps each slice identifier to its meson arguments."""
    return {
        "libraries": list(FRAMEWORKS),
        "repository": REPOSITORY,
        "tag": checkout.artifact_tag(source, mode),
        "commit": source.commit,
        "mode": mode,
        "dirty": source.dirty,
        "built_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "xcode_build": toolchain.xcode_build,
        "sdks": {
            identifier: {"name": sdk.name, "version": sdk.version}
            for identifier, sdk in toolchain.sdks.items()
        },
        "tools": toolchain.tools,
        "deps": [{field: getattr(dep, field) for field in DEP_FIELDS} for dep in DEPS],
        "inputs": inputs,
        "frameworks": frameworks,
        "configure": configure,
        "unverified": unverified,
    }


def write_artifact(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, indent=2) + "\n")


def check_artifact_fields(path: Path) -> Check:
    record = json.loads(path.read_text())
    missing = [field for field in ARTIFACT_FIELDS if field not in record]
    return Check(
        "artifact.json fields",
        not missing,
        f"missing {', '.join(missing)}"
        if missing
        else f"{len(ARTIFACT_FIELDS)} fields",
    )


def check_artifact_slices(path: Path) -> Check:
    record = json.loads(path.read_text())
    expected = sorted(slice_.identifier for slice_ in SLICES)
    sdks = record.get("sdks", {})
    problems = [] if sorted(sdks) == expected else [f"sdks: {', '.join(sdks)}"]
    problems += [
        f"sdks {identifier} has no {key}"
        for identifier, sdk in sdks.items()
        for key in ("name", "version")
        if not sdk.get(key)
    ]
    frameworks = record.get("frameworks", [])
    names = [framework.get("name") for framework in frameworks]
    if names != list(FRAMEWORKS):
        problems.append(f"frameworks: {', '.join(map(str, names))}")
    for framework in frameworks:
        slices = framework.get("slices", [])
        found = sorted(str(entry.get("identifier")) for entry in slices)
        if found != expected:
            problems.append(f"{framework.get('name')} slices: {', '.join(found)}")
        problems += [
            f"{framework.get('name')} {entry.get('identifier')} has no {key}"
            for entry in slices
            for key in ("path", "sha256")
            if not entry.get(key)
        ]
    if sorted(record.get("configure", {})) != expected:
        problems.append(f"configure: {', '.join(record.get('configure', {}))}")
    return Check(
        "artifact.json slices",
        not problems,
        listed(problems)
        or f"{len(sdks)} sdks, {len(frameworks)} frameworks with {', '.join(expected)}",
    )


def check_artifact_deps(path: Path) -> Check:
    record = json.loads(path.read_text())
    entries = record.get("deps", [])
    names = [entry.get("name") for entry in entries]
    expected = [dep.name for dep in DEPS]
    problems = [] if names == expected else [f"deps: {', '.join(map(str, names))}"]
    problems += [
        f"deps {entry.get('name')} has no {field}"
        for entry in entries
        for field in DEP_FIELDS
        if not entry.get(field)
    ]
    tools = record.get("tools", {})
    problems += [f"tools has no {name}" for name in TOOLS if not tools.get(name)]
    return Check(
        "artifact.json deps and tools",
        not problems,
        listed(problems)
        or ", ".join(
            [
                *(f"{entry['name']} {entry['tag']}" for entry in entries),
                *(f"{name} {tools[name]}" for name in TOOLS),
            ]
        ),
    )


def check_artifact_inputs(path: Path) -> Check:
    """inputs must name the FFmpeg artifact by tag, commit, mode and binary
    hashes, and a release may only use release inputs."""
    record = json.loads(path.read_text())
    entries = record.get("inputs", [])
    problems = [] if len(entries) == 1 else [f"{len(entries)} inputs, expected 1"]
    for entry in entries:
        problems += [
            f"input has no {field}"
            for field in ffmpeg.INPUT_FIELDS
            if not entry.get(field)
        ]
        problems += [
            f"input {framework.get('name')} {binary.get('identifier')} has no sha256"
            for framework in entry.get("frameworks", [])
            for binary in framework.get("slices", [])
            if not binary.get("sha256")
        ]
        if record.get("mode") == "release" and entry.get("mode") != "release":
            problems.append(f"release uses a {entry.get('mode')} input")
    return Check(
        "artifact.json inputs",
        not problems,
        listed(problems)
        or ", ".join(f"FFmpeg {entry['tag']} ({entry['mode']})" for entry in entries),
    )


FAKE_SHA256 = "0" * 64


def fake_artifact_record(tmp: Path, ffmpeg_mode: str = "release") -> dict:
    source = checkout.read_source(checkout.fake_release_checkout(tmp))
    frameworks = [
        {
            "name": name,
            "slices": [
                {
                    "identifier": slice_.identifier,
                    "path": f"{name}.xcframework/{slice_.identifier}/{name}.framework/{name}",
                    "sha256": FAKE_SHA256,
                }
                for slice_ in SLICES
            ],
        }
        for name in FRAMEWORKS
    ]
    inputs = ffmpeg.inputs(ffmpeg.fake_ffmpeg(tmp, mode=ffmpeg_mode))
    configure = {slice_.identifier: {"args": ["-Ddemos=false"]} for slice_ in SLICES}
    return artifact_record(
        "release", source, FAKE_TOOLCHAIN, inputs, frameworks, configure, []
    )


def written(path: Path, record: dict) -> Path:
    write_artifact(path, record)
    return path


@counterexample
def artifact_without_required_field(tmp: Path) -> tuple[Check, Check]:
    record = fake_artifact_record(tmp)
    good = written(tmp / "good.json", record)
    bad = written(
        tmp / "bad.json", {key: value for key, value in record.items() if key != "tag"}
    )
    return check_artifact_fields(good), check_artifact_fields(bad)


@counterexample
def artifact_framework_without_a_slice(tmp: Path) -> tuple[Check, Check]:
    record = fake_artifact_record(tmp)
    good = written(tmp / "good.json", record)
    first, *rest = record["frameworks"]
    bad = written(
        tmp / "bad.json",
        {**record, "frameworks": [{**first, "slices": first["slices"][:-1]}, *rest]},
    )
    return check_artifact_slices(good), check_artifact_slices(bad)


@counterexample
def artifact_dep_without_commit(tmp: Path) -> tuple[Check, Check]:
    record = fake_artifact_record(tmp)
    good = written(tmp / "good.json", record)
    first, *rest = record["deps"]
    without_commit = {key: value for key, value in first.items() if key != "commit"}
    bad = written(tmp / "bad.json", {**record, "deps": [without_commit, *rest]})
    return check_artifact_deps(good), check_artifact_deps(bad)


@counterexample
def release_artifact_with_dev_input(tmp: Path) -> tuple[Check, Check]:
    good = written(tmp / "good.json", fake_artifact_record(tmp / "release"))
    bad = written(tmp / "bad.json", fake_artifact_record(tmp / "dev", "dev"))
    return check_artifact_inputs(good), check_artifact_inputs(bad)
