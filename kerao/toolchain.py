import dataclasses
import re
from pathlib import Path

from common import MACOS, SLICES, BuildError, Slice, build_env, capture, stream

TOOLS = ("meson", "ninja", "cmake", "pkg-config")
CMAKE_VERSION_PREFIX = "cmake version "
CMAKE_SYSTEMS = {MACOS: "Darwin"}
CMAKE_PROCESSOR = "arm64"
NATIVE_FILE = "native.ini"


@dataclasses.dataclass(frozen=True)
class Sdk:
    name: str
    version: str
    path: Path


@dataclasses.dataclass(frozen=True)
class Toolchain:
    xcode_version: str
    xcode_build: str
    sdks: dict[str, Sdk]
    tools: dict[str, str]


def read_sdk(name: str) -> Sdk:
    def show(what: str) -> str:
        return capture(["xcrun", "--sdk", name, f"--show-sdk-{what}"])

    return Sdk(name=name, version=show("version"), path=Path(show("path")))


def read_tool_version(name: str) -> str:
    output = capture([name, "--version"])
    return output.splitlines()[0].removeprefix(CMAKE_VERSION_PREFIX) if output else ""


def read_toolchain() -> Toolchain:
    version = re.fullmatch(
        r"Xcode (\S+)\nBuild version (\S+)",
        capture(["xcrun", "xcodebuild", "-version"]),
    )
    if version is None:
        raise BuildError("cannot read the Xcode version from xcodebuild -version")
    return Toolchain(
        xcode_version=version[1],
        xcode_build=version[2],
        sdks={slice_.identifier: read_sdk(slice_.sdk) for slice_ in SLICES},
        tools={name: read_tool_version(name) for name in TOOLS},
    )


def tool(slice_: Slice, *args: str) -> list[str]:
    return ["xcrun", "--sdk", slice_.sdk, *args]


def find_tool(slice_: Slice, name: str) -> str:
    return capture([*tool(slice_, "--find"), name])


def compiler(slice_: Slice, sdk_path: Path, name: str) -> list[str]:
    """The compiler driver name for slice_, with its target and SDK."""
    return [*tool(slice_, name), "-target", slice_.target, "--sysroot", str(sdk_path)]


def link_archives(
    slice_: Slice, sdk_path: Path, archives: list[Path], dylib: Path, *flags: str
) -> None:
    """Link every object of archives, with flags, into dylib."""
    capture(
        [
            *compiler(slice_, sdk_path, "clang++"),
            "-dynamiclib",
            *(f"-Wl,-force_load,{archive}" for archive in archives),
            *flags,
            *("-o", str(dylib)),
        ]
    )


def meson_literal(values: list[str]) -> str:
    quoted = (value.replace("\\", "\\\\").replace("'", "\\'") for value in values)
    return "[" + ", ".join(f"'{value}'" for value in quoted) + "]"


def meson_native_file(slice_: Slice, sdk_path: Path, pkgconfig_dir: Path) -> str:
    """A native file compiling for slice_ that finds packages only in pkgconfig_dir."""
    return f"""\
[binaries]
c = {meson_literal(compiler(slice_, sdk_path, "clang"))}
cpp = {meson_literal(compiler(slice_, sdk_path, "clang++"))}
objc = {meson_literal(compiler(slice_, sdk_path, "clang"))}
ar = {meson_literal(tool(slice_, "ar"))}
strip = {meson_literal(tool(slice_, "strip"))}

[properties]
pkg_config_libdir = {meson_literal([str(pkgconfig_dir)])}
"""


def cmake_build(
    slice_: Slice,
    sdk_path: Path,
    source: Path,
    work: Path,
    options: tuple[str, ...],
    what: str,
    cxx_flags: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """Configure, build and install the CMake project at source.

    Returns its build directory and install prefix."""
    build_dir = work / "build"
    prefix = work / "install"
    flags = f"-target {slice_.target}"
    stream(
        [
            *("cmake", "-S", str(source), "-B", str(build_dir), "-G", "Ninja"),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={prefix}",
            f"-DCMAKE_SYSTEM_NAME={CMAKE_SYSTEMS[slice_]}",
            f"-DCMAKE_SYSTEM_PROCESSOR={CMAKE_PROCESSOR}",
            f"-DCMAKE_OSX_SYSROOT={sdk_path}",
            f"-DCMAKE_C_COMPILER={find_tool(slice_, 'clang')}",
            f"-DCMAKE_CXX_COMPILER={find_tool(slice_, 'clang++')}",
            f"-DCMAKE_C_FLAGS={flags}",
            f"-DCMAKE_CXX_FLAGS={' '.join((flags, *cxx_flags))}",
            *options,
        ],
        f"cmake configure of {what}",
        env=build_env(),
    )
    stream(
        ["cmake", "--build", str(build_dir)], f"cmake build of {what}", env=build_env()
    )
    stream(
        ["cmake", "--install", str(build_dir)],
        f"cmake install of {what}",
        env=build_env(),
    )
    return build_dir, prefix
