import dataclasses
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_LISTED_LINES = 5
PKGCONFIG_DIR = Path("lib") / "pkgconfig"

PLATFORM_MACOS = 1

ARCHS = ("arm64",)


@dataclasses.dataclass(frozen=True)
class Slice:
    identifier: str
    sdk: str
    target: str
    floor: str
    platform: int


MACOS = Slice(
    identifier="macos-arm64",
    sdk="macosx",
    target="arm64-apple-macos14.0",
    floor="14.0",
    platform=PLATFORM_MACOS,
)
SLICES = (MACOS,)


class BuildError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def scoped(scope: str, check: Check) -> Check:
    return dataclasses.replace(check, name=f"{scope} {check.name}")


def listed(items: list[str]) -> str:
    """At most MAX_LISTED_LINES of items, joined, with a count of the rest."""
    more = len(items) - MAX_LISTED_LINES
    return "; ".join(items[:MAX_LISTED_LINES]) + (f"; {more} more" if more > 0 else "")


def capture(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """command's stdout; raises BuildError when it fails."""
    try:
        result = subprocess.run(
            command, check=False, cwd=cwd, env=env, capture_output=True, text=True
        )
    except FileNotFoundError as error:
        raise BuildError(f"{command[0]} not found") from error
    if result.returncode != 0:
        raise BuildError(f"{' '.join(command)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def stream(
    command: list[str],
    what: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run command with its output on stderr; raise BuildError naming what if it fails."""
    try:
        result = subprocess.run(
            command, check=False, cwd=cwd, env=env, stdout=sys.stderr
        )
    except FileNotFoundError as error:
        raise BuildError(f"{command[0]} not found") from error
    if result.returncode != 0:
        raise BuildError(f"{what} failed")


def build_env() -> dict[str, str]:
    """The environment without any pkg-config setting or deployment target override."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PKG_CONFIG_") and not key.endswith("_DEPLOYMENT_TARGET")
    }


Counterexample = Callable[[Path], tuple[Check, Check]]
COUNTEREXAMPLES: list[Counterexample] = []


def counterexample(case: Counterexample) -> Counterexample:
    """Register a self-test case returning (good, bad): good passes, bad fails."""
    COUNTEREXAMPLES.append(case)
    return case
