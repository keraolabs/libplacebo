import dataclasses
import re
from pathlib import Path

from common import (
    ARCHS,
    IOS,
    IOS_SIMULATOR,
    MACOS,
    Check,
    Slice,
    capture,
    counterexample,
    listed,
)
from toolchain import tool

DYLIB_COMMANDS = frozenset(
    {
        "LC_LOAD_DYLIB",
        "LC_LOAD_WEAK_DYLIB",
        "LC_REEXPORT_DYLIB",
        "LC_LOAD_UPWARD_DYLIB",
        "LC_LAZY_LOAD_DYLIB",
    }
)
SYSTEM_PREFIXES = ("/usr/lib/", "/System/Library/")
OFFSET_SUFFIX = re.compile(r" \(offset \d+\)$")
ARCHIVE_MEMBER = re.compile(r"\S.*\((?P<member>[^()]+)\):")

BuildVersions = tuple[tuple[int, str], ...]
TARGETED = ("-target", MACOS.target)
FAKE_FUNCTION = "int fake() { return 0; }\n"


@dataclasses.dataclass(frozen=True)
class MachO:
    path: Path
    archs: tuple[str, ...]
    install_ids: tuple[str, ...]
    rpaths: tuple[str, ...]
    dylibs: tuple[str, ...]
    builds: BuildVersions


def load_commands(otool_output: str) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for line in otool_output.splitlines():
        key, _, value = line.strip().partition(" ")
        value = OFFSET_SUFFIX.sub("", value.strip())
        if key == "cmd":
            commands.append({"cmd": value})
        elif commands:
            commands[-1].setdefault(key, value)
    return commands


def build_versions(commands: list[dict[str, str]]) -> BuildVersions:
    return tuple(
        (int(command["platform"]), command["minos"])
        for command in commands
        if command["cmd"] == "LC_BUILD_VERSION"
    )


def read_build_versions(path: Path) -> BuildVersions:
    return build_versions(load_commands(capture(["xcrun", "otool", "-l", str(path)])))


def read_archive_builds(path: Path) -> list[tuple[str, BuildVersions]]:
    """The LC_BUILD_VERSION commands of every object in a static library."""
    members: list[tuple[str, list[str]]] = []
    for line in capture(["xcrun", "otool", "-l", str(path)]).splitlines():
        match = ARCHIVE_MEMBER.fullmatch(line)
        if match:
            members.append((f"{path.name}({match['member']})", []))
        elif members:
            members[-1][1].append(line)
    return [
        (member, build_versions(load_commands("\n".join(lines))))
        for member, lines in members
    ]


def read_archs(path: Path) -> tuple[str, ...]:
    return tuple(capture(["xcrun", "lipo", "-archs", str(path)]).split())


def read_macho(path: Path) -> MachO:
    commands = load_commands(capture(["xcrun", "otool", "-l", str(path)]))

    def values(kinds: frozenset[str], key: str) -> tuple[str, ...]:
        return tuple(command[key] for command in commands if command["cmd"] in kinds)

    return MachO(
        path=path,
        archs=read_archs(path),
        install_ids=values(frozenset({"LC_ID_DYLIB"}), "name"),
        rpaths=values(frozenset({"LC_RPATH"}), "path"),
        dylibs=values(DYLIB_COMMANDS, "name"),
        builds=build_versions(commands),
    )


def read_exports(binary: Path) -> frozenset[str]:
    return frozenset(capture(["xcrun", "nm", "-g", "-U", "-j", str(binary)]).split())


def describe_builds(builds: BuildVersions) -> str:
    return (
        ", ".join(f"platform {number} minos {minos}" for number, minos in builds)
        or "no LC_BUILD_VERSION"
    )


def check_architectures(archs: tuple[str, ...]) -> Check:
    return Check("architectures", archs == ARCHS, " ".join(archs))


def check_build_version(builds: BuildVersions, platform: int, floor: str) -> Check:
    return Check(
        "build version", builds == ((platform, floor),), describe_builds(builds)
    )


def check_rpath(macho: MachO, rpath: str) -> Check:
    return Check(
        "rpath", macho.rpaths == (rpath,), ", ".join(macho.rpaths) or "no LC_RPATH"
    )


def check_install_id(macho: MachO, install_id: str) -> Check:
    return Check(
        "install id",
        macho.install_ids == (install_id,),
        ", ".join(macho.install_ids) or "no LC_ID_DYLIB",
    )


