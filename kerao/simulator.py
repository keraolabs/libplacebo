import dataclasses
import json
from pathlib import Path

from common import IOS_SIMULATOR, BuildError, Check, capture, counterexample

SIMULATOR_PLATFORM = "iOS"
BOOTED = "Booted"


@dataclasses.dataclass(frozen=True)
class Simulator:
    udid: str
    name: str
    version: str


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def booted_simulators(listing: dict, floor: str) -> list[Simulator]:
    """The booted iOS Simulators at or above floor in `simctl list -j devices runtimes`."""
    try:
        runtimes = {
            runtime["identifier"]: runtime["version"]
            for runtime in listing["runtimes"]
            if runtime.get("platform") == SIMULATOR_PLATFORM
        }
        return [
            Simulator(device["udid"], device["name"], runtimes[runtime])
            for runtime, devices in listing["devices"].items()
            if runtime in runtimes
            and version_key(runtimes[runtime]) >= version_key(floor)
            for device in devices
            if device["state"] == BOOTED
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise BuildError(f"cannot read the simctl device list: {error!r}") from error


def read_booted_simulators() -> list[Simulator]:
    listing = capture(["xcrun", "simctl", "list", "-j", "devices", "runtimes"])
    try:
        parsed = json.loads(listing)
    except ValueError as error:
        raise BuildError(
            f"simctl printed a device list that is not JSON: {error}"
        ) from error
    return booted_simulators(parsed, IOS_SIMULATOR.floor)


def spawn_command(simulator: Simulator) -> tuple[str, ...]:
    """The command prefix that runs a program in simulator."""
    return ("xcrun", "simctl", "spawn", simulator.udid)


def check_booted_simulator(simulators: list[Simulator]) -> Check:
    """Passes when simulators is not empty, naming simulators[0]."""
    if not simulators:
        return Check(
            "booted simulator",
            False,
            f"no booted iOS {IOS_SIMULATOR.floor} or newer Simulator; "
            "boot one with xcrun simctl boot <udid>",
        )
    first = simulators[0]
    return Check(
        "booted simulator", True, f"{first.name}, iOS {first.version}, {first.udid}"
    )


FAKE_RUNTIME_PREFIX = "com.apple.CoreSimulator.SimRuntime."


def fake_simulator_listing(booted_versions: tuple[str, ...]) -> dict:
    """iOS 16.4 and 17.0 runtimes with one iPhone each; booted_versions are running."""
    versions = ("16.4", "17.0")
    return {
        "devices": {
            f"{FAKE_RUNTIME_PREFIX}iOS-{version.replace('.', '-')}": [
                {
                    "udid": f"00000000-0000-0000-0000-00000000{index:04d}",
                    "name": f"iPhone {index}",
                    "state": BOOTED if version in booted_versions else "Shutdown",
                }
            ]
            for index, version in enumerate(versions)
        },
        "runtimes": [
            {
                "identifier": f"{FAKE_RUNTIME_PREFIX}iOS-{version.replace('.', '-')}",
                "platform": SIMULATOR_PLATFORM,
                "version": version,
            }
            for version in versions
        ],
    }


@counterexample
def only_simulator_booted_is_below_floor(tmp: Path) -> tuple[Check, Check]:
    def check(booted_versions: tuple[str, ...]) -> Check:
        listing = fake_simulator_listing(booted_versions)
        return check_booted_simulator(booted_simulators(listing, IOS_SIMULATOR.floor))

    return check(("16.4", "17.0")), check(("16.4",))
