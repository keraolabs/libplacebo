#!/usr/bin/env python3
"""Build the kerao libplacebo libraries for Apple platforms.

python3 kerao/build.py --output DIR --mode release|dev --ffmpeg-artifacts DIR
python3 kerao/build.py --self-test
"""

import sys

MIN_PYTHON = (3, 11)

if sys.version_info < MIN_PYTHON:
    sys.exit(
        f"build.py needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer, found {sys.version.split()[0]}"
    )

sys.dont_write_bytecode = True

import argparse
import dataclasses
import importlib
import shutil
import tempfile
import types
from pathlib import Path

import artifact
import checkout
import consumer
import deps
import exports
import ffmpeg
import framework
import glslang
import libplacebo
import privacy
import simulator
import xcframework
from common import (
    COUNTEREXAMPLES,
    FRAMEWORKS,
    HEADER_DIRS,
    IOS_SIMULATOR,
    KERAO,
    MACOS,
    ROOT,
    SLICES,
    BuildError,
    Check,
    Counterexample,
    Library,
    Slice,
    print_summary,
    scoped,
)
from deps import DEPS, Dep
from toolchain import Toolchain, read_toolchain

CHECK_PREFIX = "check_"
NOTICES_DIR = "notices"
ARTIFACT_JSON = "artifact.json"


@dataclasses.dataclass(frozen=True)
class SliceBuild:
    checks: list[Check]
    libraries: dict[str, Library]
    frameworks: Path


def build_slice(
    slice_: Slice,
    toolchain: Toolchain,
    bundle_version: str,
    checkouts: dict[Dep, Path],
    ffmpeg_input: ffmpeg.FFmpeg,
    records: privacy.Records,
    work: Path,
) -> SliceBuild:
    sdk_path = toolchain.sdks[slice_.identifier].path
    deps_prefix = work / "deps"
    checks = deps.build_deps(
        slice_, sdk_path, checkouts, work / "deps-build", deps_prefix
    )
    build_dir = libplacebo.build_libplacebo(
        slice_,
        sdk_path,
        deps_prefix,
        ffmpeg_input,
        work / "libplacebo",
        libplacebo.MESON_OPTIONS,
        [],
    )
    libraries = {
        "libplacebo": libplacebo.install_libplacebo(
            build_dir, work / "libplacebo" / "prefix"
        ),
        "libglslang": glslang.installed_library(deps_prefix),
    }
    frameworks = work / "frameworks"
    framework.package_slice(
        slice_, toolchain, bundle_version, libraries, records, frameworks
    )
    return SliceBuild(checks=checks, libraries=libraries, frameworks=frameworks)


def copy_notices(checkouts: dict[Dep, Path], notices: Path) -> None:
    (notices / "libplacebo").mkdir(parents=True)
    shutil.copy(ROOT / libplacebo.NOTICE, notices / "libplacebo" / libplacebo.NOTICE)
    deps.copy_notices(checkouts, notices)


def delivered_checks(
    output: Path,
    records: privacy.Records,
    bundle_version: str,
    ffmpeg_input: ffmpeg.FFmpeg,
) -> list[Check]:
    checks = []
    for slice_ in SLICES:
        for name in FRAMEWORKS:
            checks += framework.framework_checks(
                output, slice_, name, records, bundle_version, ffmpeg_input.libraries
            )
        checks.append(
            framework.linked_frameworks_check(
                output, slice_, "libplacebo", ("libglslang", *ffmpeg.LINKED)
            )
        )
        delivered = framework.delivered_framework(output, slice_, "libplacebo")
        checks += [
            scoped(f"{slice_.identifier} libplacebo", check)
            for check in exports.export_checks(
                framework.framework_binary(delivered, slice_.layout),
                framework.framework_headers(delivered, slice_.layout),
            )
        ]
    checks += [
        xcframework.check_library_identifiers(output / f"{name}.xcframework")
        for name in FRAMEWORKS
    ]
    return checks + xcframework.public_headers_checks(output)


