import dataclasses
import datetime
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import suite
from checkout import UPSTREAM_VERSION
from common import IOS, BuildError, Check, capture, counterexample
from ffmpeg import FFmpeg, slice_dir
from framework import install_id, platform_keys
from macho import read_macho

DEVICE_CHECK = "device"
UNLOCKED_CHECK = "device unlocked"
PHYSICAL = "physical"
PAIRED = "paired"
IOS_PLATFORM = "iOS"
SHA1 = re.compile(r"[0-9A-F]{40}")
APP_NAME = "KeraoMetalTest"
APP_VERSION = "1"
APP_RPATH = "@executable_path/Frameworks"
WILDCARD_APP_NAME = "kerao-metal-test"
CHILD_ENVIRONMENT = {"DEVICECTL_CHILD_MTL_DEBUG_LAYER": "1"}
RUN_TIMEOUT_SECONDS = 120


@dataclasses.dataclass(frozen=True)
class Iphone:
    udid: str
    name: str
    model: str
    version: str
    paired: bool


def find_iphone(listing: dict, udid: str) -> Iphone | None:
    """The physical iOS device udid in `devicectl list devices` JSON, if it is listed."""
    try:
        for device in listing["result"]["devices"]:
            properties = device["properties"]
            hardware = properties["hardware"]
            if (
                hardware.get("udid") != udid
                or hardware["reality"] != PHYSICAL
                or hardware["platform"] != IOS_PLATFORM
            ):
                continue
            return Iphone(
                udid=udid,
                name=properties["state"]["name"],
                model=hardware["marketingName"],
                version=properties["software"]["osVersionNumber"]["stringValue"],
                paired=properties["connection"]["pairingState"] == PAIRED,
            )
    except (KeyError, TypeError) as error:
        raise BuildError(f"cannot read the devicectl device list: {error!r}") from error
    return None


def read_devicectl_json(*args: str) -> dict:
    output = capture(["xcrun", "devicectl", *args, "--json-output", "-"])
    try:
        return json.loads(output)
    except ValueError as error:
        raise BuildError(
            f"devicectl {' '.join(args)} printed something that is not JSON: {error}"
        ) from error


def check_device(listing: dict, udid: str) -> Check:
    """listing is `devicectl list devices` JSON."""
    iphone = find_iphone(listing, udid)
    if iphone is None:
        return Check(DEVICE_CHECK, False, f"devicectl lists no iPhone {udid}")
    if not iphone.paired:
        return Check(
            DEVICE_CHECK,
            False,
            f"{iphone.name} ({udid}) is not paired: connect it and trust this Mac",
        )
    return Check(
        DEVICE_CHECK, True, f"{iphone.name}, {iphone.model}, iOS {iphone.version}, {udid}"
    )


def check_unlocked(read_lock_state: Callable[[], dict]) -> Check:
    """read_lock_state returns `devicectl device info lockState` JSON and raises
    BuildError when devicectl cannot reach the device."""
    try:
        lock_state = read_lock_state()
    except BuildError as error:
        return Check(
            UNLOCKED_CHECK,
            False,
            f"cannot reach the iPhone: {str(error).splitlines()[0]}",
        )
    try:
        locked = lock_state["result"]["passcodeRequired"]
    except (KeyError, TypeError) as error:
        raise BuildError(f"cannot read the devicectl lock state: {error!r}") from error
    if locked:
        return Check(
            UNLOCKED_CHECK,
            False,
            "the iPhone is locked: unlock it and keep it unlocked until the run ends",
        )
    return Check(UNLOCKED_CHECK, True)


def preconditions(udid: str) -> list[Check]:
    device = check_device(read_devicectl_json("list", "devices"), udid)
    if not device.ok:
        return [device]
    return [
        device,
        check_unlocked(
            lambda: read_devicectl_json("device", "info", "lockState", "--device", udid)
        ),
    ]


@dataclasses.dataclass(frozen=True)
class Target:
    """An iPhone and what signs the runner app installed on it."""

    udid: str
    identity: str
    profile: Path
    team: str
    bundle_id: str
    get_task_allow: bool


def read_profile(path: Path) -> dict:
    try:
        return plistlib.loads(
            capture(["security", "cms", "-D", "-i", str(path)]).encode()
        )
    except plistlib.InvalidFileException as error:
        raise BuildError(f"{path} is not a provisioning profile: {error}") from error