def check_dependencies(macho: MachO, bundled: frozenset[str]) -> Check:
    """bundled holds the install ids of the frameworks shipped alongside this binary."""
    foreign = [
        name
        for name in macho.dylibs
        if name not in bundled and not name.startswith(SYSTEM_PREFIXES)
    ]
    return Check(
        "dependencies",
        not foreign,
        ", ".join(foreign)
        or f"{len(macho.dylibs)} load commands, all bundled or system",
    )


def check_linked_frameworks(macho: MachO, required: tuple[str, ...]) -> Check:
    """required are install ids this binary must load."""
    missing = [name for name in required if name not in macho.dylibs]
    return Check(
        "linked frameworks",
        not missing,
        f"no {', '.join(missing)}" if missing else ", ".join(required),
    )


def check_object_build_versions(
    objects: list[tuple[str, BuildVersions]], platform: int, floor: str
) -> Check:
    expected = ((platform, floor),)
    wrong = [
        f"{member} has {describe_builds(builds)}"
        for member, builds in objects
        if builds != expected
    ]
    return Check(
        "objects build version",
        bool(objects) and not wrong,
        listed(wrong)
        or (
            f"{len(objects)} objects at platform {platform} minos {floor}"
            if objects
            else "no objects"
        ),
    )


def fake_object(
    directory: Path,
    name: str,
    code: str,
    flags: tuple[str, ...],
    slice_: Slice = MACOS,
) -> Path:
    """C++ code compiled with flags and the SDK of slice_ into directory/name.o."""
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / f"{name}.cc"
    source.write_text(code)
    target = source.with_suffix(".o")
    capture([*tool(slice_, "clang++"), *flags, "-c", "-o", str(target), str(source)])
    return target


def fake_archive(directory: Path, name: str, objects: list[Path]) -> Path:
    archive = directory / f"lib{name}.a"
    capture([*tool(MACOS, "ar"), "rcs", str(archive), *map(str, objects)])
    return archive


def fake_dylib(
    directory: Path,
    name: str,
    code: str,
    flags: tuple[str, ...] = TARGETED,
    slice_: Slice = MACOS,
) -> Path:
    """C++ code linked with flags and the SDK of slice_ into directory/libname.dylib."""
    dylib = directory / f"lib{name}.dylib"
    capture(
        [
            *tool(slice_, "clang++"),
            *(*flags, "-dynamiclib", "-o", str(dylib)),
            str(fake_object(directory, name, code, flags, slice_)),
        ]
    )
    return dylib


@counterexample
def archive_object_above_floor(tmp: Path) -> tuple[Check, Check]:
    def build_version(name: str, targets: tuple[str, ...]) -> Check:
        objects = [
            fake_object(
                tmp / name,
                f"object{index}",
                f"int object{index}() {{ return {index}; }}\n",
                ("-target", target),
            )
            for index, target in enumerate(targets)
        ]
        archive = fake_archive(tmp / name, "fake", objects)
        return check_object_build_versions(
            read_archive_builds(archive), MACOS.platform, MACOS.floor
        )

    return (
        build_version("good", (MACOS.target, MACOS.target)),
        build_version("bad", (MACOS.target, "arm64-apple-macos15.0")),
    )


@counterexample
def binary_built_above_floor(tmp: Path) -> tuple[Check, Check]:
    def build_version(name: str, target: str) -> Check:
        dylib = fake_dylib(tmp / name, "fake", FAKE_FUNCTION, ("-target", target))
        return check_build_version(
            read_build_versions(dylib), MACOS.platform, MACOS.floor
        )

    return (
        build_version("good", MACOS.target),
        build_version("bad", "arm64-apple-macos15.0"),
    )


@counterexample
def binary_with_x86_64_slice(tmp: Path) -> tuple[Check, Check]:
    def architectures(name: str, flags: tuple[str, ...]) -> Check:
        dylib = fake_dylib(tmp / name, "fake", FAKE_FUNCTION, flags)
        return check_architectures(read_archs(dylib))

    return (
        architectures("good", TARGETED),
        architectures(
            "bad",
            (
                *("-arch", "arm64", "-arch", "x86_64"),
                f"-mmacosx-version-min={MACOS.floor}",
            ),
        ),
    )


@counterexample
def simulator_binary_built_for_device(tmp: Path) -> tuple[Check, Check]:
    def build_version(name: str, slice_: Slice) -> Check:
        dylib = fake_dylib(
            tmp / name, "fake", FAKE_FUNCTION, ("-target", slice_.target), slice_
        )
        return check_build_version(
            read_build_versions(dylib), IOS_SIMULATOR.platform, IOS_SIMULATOR.floor
        )

    return build_version("good", IOS_SIMULATOR), build_version("bad", IOS)
