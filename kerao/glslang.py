import re
import shutil
from pathlib import Path

from common import (
    MACOS,
    BuildError,
    Check,
    Slice,
    capture,
    counterexample,
    listed,
    scoped,
)
from macho import (
    TARGETED,
    check_architectures,
    check_build_version,
    check_object_build_versions,
    fake_archive,
    fake_dylib,
    fake_object,
    read_archive_builds,
    read_archs,
    read_build_versions,
)
from toolchain import cmake_build, compiler, read_sdk

OPTIONS = (
    "-DBUILD_SHARED_LIBS=OFF",
    "-DENABLE_OPT=0",
    "-DENABLE_HLSL=OFF",
    "-DBUILD_EXTERNAL=OFF",
    "-DENABLE_GLSLANG_BINARIES=OFF",
    "-DGLSLANG_TESTS=OFF",
)
ARCHIVES = ("libglslang.a", "libSPIRV.a", "libglslang-default-resource-limits.a")
DYLIB = "libglslang.dylib"
INSTALL_NAME = f"@rpath/{DYLIB}"
PKGCONFIG = "glslang.pc"
BUILD_INFO = Path("include") / "glslang" / "build_info.h"
VERSION_DEFINE = re.compile(r"#define GLSLANG_VERSION_(MAJOR|MINOR|PATCH) (\d+)")
SPIRV_TOOLS_SYMBOL = re.compile(r"spvtools::|^_spv[A-Z]")
HLSL_SYMBOL = re.compile(r"glslang::(?:Hlsl|TBuiltInParseablesHlsl)")
EXTERNAL_DEFINITION = re.compile(r"[0-9a-f]+ \([^)]*\) external (?P<name>\S+)")


def link_dylib(
    slice_: Slice, sdk_path: Path, archives: list[Path], dylib: Path
) -> None:
    """Link every object of archives into dylib, which loads as INSTALL_NAME."""
    capture(
        [
            *compiler(slice_, sdk_path, "clang++"),
            "-dynamiclib",
            *(f"-Wl,-force_load,{archive}" for archive in archives),
            *("-install_name", INSTALL_NAME, "-Wl,-headerpad_max_install_names"),
            *("-o", str(dylib)),
        ]
    )


def read_version(build_info: Path) -> str:
    parts = dict(VERSION_DEFINE.findall(build_info.read_text()))
    if parts.keys() != {"MAJOR", "MINOR", "PATCH"}:
        raise BuildError(f"cannot read glslang's version from {build_info}")
    return f"{parts['MAJOR']}.{parts['MINOR']}.{parts['PATCH']}"


def pkgconfig(prefix: Path, version: str) -> str:
    return f"""\
prefix={prefix}
libdir=${{prefix}}/lib
includedir=${{prefix}}/include

Name: glslang
Description: glslang, SPIRV and glslang-default-resource-limits in one library
Version: {version}
Libs: -L${{libdir}} -lglslang
Cflags: -I${{includedir}}
"""


def read_symbols(binary: Path) -> list[str]:
    """Every symbol name in binary's symbol table, defined or not, demangled."""
    return capture(["xcrun", "nm", "-C", "-j", str(binary)]).splitlines()


def read_external_definitions(archive: Path) -> frozenset[str]:
    """The symbols an archive defines as external, neither weak nor hidden."""
    output = capture(["xcrun", "nm", "-g", "-U", "-m", str(archive)])
    return frozenset(
        match["name"]
        for line in output.splitlines()
        if (match := EXTERNAL_DEFINITION.fullmatch(line))
    )


def read_exports(binary: Path) -> frozenset[str]:
    return frozenset(capture(["xcrun", "nm", "-g", "-U", "-j", str(binary)]).split())


def check_no_symbols(what: str, symbols: list[str], pattern: re.Pattern[str]) -> Check:
    found = sorted({symbol for symbol in symbols if pattern.search(symbol)}, key=len)
    return Check(
        f"{what} symbols", not found, listed(found) or f"none of {len(symbols)}"
    )


def check_archives_exported(
    archives: dict[str, frozenset[str]], exports: frozenset[str]
) -> Check:
    unexported = {name: symbols - exports for name, symbols in archives.items()}
    missing = [
        f"{name} defines {len(symbols)} unexported, such as {min(symbols)}"
        for name, symbols in unexported.items()
        if symbols
    ]
    total = sum(len(symbols) for symbols in archives.values())
    return Check(
        "merged archives",
        not missing,
        listed(missing) or f"{total} symbols of {', '.join(archives)} exported",
    )