def read_target(udid: str, identity: str, profile_path: Path) -> Target:
    """identity is the SHA-1 of a codesigning identity in the keychain.

    Raises BuildError unless the profile provisions udid, holds the identity's
    certificate and has not expired.
    """
    if not SHA1.fullmatch(identity.upper()):
        raise BuildError(
            f"--codesign-identity {identity} is not the SHA-1 that "
            "security find-identity -v -p codesigning prints"
        )
    profile = read_profile(profile_path)
    try:
        team = profile["TeamIdentifier"][0]
        entitlements = profile["Entitlements"]
        app_id = entitlements["application-identifier"]
        expires = profile["ExpirationDate"]
        certificates = {
            hashlib.sha1(certificate).hexdigest().upper()
            for certificate in profile["DeveloperCertificates"]
        }
        provisioned = profile.get("ProvisionsAllDevices", False) or udid in profile.get(
            "ProvisionedDevices", []
        )
    except (KeyError, IndexError, TypeError) as error:
        raise BuildError(f"cannot read {profile_path}: {error!r}") from error
    if not app_id.startswith(f"{team}."):
        raise BuildError(f"{profile_path}: app id {app_id} is not in team {team}")
    if expires <= datetime.datetime.now(datetime.UTC).replace(tzinfo=None):
        raise BuildError(f"{profile_path} expired on {expires:%Y-%m-%d}")
    if not provisioned:
        raise BuildError(f"{profile_path} does not provision {udid}")
    if identity.upper() not in certificates:
        raise BuildError(f"{profile_path} does not hold the certificate of {identity}")
    bundle_id = app_id.removeprefix(f"{team}.")
    if bundle_id.endswith("*"):
        bundle_id = bundle_id.removesuffix("*") + WILDCARD_APP_NAME
    return Target(
        udid=udid,
        identity=identity,
        profile=profile_path,
        team=team,
        bundle_id=bundle_id,
        get_task_allow=bool(entitlements.get("get-task-allow", False)),
    )


def linked_frameworks(binary: Path, ffmpeg: FFmpeg) -> list[Path]:
    """The ios-arm64 FFmpeg frameworks binary loads, directly or through another."""
    names = {install_id(name, IOS.layout): name for name in ffmpeg.libraries}
    found: dict[str, Path] = {}
    pending = [binary]
    while pending:
        for dependency in read_macho(pending.pop()).dylibs:
            name = names.get(dependency)
            if name is None or name in found:
                continue
            found[name] = slice_dir(ffmpeg, IOS, name) / f"{name}.framework"
            pending.append(found[name] / name)
    return list(found.values())


def codesign(identity: str, bundle: Path, entitlements: Path | None = None) -> None:
    capture(
        [
            *("codesign", "--force", "--sign", identity, "--timestamp=none"),
            *(("--entitlements", str(entitlements)) if entitlements else ()),
            str(bundle),
        ]
    )


def app_plist(bundle_id: str) -> dict[str, object]:
    return {
        "CFBundleExecutable": APP_NAME,
        "CFBundleIdentifier": bundle_id,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": UPSTREAM_VERSION,
        "CFBundleSupportedPlatforms": [IOS.bundle_platform],
        "CFBundleVersion": APP_VERSION,
        "UIApplicationSceneManifest": {"UIApplicationSupportsMultipleScenes": False},
        **platform_keys(IOS),
    }


def make_app(program: Path, libraries: list[Path], target: Target, app: Path) -> Path:
    """Wrap program, an iOS app executable, in an app signed for target with each
    of libraries, a dylib or framework, copied into Frameworks/ and signed."""
    executable = app / APP_NAME
    embedded = app / "Frameworks"
    embedded.mkdir(parents=True)
    shutil.copy(program, executable)
    capture(
        [
            *("xcrun", "install_name_tool"),
            *(
                arg
                for rpath in read_macho(executable).rpaths
                for arg in ("-delete_rpath", rpath)
            ),
            *("-add_rpath", APP_RPATH, str(executable)),
        ]
    )
    for library in libraries:
        copy = embedded / library.name
        if library.is_dir():
            shutil.copytree(library, copy)
        else:
            shutil.copy(library, copy)
        codesign(target.identity, copy)
    with (app / "Info.plist").open("wb") as file:
        plistlib.dump(app_plist(target.bundle_id), file)
    shutil.copy(target.profile, app / "embedded.mobileprovision")
    entitlements = app.parent / "entitlements.plist"
    with entitlements.open("wb") as file:
        plistlib.dump(
            {
                "application-identifier": f"{target.team}.{target.bundle_id}",
                "com.apple.developer.team-identifier": target.team,
                "get-task-allow": target.get_task_allow,
            },
            file,
        )
    codesign(target.identity, app, entitlements)
    return app


def install(target: Target, app: Path) -> None:
    capture(
        [
            *("xcrun", "devicectl", "device", "install", "app"),
            *("--device", target.udid, str(app)),
        ]
    )


