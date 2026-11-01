#!/usr/bin/env python3
"""Run libplacebo's tests, Metal included, on this Mac.

python3 kerao/test.py --ffmpeg-artifacts DIR
"""

import sys

MIN_PYTHON = (3, 11)

if sys.version_info < MIN_PYTHON:
    sys.exit(
        f"test.py needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer, found {sys.version.split()[0]}"
    )

sys.dont_write_bytecode = True

import argparse
import tempfile
from pathlib import Path

import checkout
import deps
import ffmpeg
import libplacebo
import suite
from common import MACOS, ROOT, BuildError, Check, capture, print_summary, scoped
from deps import DEPS
from toolchain import Toolchain, read_toolchain

TEST_SLICES = (MACOS,)
TEST_MODE = "dev"


def machine_facts() -> list[tuple[str, str]]:
    def sysctl(name: str) -> str:
        return capture(["sysctl", "-n", name])

    def sw_vers(option: str) -> str:
        return capture(["sw_vers", option])

    return [
        ("machine", f"{sysctl('hw.model')}, {sysctl('machdep.cpu.brand_string')}"),
        ("macos", f"{sw_vers('-productVersion')} ({sw_vers('-buildVersion')})"),
    ]


def summary_facts(
    source: checkout.Source, toolchain: Toolchain, ffmpeg_input: ffmpeg.FFmpeg
) -> list[tuple[str, str]]:
    record = ffmpeg_input.record
    return [
        ("commit", source.commit),
        ("pl_version()", checkout.expected_pl_version(source, TEST_MODE)),
        ("dirty", "yes" if source.dirty else "no"),
        *machine_facts(),
        ("xcode", f"{toolchain.xcode_version} ({toolchain.xcode_build})"),
        *(
            (f"sdk {key}", f"{sdk.name} {sdk.version}")
            for key, sdk in toolchain.sdks.items()
            if key in {slice_.identifier for slice_ in TEST_SLICES}
        ),
        (
            "tools",
            ", ".join(f"{name} {version}" for name, version in toolchain.tools.items()),
        ),
        *((dep.name, f"{dep.tag} {dep.commit}") for dep in DEPS),
        ("ffmpeg", f"{record['tag']} {record['commit']} {record['mode']}"),
        ("meson options", " ".join(libplacebo.MESON_OPTIONS + libplacebo.TEST_OPTIONS)),
    ]


def test(ffmpeg_root: Path) -> int:
    source = checkout.read_source(ROOT)
    ffmpeg_input = ffmpeg.read_ffmpeg(ffmpeg_root)
    toolchain = read_toolchain()
    facts = summary_facts(source, toolchain, ffmpeg_input)
    checks: list[Check] = [
        checkout.check_submodules(source),
        *ffmpeg.preconditions(ffmpeg_input, TEST_MODE),
    ]
    if not all(check.ok for check in checks):
        print_summary(facts, checks)
        print("not tested: preconditions failed")
        return 1
    with tempfile.TemporaryDirectory(prefix="kerao-libplacebo-test-") as tmp:
        work = Path(tmp)
        checkouts = {dep: deps.fetch(dep, work / "src" / dep.name) for dep in DEPS}
        checks += [deps.check_commit(dep, path) for dep, path in checkouts.items()]
        if not all(check.ok for check in checks):
            print_summary(facts, checks)
            print("not tested: a dependency is not at its pinned commit")
            return 1
        for slice_ in TEST_SLICES:
            sdk_path = toolchain.sdks[slice_.identifier].path
            slice_work = work / slice_.identifier
            deps_prefix = slice_work / "deps"
            checks += deps.build_deps(
                slice_, sdk_path, checkouts, slice_work / "deps-build", deps_prefix
            )
            build_dir = libplacebo.build_libplacebo(
                slice_,
                sdk_path,
                deps_prefix,
                ffmpeg_input,
                slice_work / "libplacebo",
                libplacebo.MESON_OPTIONS + libplacebo.TEST_OPTIONS,
                ffmpeg.rpath_args(ffmpeg_input, slice_),
            )
            checks += [
                scoped(slice_.identifier, check)
                for check in suite.suite_checks(suite.run_meson_tests(build_dir))
            ]
    print_summary(facts, checks)
    return 0 if all(check.ok for check in checks) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run libplacebo's tests, Metal included, on this Mac."
    )
    parser.add_argument(
        "--ffmpeg-artifacts",
        type=Path,
        metavar="DIR",
        required=True,
        help="output directory of the kerao FFmpeg build",
    )
    args = parser.parse_args()
    if not args.ffmpeg_artifacts.is_dir():
        parser.error(f"--ffmpeg-artifacts {args.ffmpeg_artifacts} is not a directory")
    try:
        return test(args.ffmpeg_artifacts.resolve())
    except (BuildError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
