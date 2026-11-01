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
import tempfile
import types
from pathlib import Path

import checkout
import deps
import glslang
import macho
import spirv_cross
from common import COUNTEREXAMPLES, ROOT, SLICES, BuildError, Check, Counterexample
from deps import DEPS
from toolchain import Toolchain, read_toolchain

KERAO = ROOT / "kerao"
CHECK_PREFIX = "check_"
DEPS_DIR = "deps"


def print_summary(
    mode: str,
    source: checkout.Source,
    toolchain: Toolchain,
    ffmpeg_artifacts: Path,
    output: Path,
    checks: list[Check],
) -> None:
    facts = [
        ("mode", mode),
        ("commit", source.commit),
        ("version", source.version),
        ("dirty", "yes" if source.dirty else "no"),
        ("ffmpeg artifacts", str(ffmpeg_artifacts)),
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
    width = max(len(label) for label, _ in facts)
    print("\n".join(f"{label:<{width}}  {value}" for label, value in facts))
    print()
    width = max(len(check.name) for check in checks)
    for check in checks:
        print(
            f"{'pass' if check.ok else 'FAIL'}  {check.name:<{width}}  {check.detail}".rstrip()
        )
    failed = sum(not check.ok for check in checks)
    print(f"\n{len(checks) - failed} of {len(checks)} checks passed")


def build(output: Path, mode: str, ffmpeg_artifacts: Path) -> int:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise BuildError(f"--output {output} must be a new or empty directory")
    source = checkout.read_source(ROOT)
    toolchain = read_toolchain()
    with tempfile.TemporaryDirectory(prefix="kerao-libplacebo-") as tmp:
        work = Path(tmp)
        checkouts = {dep: deps.fetch(dep, work / "src" / dep.name) for dep in DEPS}
        checks = [deps.check_commit(dep, path) for dep, path in checkouts.items()]
        if not all(check.ok for check in checks):
            print_summary(mode, source, toolchain, ffmpeg_artifacts, output, checks)
            print("not built: a dependency is not at its pinned commit")
            return 1
        for slice_ in SLICES:
            checks += deps.build_deps(
                slice_,
                toolchain.sdks[slice_.identifier].path,
                checkouts,
                work / slice_.identifier,
                output / DEPS_DIR / slice_.identifier,
            )
    print_summary(mode, source, toolchain, ffmpeg_artifacts, output, checks)
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
