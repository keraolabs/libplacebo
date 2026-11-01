import dataclasses
import json
import subprocess
import sys
from pathlib import Path

from common import BuildError, Check, build_env, counterexample

TESTLOG = Path("meson-logs") / "testlog.json"
PASSED = "OK"
PROJECT_PREFIX = "libplacebo:"
METAL_TEST = "metal.c"
RESULT_PREFIX = "kerao-metal-test"
CONFIGURATIONS = ("native", "Apple5")


@dataclasses.dataclass(frozen=True)
class TestResult:
    name: str
    result: str
    duration: float
    stdout: str


@dataclasses.dataclass(frozen=True)
class MetalRun:
    """configurations maps each configuration the Metal test reported as passed
    to its Apple family; complete says its end line came last with their count."""

    configurations: dict[str, str]
    complete: bool


def read_testlog(path: Path) -> list[TestResult]:
    try:
        return [
            TestResult(
                name=entry["name"].removeprefix(PROJECT_PREFIX),
                result=entry["result"],
                duration=float(entry["duration"]),
                stdout=entry.get("stdout") or "",
            )
            for entry in map(json.loads, path.read_text().splitlines())
        ]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BuildError(f"cannot read meson's test log {path}: {error!r}") from error


def run_meson_tests(build_dir: Path) -> list[TestResult]:
    """Run every test of build_dir and return meson's result for each."""
    command = ["meson", "test", "-C", str(build_dir), "--no-rebuild"]
    try:
        status = subprocess.run(
            command, check=False, stdout=sys.stderr, env=build_env()
        ).returncode
    except FileNotFoundError as error:
        raise BuildError("meson not found") from error
    results = read_testlog(build_dir / TESTLOG)
    if not results:
        raise BuildError(f"{' '.join(command)} ran no tests")
    if status != 0 and all(result.result == PASSED for result in results):
        raise BuildError(f"{' '.join(command)} exited {status} with every test passed")
    return results


def parse_metal_output(stdout: str) -> MetalRun:
    configurations: dict[str, str] = {}
    end: str | None = None
    for line in stdout.splitlines():
        prefix, _, rest = line.partition(" ")
        if prefix != RESULT_PREFIX:
            continue
        words = rest.split()
        if len(words) == 3 and words[0] == "config":
            configurations[words[1]] = words[2]
            end = None
        elif len(words) == 2 and words[0] == "end":
            end = words[1]
    return MetalRun(configurations, end == str(len(configurations)))


def check_test(result: TestResult) -> Check:
    return Check(
        f"test {result.name}",
        result.result == PASSED,
        f"{result.result} in {result.duration:.1f} s",
    )


def check_configuration(run: MetalRun | None, name: str) -> Check:
    """run is the Metal test's output, None when meson did not run it."""
    label = f"Metal {name}"
    if run is None:
        return Check(label, False, f"{METAL_TEST} did not run")
    if not run.complete:
        return Check(label, False, "no complete result")
    if name not in run.configurations:
        return Check(label, False, "no result line")
    return Check(label, True, f"{run.configurations[name]}, MTL_DEBUG_LAYER=1")


def suite_checks(results: list[TestResult]) -> list[Check]:
    metal = next(
        (parse_metal_output(r.stdout) for r in results if r.name == METAL_TEST), None
    )
    return [
        *map(check_test, results),
        *(check_configuration(metal, name) for name in CONFIGURATIONS),
    ]


FAKE_METAL_OUTPUT = f"""\
[  0.001] debug: Metal: compiling vertex shader GLSL with glslang
{RESULT_PREFIX} config native Apple9
{RESULT_PREFIX} config Apple5 Apple5
{RESULT_PREFIX} end 2
"""


@counterexample
def meson_test_failed(tmp: Path) -> tuple[Check, Check]:
    return (
        check_test(TestResult("dither.c", PASSED, 0.3, "")),
        check_test(TestResult("dither.c", "FAIL", 0.3, "")),
    )


def fake_suite(tmp: Path, metal_stdout: str, name: str) -> Check:
    """The check of configuration name, from a meson test log whose Metal test
    printed metal_stdout."""
    testlog = tmp / f"{len(list(tmp.iterdir()))}.json"
    entries = [
        {"name": f"{PROJECT_PREFIX}dither.c", "result": PASSED, "duration": 0.3},
        {
            "name": f"{PROJECT_PREFIX}{METAL_TEST}",
            "result": PASSED,
            "duration": 7.0,
            "stdout": metal_stdout,
        },
    ]
    testlog.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    checks = suite_checks(read_testlog(testlog))
    return next(check for check in checks if check.name == f"Metal {name}")


@counterexample
def metal_test_without_end_line(tmp: Path) -> tuple[Check, Check]:
    truncated = FAKE_METAL_OUTPUT.rsplit(RESULT_PREFIX, 1)[0]
    return (
        check_configuration(parse_metal_output(FAKE_METAL_OUTPUT), "native"),
        fake_suite(tmp, truncated, "native"),
    )


@counterexample
def metal_configuration_without_result_line(tmp: Path) -> tuple[Check, Check]:
    without_apple5 = "\n".join(
        line.replace("end 2", "end 1")
        for line in FAKE_METAL_OUTPUT.splitlines()
        if "config Apple5" not in line
    )
    return (
        fake_suite(tmp, FAKE_METAL_OUTPUT, "Apple5"),
        fake_suite(tmp, without_apple5, "Apple5"),
    )