def consumer_checks(
    output: Path,
    ffmpeg_input: ffmpeg.FFmpeg,
    toolchain: Toolchain,
    expected_version: str,
    launchers: dict[Slice, tuple[str, ...]],
) -> list[Check]:
    """launchers maps each slice the consumer runs on to its command prefix."""
    checks = []
    with tempfile.TemporaryDirectory(prefix="kerao-consumer-") as tmp:
        for slice_, launcher in launchers.items():
            run = consumer.delivered_consumer_run(
                output,
                ffmpeg_input,
                slice_,
                toolchain.sdks[slice_.identifier].path,
                Path(tmp) / slice_.identifier,
                launcher,
            )
            checks += [
                scoped(slice_.identifier, check)
                for check in (
                    consumer.check_consumer_version(run, expected_version),
                    consumer.check_consumer_libav(run),
                )
            ]
    return checks


def artifact_checks(
    output: Path,
    mode: str,
    source: checkout.Source,
    toolchain: Toolchain,
    ffmpeg_input: ffmpeg.FFmpeg,
) -> list[Check]:
    record = artifact.artifact_record(
        mode,
        source,
        toolchain,
        ffmpeg.inputs(ffmpeg_input),
        artifact.framework_records(output, SLICES),
        {
            slice_.identifier: {"args": list(libplacebo.MESON_OPTIONS)}
            for slice_ in SLICES
        },
        [],
    )
    path = output / ARTIFACT_JSON
    artifact.write_artifact(path, record)
    return [
        artifact.check_artifact_fields(path),
        artifact.check_artifact_slices(path),
        artifact.check_artifact_deps(path),
        artifact.check_artifact_inputs(path),
    ]


def summary_facts(
    mode: str,
    source: checkout.Source,
    toolchain: Toolchain,
    ffmpeg_input: ffmpeg.FFmpeg,
    output: Path,
) -> list[tuple[str, str]]:
    record = ffmpeg_input.record
    return [
        ("mode", mode),
        ("commit", source.commit),
        ("version", source.version),
        ("pl_version()", checkout.expected_pl_version(source, mode)),
        ("dirty", "yes" if source.dirty else "no"),
        ("ffmpeg", f"{record['tag']} {record['commit']} {record['mode']}"),
        ("ffmpeg artifacts", str(ffmpeg_input.root)),
        ("xcode", f"{toolchain.xcode_version} ({toolchain.xcode_build})"),
        *(
            (f"sdk {key}", f"{sdk.name} {sdk.version}")
            for key, sdk in toolchain.sdks.items()
        ),
        (
            "tools",
            ", ".join(f"{name} {version}" for name, version in toolchain.tools.items()),
        ),
        *((dep.name, f"{dep.tag} {dep.commit}") for dep in DEPS),
        ("output", str(output)),
    ]


def build(output: Path, mode: str, ffmpeg_root: Path) -> int:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise BuildError(f"--output {output} must be a new or empty directory")
    records = privacy.read_records()
    source = checkout.read_source(ROOT)
    ffmpeg_input = ffmpeg.read_ffmpeg(ffmpeg_root)
    toolchain = read_toolchain()
    simulators = simulator.read_booted_simulators()
    facts = summary_facts(mode, source, toolchain, ffmpeg_input, output)
    checks = [
        checkout.check_output_outside(source, output),
        *checkout.preconditions(source, mode),
        *ffmpeg.preconditions(ffmpeg_input, mode),
        simulator.check_booted_simulator(simulators),
    ]
    if not all(check.ok for check in checks):
        print_summary(facts, checks)
        print("not built: preconditions failed")
        return 1
    bundle_version = checkout.bundle_version(source, mode)
    with tempfile.TemporaryDirectory(prefix="kerao-libplacebo-") as tmp:
        work = Path(tmp)
        checkouts = {dep: deps.fetch(dep, work / "src" / dep.name) for dep in DEPS}
        checks += [deps.check_commit(dep, path) for dep, path in checkouts.items()]
        if not all(check.ok for check in checks):
            print_summary(facts, checks)
            print("not built: a dependency is not at its pinned commit")
            return 1
        builds = {
            slice_: build_slice(
                slice_,
                toolchain,
                bundle_version,
                checkouts,
                ffmpeg_input,
                records,
                work / slice_.identifier,
            )
            for slice_ in SLICES
        }
        for slice_build in builds.values():
            checks += slice_build.checks
        output.mkdir(parents=True, exist_ok=True)
        for name, library in builds[SLICES[0]].libraries.items():
            shutil.copytree(
                library.headers, output / xcframework.INCLUDE_DIR / HEADER_DIRS[name]
            )
        xcframework.make_xcframeworks(
            {slice_: slice_build.frameworks for slice_, slice_build in builds.items()},
            output,
        )
        copy_notices(checkouts, output / NOTICES_DIR)
    checks += delivered_checks(output, records, bundle_version, ffmpeg_input)
    checks += consumer_checks(
        output,
        ffmpeg_input,
        toolchain,
        checkout.expected_pl_version(source, mode),
        {MACOS: (), IOS_SIMULATOR: simulator.spawn_command(simulators[0])},
    )
    checks += artifact_checks(output, mode, source, toolchain, ffmpeg_input)
    print_summary(facts, checks)
    return 0 if all(check.ok for check in checks) else 1


