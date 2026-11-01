import dataclasses
import re
from pathlib import Path

import deps
from common import (
    PKGCONFIG_DIR,
    ROOT,
    BuildError,
    Check,
    Library,
    Slice,
    build_env,
    stream,
)
from deps import Dep
from ffmpeg import FFmpeg, compile_args
from toolchain import meson_machine_file, meson_machine_option

MESON_OPTIONS = (
    "--auto-features=disabled",
    "-Dmetal=enabled",
    "-Dglslang=enabled",
    "-Ddovi=enabled",
    "-Dlibdovi=disabled",
    "-Ddemos=false",
)
TEST_OPTIONS = ("-Dtests=true",)
HEADERPAD = "-Wl,-headerpad_max_install_names"
MACHINE_FILE = "machine.ini"
DYLIB = re.compile(r"libplacebo\.[0-9]+\.dylib")
NOTICE = "LICENSE"
METAL_TEST_PROGRAM = Path("src") / "tests" / "test.metal.c"


@dataclasses.dataclass(frozen=True)
class TestBuild:
    """A build with TEST_OPTIONS; its programs load libglslang.dylib from
    deps_prefix/lib."""

    checks: list[Check]
    build_dir: Path
    deps_prefix: Path


def build_libplacebo(
    slice_: Slice,
    sdk_path: Path,
    deps_prefix: Path,
    ffmpeg: FFmpeg,
    work: Path,
    options: tuple[str, ...],
    link_args: list[str],
) -> Path:
    """Configure and compile this checkout for slice_ against the deps installed
    under deps_prefix; returns the meson build directory."""
    work.mkdir(parents=True, exist_ok=True)
    machine = work / MACHINE_FILE
    machine.write_text(
        meson_machine_file(
            slice_,
            sdk_path,
            deps_prefix / PKGCONFIG_DIR,
            compile_args(ffmpeg, slice_),
            [HEADERPAD, *link_args],
        )
    )
    build_dir = work / "build"
    what = f"libplacebo for {slice_.identifier}"
    stream(
        [
            *("meson", "setup", str(build_dir), str(ROOT)),
            *meson_machine_option(slice_, machine),
            f"--prefix={work / 'prefix'}",
            *options,
        ],
        f"meson setup of {what}",
        env=build_env(),
    )
    stream(
        ["meson", "compile", "-C", str(build_dir)],
        f"meson compile of {what}",
        env=build_env(),
    )
    return build_dir


def install_libplacebo(build_dir: Path, prefix: Path) -> Library:
    """Install the build into prefix, the --prefix it was configured with."""
    stream(
        ["meson", "install", "-C", str(build_dir), "--no-rebuild", "--quiet"],
        "meson install of libplacebo",
        env=build_env(),
    )
    lib = prefix / "lib"
    found = [
        path
        for path in lib.iterdir()
        if DYLIB.fullmatch(path.name) and not path.is_symlink()
    ]
    if len(found) != 1:
        raise BuildError(
            f"expected one libplacebo.<soversion>.dylib in {lib}, found {len(found)}"
        )
    return Library(dylib=found[0], headers=prefix / "include" / "libplacebo")


def build_tests(
    slice_: Slice,
    sdk_path: Path,
    checkouts: dict[Dep, Path],
    ffmpeg: FFmpeg,
    work: Path,
    link_args: list[str],
) -> TestBuild:
    """Build the deps and this checkout with TEST_OPTIONS for slice_ under work."""
    deps_prefix = work / "deps"
    checks = deps.build_deps(
        slice_, sdk_path, checkouts, work / "deps-build", deps_prefix
    )
    build_dir = build_libplacebo(
        slice_,
        sdk_path,
        deps_prefix,
        ffmpeg,
        work / "libplacebo",
        MESON_OPTIONS + TEST_OPTIONS,
        link_args,
    )
    return TestBuild(checks=checks, build_dir=build_dir, deps_prefix=deps_prefix)
