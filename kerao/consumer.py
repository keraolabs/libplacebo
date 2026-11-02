import dataclasses
import os
import shutil
from pathlib import Path

from common import (
    FRAMEWORKS,
    KERAO,
    MACOS,
    BuildError,
    Check,
    Slice,
    capture,
    counterexample,
)
from ffmpeg import FFmpeg, slice_dir
from framework import delivered_framework, fake_frameworks
from toolchain import read_sdk
from xcframework import INCLUDE_DIR

SOURCE = KERAO / "consumer.c"
PREFIX = "kerao-consumer"
NV12_PLANES = "2"
RUN_TIMEOUT_SECONDS = 60
DYLD_PREFIXES = ("DYLD_", "SIMCTL_CHILD_DYLD_")


@dataclasses.dataclass(frozen=True)
class Run:
    """lines maps each key the program printed after PREFIX to its value; error
    says why the program printed nothing."""

    lines: dict[str, str]
    error: str | None = None


def compile_consumer(
    slice_: Slice,
    sdk_path: Path,
    include_dirs: list[Path],
    framework_dirs: list[Path],
    source: Path,
    binary: Path,
) -> Path:
    """Link source against libplacebo.framework, loading every framework from
    framework_dirs through the program's own rpaths."""
    binary.parent.mkdir(parents=True, exist_ok=True)
    capture(
        [
            *("xcrun", "--sdk", slice_.sdk, "clang"),
            *("-target", slice_.target, "-isysroot", str(sdk_path)),
            *(f"-I{directory}" for directory in include_dirs),
            *(f"-F{directory}" for directory in framework_dirs),
            *("-framework", "libplacebo"),
            *(f"-Wl,-rpath,{directory}" for directory in framework_dirs),
            *("-o", str(binary), str(source)),
        ]
    )
    return binary


def parse(stdout: str) -> dict[str, str]:
    lines: dict[str, str] = {}
    for line in stdout.splitlines():
        prefix, _, rest = line.partition(" ")
        if prefix == PREFIX:
            key, _, value = rest.partition(" ")
            lines[key] = value
    return lines


def run_consumer(binary: Path, launcher: tuple[str, ...] = ()) -> Run:
    """Run binary through the launcher command prefix, with no DYLD_* variable in
    its environment and none that simctl would pass on as one."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(DYLD_PREFIXES)
    }
    try:
        stdout = capture(
            [*launcher, str(binary)], env=env, timeout_seconds=RUN_TIMEOUT_SECONDS
        )
    except BuildError as error:
        return Run({}, str(error))
    return Run(parse(stdout))


def delivered_consumer_run(
    output: Path,
    ffmpeg: FFmpeg,
    slice_: Slice,
    sdk_path: Path,
    work: Path,
    launcher: tuple[str, ...],
) -> Run:
    """Build SOURCE against the delivered slice_ and the FFmpeg frameworks, and run
    it through the launcher command prefix."""
    framework_dirs = [
        *(delivered_framework(output, slice_, name).parent for name in FRAMEWORKS),
        *(slice_dir(ffmpeg, slice_, name) for name in ffmpeg.libraries),
    ]
    try:
        binary = compile_consumer(
            slice_,
            sdk_path,
            [output / INCLUDE_DIR, ffmpeg.root / INCLUDE_DIR],
            framework_dirs,
            SOURCE,
            work / "consumer",
        )
    except BuildError as error:
        return Run({}, str(error))
    return run_consumer(binary, launcher)


def failure(run: Run, key: str) -> str | None:
    if run.error is not None:
        return run.error.splitlines()[0]
    return None if key in run.lines else f"printed no {key} line"


def check_consumer_version(run: Run, expected: str) -> Check:
    """expected is the pl_version() the build must report."""
    problem = failure(run, "version")
    if problem is None and run.lines["version"] != expected:
        problem = f"{run.lines['version']}, expected {expected}"
    return Check("consumer pl_version()", problem is None, problem or expected)


def check_consumer_libav(run: Run) -> Check:
    problem = failure(run, "planes")
    if problem is None and run.lines["planes"] != NV12_PLANES:
        problem = f"{run.lines['planes']} NV12 planes, expected {NV12_PLANES}"
    return Check(
        "consumer pl_plane_data_from_pixfmt()",
        problem is None,
        problem or f"{NV12_PLANES} NV12 planes",
    )


FAKE_VERSION = "v7.387.0 (v7.387.0-kerao.1)"
FAKE_SOURCE = f"""\
#include <stdio.h>
int libplacebo_version(void);
int main(void)
{{
    printf("{PREFIX} version {FAKE_VERSION}\\n");
    printf("{PREFIX} planes %d\\n", libplacebo_version() + 1);
    return 0;
}}
"""


@counterexample
def consumer_reports_another_version(tmp: Path) -> tuple[Check, Check]:
    run = Run(parse(f"{PREFIX} version {FAKE_VERSION}\n{PREFIX} planes 2\n"))
    return (
        check_consumer_version(run, FAKE_VERSION),
        check_consumer_version(run, FAKE_VERSION.replace(")", "-dirty)")),
    )


@counterexample
def consumer_misses_a_framework(tmp: Path) -> tuple[Check, Check]:
    frameworks = fake_frameworks(tmp)
    source = tmp / "consumer.c"
    source.write_text(FAKE_SOURCE)
    framework_dirs = sorted({framework.parent for framework in frameworks.values()})
    binary = compile_consumer(
        MACOS, read_sdk(MACOS.sdk).path, [], framework_dirs, source, tmp / "consumer"
    )
    good = check_consumer_libav(run_consumer(binary))
    shutil.rmtree(frameworks["libglslang"])
    return good, check_consumer_libav(run_consumer(binary))
