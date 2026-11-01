import re
from pathlib import Path

from common import ARCHS, MACOS, Check, capture, counterexample, listed
from toolchain import tool

OFFSET_SUFFIX = re.compile(r" \(offset \d+\)$")
ARCHIVE_MEMBER = re.compile(r"\S.*\((?P<member>[^()]+)\):")

BuildVersions = tuple[tuple[int, str], ...]
TARGETED = ("-target", MACOS.target)
FAKE_FUNCTION = "int fake() { return 0; }\n"


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
    directory: Path, name: str, code: str, flags: tuple[str, ...]
) -> Path:
    """C++ code compiled with flags into directory/name.o."""
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / f"{name}.cc"
    source.write_text(code)
    target = source.with_suffix(".o")
    capture([*tool(MACOS, "clang++"), *flags, "-c", "-o", str(target), str(source)])
    return target


def fake_archive(directory: Path, name: str, objects: list[Path]) -> Path:
    archive = directory / f"lib{name}.a"
    capture([*tool(MACOS, "ar"), "rcs", str(archive), *map(str, objects)])
    return archive


def fake_dylib(
    directory: Path, name: str, code: str, flags: tuple[str, ...] = TARGETED
) -> Path:
    """C++ code linked with flags into directory/libname.dylib."""
    dylib = directory / f"lib{name}.dylib"
    capture(
        [
            *tool(MACOS, "clang++"),
            *(*flags, "-dynamiclib", "-o", str(dylib)),
            str(fake_object(directory, name, code, flags)),
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