def dylib_checks(slice_: Slice, dylib: Path, archives: list[Path]) -> list[Check]:
    symbols = read_symbols(dylib)
    checks = [
        check_architectures(read_archs(dylib)),
        check_build_version(read_build_versions(dylib), slice_.platform, slice_.floor),
        check_no_symbols("SPIRV-Tools", symbols, SPIRV_TOOLS_SYMBOL),
        check_no_symbols("HLSL", symbols, HLSL_SYMBOL),
        check_archives_exported(
            {archive.name: read_external_definitions(archive) for archive in archives},
            read_exports(dylib),
        ),
    ]
    return [scoped(DYLIB, check) for check in checks]


def build_glslang(
    slice_: Slice, sdk_path: Path, checkout: Path, work: Path, prefix: Path
) -> list[Check]:
    """Install glslang's headers and libglslang.dylib under prefix."""
    _, install = cmake_build(
        slice_, sdk_path, checkout, work, OPTIONS, f"glslang for {slice_.identifier}"
    )
    archives = [install / "lib" / name for name in ARCHIVES]
    shutil.copytree(install / "include" / "glslang", prefix / "include" / "glslang")
    (prefix / "lib" / "pkgconfig").mkdir(parents=True)
    dylib = prefix / "lib" / DYLIB
    link_dylib(slice_, sdk_path, archives, dylib)
    (prefix / "lib" / "pkgconfig" / PKGCONFIG).write_text(
        pkgconfig(prefix, read_version(install / BUILD_INFO))
    )
    objects = [
        member for archive in archives for member in read_archive_builds(archive)
    ]
    return [
        scoped(
            "glslang",
            check_object_build_versions(objects, slice_.platform, slice_.floor),
        ),
        *dylib_checks(slice_, dylib, archives),
    ]


def fake_symbols(tmp: Path, code: str) -> list[str]:
    return read_symbols(fake_dylib(tmp, "fake", code))


@counterexample
def dylib_with_spirv_tools_symbols(tmp: Path) -> tuple[Check, Check]:
    good = fake_symbols(
        tmp / "good", "namespace spvutils { int HexFloat() { return 0; } }\n"
    )
    bad = fake_symbols(
        tmp / "bad",
        "namespace spvtools { int Optimizer() { return 0; } }\n"
        'extern "C" int spvContextCreate() { return 0; }\n',
    )
    return (
        check_no_symbols("SPIRV-Tools", good, SPIRV_TOOLS_SYMBOL),
        check_no_symbols("SPIRV-Tools", bad, SPIRV_TOOLS_SYMBOL),
    )


@counterexample
def dylib_with_hlsl_symbols(tmp: Path) -> tuple[Check, Check]:
    good = fake_symbols(
        tmp / "good",
        "namespace glslang { struct TShader { int setHlslIoMapping(); };\n"
        "int TShader::setHlslIoMapping() { return 0; } }\n",
    )
    bad = fake_symbols(
        tmp / "bad",
        "namespace glslang { struct HlslGrammar { int acceptDeclaration(); };\n"
        "int HlslGrammar::acceptDeclaration() { return 0; } }\n",
    )
    return (
        check_no_symbols("HLSL", good, HLSL_SYMBOL),
        check_no_symbols("HLSL", bad, HLSL_SYMBOL),
    )


@counterexample
def dylib_without_one_archive(tmp: Path) -> tuple[Check, Check]:
    def archive(name: str) -> Path:
        code = f'extern "C" int {name}() {{ return 0; }}\n'
        return fake_archive(tmp, name, [fake_object(tmp, name, code, TARGETED)])

    archives = [archive("alpha"), archive("beta")]
    defined = {
        archive.name: read_external_definitions(archive) for archive in archives
    }
    sdk_path = read_sdk(MACOS.sdk).path

    def merged(name: str, linked: list[Path]) -> Check:
        dylib = tmp / f"{name}.dylib"
        link_dylib(MACOS, sdk_path, linked, dylib)
        return check_archives_exported(defined, read_exports(dylib))

    return merged("good", archives), merged("bad", archives[:1])