def launch(target: Target) -> str:
    """The console output of one run of the installed app, also copied to stderr,
    whatever devicectl's exit status. A run past RUN_TIMEOUT_SECONDS is cut off."""
    command = [
        *("xcrun", "devicectl", "device", "process", "launch"),
        *("--device", target.udid, "--console", "--terminate-existing"),
        target.bundle_id,
    ]
    lines: list[str] = []
    timed_out = threading.Event()
    try:
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            text=True,
            errors="replace",
            env={**os.environ, **CHILD_ENVIRONMENT},
        ) as process:

            def stop() -> None:
                timed_out.set()
                process.kill()

            timer = threading.Timer(RUN_TIMEOUT_SECONDS, stop)
            timer.start()
            for line in process.stdout:
                sys.stderr.write(line)
                lines.append(line)
            timer.cancel()
    except FileNotFoundError as error:
        raise BuildError("xcrun not found") from error
    if timed_out.is_set():
        print(f"the app on {target.udid} ran past {RUN_TIMEOUT_SECONDS} s", file=sys.stderr)
    return "".join(lines)


FAKE_UDID = "00008150-0000000000000001"


def fake_device_listing(pairing_state: str) -> dict:
    """`devicectl list devices` JSON with one iPhone in pairing_state and one Simulator."""
    return {
        "result": {
            "devices": [
                {
                    "properties": {
                        "connection": {"pairingState": pairing_state},
                        "hardware": {
                            "marketingName": "iPhone 17",
                            "platform": "iOS",
                            "reality": "physical",
                            "udid": FAKE_UDID,
                        },
                        "software": {"osVersionNumber": {"stringValue": "27.2"}},
                        "state": {"name": "iPhone"},
                    }
                },
                {
                    "properties": {
                        "connection": {"pairingState": "paired"},
                        "hardware": {
                            "marketingName": "iPhone 15",
                            "platform": "iOS",
                            "reality": "simulated",
                            "udid": "00000000-0000-0000-0000-000000000000",
                        },
                        "software": {"osVersionNumber": {"stringValue": "17.0"}},
                        "state": {"name": "iPhone 15"},
                    }
                },
            ]
        }
    }


@counterexample
def device_not_paired(tmp: Path) -> tuple[Check, Check]:
    def check(pairing_state: str) -> Check:
        return check_device(fake_device_listing(pairing_state), FAKE_UDID)

    return check("paired"), check("unpaired")


@counterexample
def device_not_listed(tmp: Path) -> tuple[Check, Check]:
    listing = fake_device_listing("paired")
    return (
        check_device(listing, FAKE_UDID),
        check_device(listing, "00000000-0000-0000-0000-000000000000"),
    )


def fake_lock_state(passcode_required: bool) -> Callable[[], dict]:
    return lambda: {"result": {"passcodeRequired": passcode_required}}


def unreachable_lock_state() -> dict:
    raise BuildError(
        "xcrun devicectl device info lockState failed: ERROR: The device is not "
        "able to fulfill the requested usage assertion requirements. "
        "(com.apple.dt.CoreDeviceError error 4016 (0xFB0))\n"
        "       CurrentlyAssertableStates = (\n       )"
    )


@counterexample
def device_locked(tmp: Path) -> tuple[Check, Check]:
    return check_unlocked(fake_lock_state(False)), check_unlocked(fake_lock_state(True))


@counterexample
def device_unreachable(tmp: Path) -> tuple[Check, Check]:
    return check_unlocked(fake_lock_state(False)), check_unlocked(unreachable_lock_state)


FAKE_CONSOLE_START = f"""\
Launched application with com.keraolabs.kerao.probes7 bundle identifier.
Waiting for the application to terminate\u2026
Initialized libplacebo v7.387.0 (v7.387.0-15-gc442c617) (API v387)
{suite.RESULT_PREFIX} validation {suite.VALIDATION}
[0.001][i] Metal device: Apple A19 GPU, MTLGPUFamilyApple10
{suite.RESULT_PREFIX} config native Apple10
[0.000][i] Metal device: Apple A19 GPU, MTLGPUFamilyApple5 (capped)
"""
FAKE_CONSOLE_PASSED = f"""{FAKE_CONSOLE_START}\
{suite.RESULT_PREFIX} config Apple5 Apple5
{suite.RESULT_PREFIX} end 2
The app terminated with the exit code 0.
"""


def console_check(console: str, name: str) -> Check:
    run = suite.parse_metal_output(console)
    return next(c for c in suite.metal_checks(run) if c.name == f"Metal {name}")


@counterexample
def iphone_run_with_a_failing_test(tmp: Path) -> tuple[Check, Check]:
    failed = f"{FAKE_CONSOLE_START}The app terminated with the exit code 1.\n"
    return (
        console_check(FAKE_CONSOLE_PASSED, "Apple5"),
        console_check(failed, "Apple5"),
    )


@counterexample
def iphone_app_killed_mid_run(tmp: Path) -> tuple[Check, Check]:
    killed = f"{FAKE_CONSOLE_START}App terminated due to signal 9.\n"
    return (
        console_check(FAKE_CONSOLE_PASSED, "native"),
        console_check(killed, "native"),
    )
