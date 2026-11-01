#!/usr/bin/env python3
"""Run libplacebo's tests, Metal included, on this Mac and the Metal test on an iPhone.

python3 kerao/test.py --ffmpeg-artifacts DIR --device UDID --codesign-identity SHA1
    --provisioning-profile PATH
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
import device
import ffmpeg
import glslang
import libplacebo
import suite
from common import IOS, MACOS, ROOT, BuildError, Check, capture, print_summary, scoped
from deps import DEPS
from framework import bundled
from macho import (
    check_architectures,
    check_build_version,
    check_dependencies,
    read_macho,
)
from toolchain import Toolchain, read_toolchain

TEST_SLICES = (MACOS, IOS)
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


def device_test_checks(
    build: libplacebo.TestBuild, ffmpeg_input: ffmpeg.FFmpeg
) -> list[Check]:
    """The Metal test program of the IOS build must run on iOS arm64 and load
    only system libraries, libglslang.dylib and FFmpeg frameworks."""
    program = build.build_dir / libplacebo.METAL_TEST_PROGRAM
    macho = read_macho(program)
    embedded = frozenset(
        {glslang.INSTALL_NAME, *bundled(IOS.layout, ffmpeg_input.libraries)}
    )
    checks = (
        check_build_version(macho.builds, IOS.platform, IOS.floor),
        check_architectures(macho.archs),
        check_dependencies(macho, embedded),
    )
    return [scoped(f"{IOS.identifier} {program.name}", check) for check in checks]


def run_on_iphone(
    build: libplacebo.TestBuild,
    ffmpeg_input: ffmpeg.FFmpeg,
    target: device.Target,
    work: Path,
) -> list[Check]:
    """Install the Metal test program of the IOS build as the runner app and
    judge its console output."""
    program = build.build_dir / libplacebo.METAL_TEST_PROGRAM
    libraries = [
        build.deps_prefix / "lib" / glslang.DYLIB,
        *device.linked_frameworks(program, ffmpeg_input),
    ]
    app = device.make_app(program, libraries, target, work / f"{device.APP_NAME}.app")
    device.install(target, app)
    run = suite.parse_metal_output(device.launch(target))
    return [scoped(IOS.identifier, check) for check in suite.metal_checks(run)]


def test(ffmpeg_root: Path, target: device.Target) -> int:
    source = checkout.read_source(ROOT)
    ffmpeg_input = ffmpeg.read_ffmpeg(ffmpeg_root)
    toolchain = read_toolchain()
    facts = summary_facts(source, toolchain, ffmpeg_input)
    checks: list[Check] = [
        checkout.check_submodules(source),
        *ffmpeg.preconditions(ffmpeg_input, TEST_MODE),
        *device.preconditions(target.udid),
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
        builds = {
            slice_: libplacebo.build_tests(
                slice_,
                toolchain.sdks[slice_.identifier].path,
                checkouts,
                ffmpeg_input,
                work / slice_.identifier,
                ffmpeg.rpath_args(ffmpeg_input, slice_) if slice_ == MACOS else [],
            )
            for slice_ in TEST_SLICES
        }
        for build in builds.values():
            checks += build.checks
        checks += device_test_checks(builds[IOS], ffmpeg_input)
        checks += [
            scoped(MACOS.identifier, check)
            for check in suite.suite_checks(
                suite.run_meson_tests(builds[MACOS].build_dir)
            )
        ]
        checks += run_on_iphone(builds[IOS], ffmpeg_input, target, work / "app")
    print_summary(facts, checks)
    return 0 if all(check.ok for check in checks) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run libplacebo's tests, Metal included, on this Mac and an iPhone."
    )
    parser.add_argument(
        "--ffmpeg-artifacts",
        type=Path,
        metavar="DIR",
        required=True,
        help="output directory of the kerao FFmpeg build",
    )
    parser.add_argument(
        "--device", metavar="UDID", required=True, help="the iPhone to run on"
    )
    parser.add_argument(
        "--codesign-identity",
        metavar="SHA1",
        required=True,
        help="Apple Development identity that signs the runner app",
    )
    parser.add_argument(
        "--provisioning-profile",
        type=Path,
        metavar="PATH",
        required=True,
        help="development profile of the identity's team that provisions the iPhone",
    )
    args = parser.parse_args()
    if not args.ffmpeg_artifacts.is_dir():
        parser.error(f"--ffmpeg-artifacts {args.ffmpeg_artifacts} is not a directory")
    try:
        target = device.read_target(
            args.device, args.codesign_identity, args.provisioning_profile
        )
        return test(args.ffmpeg_artifacts.resolve(), target)
    except (BuildError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