def run_counterexample(case: Counterexample) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="kerao-self-test-") as tmp:
        try:
            good, bad = case(Path(tmp))
        except BuildError as error:
            return False, f"error: {error}"
    if not good.ok:
        return False, f"baseline fails {good.name}: {good.detail}"
    if bad.ok:
        return False, f"not caught by {bad.name}"
    return True, f"{bad.name}: {bad.detail}"


def load_modules() -> None:
    """Import every module of kerao/, so that the self-test sees all checks."""
    for path in sorted(KERAO.glob("*.py")):
        importlib.import_module(path.stem)


def defined_checks() -> list[types.FunctionType]:
    """Every function named check_* that a loaded module of kerao/ defines."""
    return [
        value
        for module in list(sys.modules.values())
        if getattr(module, "__file__", None)
        and Path(module.__file__).resolve().parent == KERAO
        for name, value in vars(module).items()
        if name.startswith(CHECK_PREFIX)
        and isinstance(value, types.FunctionType)
        and value.__module__ == module.__name__
    ]


def referenced_names(code: types.CodeType) -> frozenset[str]:
    nested = [const for const in code.co_consts if isinstance(const, types.CodeType)]
    return frozenset(code.co_names).union(*map(referenced_names, nested))


def uncovered_checks() -> tuple[int, list[str]]:
    """The number of check functions, and those no counterexample calls."""
    called = frozenset().union(
        *(referenced_names(case.__code__) for case in COUNTEREXAMPLES)
    )
    checks = defined_checks()
    return len(checks), sorted(
        f"{check.__module__}.{check.__name__}"
        for check in checks
        if check.__name__ not in called
    )


def self_test() -> int:
    load_modules()
    width = max(len(case.__name__) for case in COUNTEREXAMPLES)
    caught = 0
    for case in COUNTEREXAMPLES:
        ok, detail = run_counterexample(case)
        caught += ok
        print(f"{'pass' if ok else 'FAIL'}  {case.__name__:<{width}}  {detail}")
    print(f"\n{caught} of {len(COUNTEREXAMPLES)} counterexamples caught")
    total, uncovered = uncovered_checks()
    for name in uncovered:
        print(f"FAIL  {name}  no counterexample calls it")
    print(f"{total - len(uncovered)} of {total} checks have a counterexample")
    return 0 if caught == len(COUNTEREXAMPLES) and not uncovered else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the kerao libplacebo libraries for Apple platforms."
    )
    parser.add_argument(
        "--output", type=Path, help="new or empty directory for the build products"
    )
    parser.add_argument("--mode", choices=("release", "dev"))
    parser.add_argument(
        "--ffmpeg-artifacts",
        type=Path,
        metavar="DIR",
        help="output directory of the kerao FFmpeg build",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run every check against its counterexample",
    )
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.output is None or args.mode is None or args.ffmpeg_artifacts is None:
        parser.error(
            "--output, --mode and --ffmpeg-artifacts are required unless --self-test is given"
        )
    if not args.ffmpeg_artifacts.is_dir():
        parser.error(f"--ffmpeg-artifacts {args.ffmpeg_artifacts} is not a directory")
    try:
        return build(args.output.resolve(), args.mode, args.ffmpeg_artifacts.resolve())
    except (BuildError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
