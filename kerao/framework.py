import plistlib
import re
import shutil
from pathlib import Path

import privacy
from checkout import UPSTREAM_VERSION
from common import (
    FRAMEWORKS,
    HEADER_DIRS,
    IOS,
    MACOS,
    Check,
    Library,
    Layout,
    Slice,
    capture,
    counterexample,
    scoped,
)
from macho import (
    check_architectures,
    check_build_version,
    check_dependencies,
    check_install_id,
    check_linked_frameworks,
    check_rpath,
    read_macho,
)
from toolchain import FAKE_TOOLCHAIN, Toolchain

BUNDLE_ID_PREFIX = "org.keraolabs."
DT_COMPILER = "com.apple.compilers.llvm.clang.1_0"
LINKED_INSTALL_NAME = re.compile(r"@rpath/(lib\w+?)(?:\.[0-9]+)?\.dylib")
INFO_PLIST_KEYS = (
    "BuildMachineOSBuild",
    "CFBundleDevelopmentRegion",
    "CFBundleExecutable",
    "CFBundleIdentifier",
    "CFBundleInfoDictionaryVersion",
    "CFBundleName",
    "CFBundlePackageType",
    "CFBundleShortVersionString",
    "CFBundleSupportedPlatforms",
    "CFBundleVersion",
    "DTCompiler",
    "DTPlatformBuild",
    "DTPlatformName",
    "DTPlatformVersion",
    "DTSDKBuild",
    "DTSDKName",
    "DTXcode",
    "DTXcodeBuild",
)


def install_id(name: str, layout: Layout) -> str:
    return f"@rpath/{(Path(f'{name}.framework') / layout.contents / name).as_posix()}"


def bundled(layout: Layout, names: tuple[str, ...]) -> frozenset[str]:
    return frozenset(install_id(name, layout) for name in names)


def framework_binary(framework: Path, layout: Layout) -> Path:
    return framework / layout.contents / framework.stem


def framework_headers(framework: Path, layout: Layout) -> Path:
    return framework / layout.contents / "Headers"


def delivered_framework(output: Path, slice_: Slice, name: str) -> Path:
    return output / f"{name}.xcframework" / slice_.identifier / f"{name}.framework"


def framework_plist(framework: Path, layout: Layout) -> Path:
    return framework / layout.resources / "Info.plist"


def xcode_number(version: str) -> str:
    major, minor, patch = (version.split(".") + ["0", "0"])[:3]
    return f"{int(major):02d}{minor}{patch}"


def platform_keys(slice_: Slice) -> dict[str, object]:
    keys: dict[str, object] = {slice_.minimum_version_key: slice_.floor}
    if slice_.device_families:
        keys["UIDeviceFamily"] = list(slice_.device_families)
    if slice_.required_capabilities:
        keys["UIRequiredDeviceCapabilities"] = list(slice_.required_capabilities)
    return keys


def info_plist(
    slice_: Slice, name: str, toolchain: Toolchain, bundle_version: str
) -> dict[str, object]:
    sdk = toolchain.sdks[slice_.identifier]
    return {
        "BuildMachineOSBuild": toolchain.machine_build,
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": name,
        "CFBundleIdentifier": BUNDLE_ID_PREFIX + name,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": name,
        "CFBundlePackageType": "FMWK",
        "CFBundleShortVersionString": UPSTREAM_VERSION,
        "CFBundleSupportedPlatforms": [slice_.bundle_platform],
        "CFBundleVersion": bundle_version,
        "DTCompiler": DT_COMPILER,
        "DTPlatformBuild": sdk.build,
        "DTPlatformName": sdk.name,
        "DTPlatformVersion": sdk.platform_version,
        "DTSDKBuild": sdk.build,
        "DTSDKName": f"{sdk.name}{sdk.version}",
        "DTXcode": xcode_number(toolchain.xcode_version),
        "DTXcodeBuild": toolchain.xcode_build,
        **platform_keys(slice_),
    }


def rewrite_install_names(binary: Path, name: str, layout: Layout) -> None:
    """Give binary its framework install id and the sibling rpath, and load every
    framework of FRAMEWORKS it links by its framework install id."""
    changes: list[str] = []
    for dependency in read_macho(binary).dylibs:
        match = LINKED_INSTALL_NAME.fullmatch(dependency)
        if match and match[1] in FRAMEWORKS:
            changes += ["-change", dependency, install_id(match[1], layout)]
    capture(
        [
            *("xcrun", "install_name_tool", "-id", install_id(name, layout)),
            *changes,
            *("-add_rpath", layout.rpath, str(binary)),
        ]
    )


def make_framework(
    name: str,
    library: Library,
    plist: dict[str, object],
    record: privacy.Record,
    frameworks_dir: Path,
    layout: Layout,
) -> Path:
    framework = frameworks_dir / f"{name}.framework"
    contents = framework / layout.contents
    contents.mkdir(parents=True)
    (framework / layout.resources).mkdir(exist_ok=True)
    shutil.copy(library.dylib, contents / name)
    shutil.copytree(library.headers, framework_headers(framework, layout))
    with framework_plist(framework, layout).open("wb") as file:
        plistlib.dump(plist, file)
    privacy.write_manifest(framework, layout, record)
    if layout.contents.parts:
        current = layout.contents.parent / "Current"
        (framework / current).symlink_to(layout.contents.name)
        for entry in (name, "Headers", "Resources"):
            (framework / entry).symlink_to(current / entry)
    rewrite_install_names(contents / name, name, layout)
    return framework


def package_slice(
    slice_: Slice,
    toolchain: Toolchain,
    bundle_version: str,
    libraries: dict[str, Library],
    records: privacy.Records,
    frameworks_dir: Path,
) -> None:
    """Turn libraries, keyed by framework name, into one framework each."""
    frameworks_dir.mkdir(parents=True)
    for name in FRAMEWORKS:
        make_framework(
            name,
            libraries[name],
            info_plist(slice_, name, toolchain, bundle_version),
            records.libraries[name],
            frameworks_dir,
            slice_.layout,
        )


def check_info_plist(framework: Path, slice_: Slice, bundle_version: str) -> Check:
    name = framework.stem
    with framework_plist(framework, slice_.layout).open("rb") as file:
        plist = plistlib.load(file)
    required = {
        **dict.fromkeys(INFO_PLIST_KEYS),
        "CFBundleExecutable": name,
        "CFBundleIdentifier": BUNDLE_ID_PREFIX + name,
        "CFBundleName": name,
        "CFBundlePackageType": "FMWK",
        "CFBundleShortVersionString": UPSTREAM_VERSION,
        "CFBundleSupportedPlatforms": [slice_.bundle_platform],
        "CFBundleVersion": bundle_version,
        **platform_keys(slice_),
    }
    problems = [
        f"no {key}" if key not in plist else f"{key} is {plist[key]!r}"
        for key, value in required.items()
        if key not in plist or (value is not None and plist[key] != value)
    ]
    return Check(
        "Info.plist",
        not problems,
        "; ".join(problems)
        or f"{UPSTREAM_VERSION} ({bundle_version}), "
        f"{slice_.minimum_version_key} {slice_.floor}",
    )


def framework_checks(
    output: Path,
    slice_: Slice,
    name: str,
    records: privacy.Records,
    bundle_version: str,
    ffmpeg_libraries: tuple[str, ...],
) -> list[Check]:
    """ffmpeg_libraries are the FFmpeg frameworks the delivered ones may load."""
    layout = slice_.layout
    framework = delivered_framework(output, slice_, name)
    record = records.libraries[name]
    macho = read_macho(framework_binary(framework, layout))
    checks = [
        check_build_version(macho.builds, slice_.platform, slice_.floor),
        check_architectures(macho.archs),
        check_install_id(macho, install_id(name, layout)),
        check_rpath(macho, layout.rpath),
        check_dependencies(macho, bundled(layout, FRAMEWORKS + ffmpeg_libraries)),
        check_info_plist(framework, slice_, bundle_version),
        privacy.check_manifest(framework, layout, record),
        privacy.check_required_reason_apis(macho.path, record, records.apple),
    ]
    return [scoped(f"{slice_.identifier} {name}", check) for check in checks]


def linked_frameworks_check(
    output: Path, slice_: Slice, name: str, required: tuple[str, ...]
) -> Check:
    """name's framework must load each framework of required through @rpath."""
    framework = delivered_framework(output, slice_, name)
    macho = read_macho(framework_binary(framework, slice_.layout))
    return scoped(
        f"{slice_.identifier} {name}",
        check_linked_frameworks(
            macho, tuple(install_id(other, slice_.layout) for other in required)
        ),
    )


FAKE_SOVERSION = 387


def fake_frameworks(tmp: Path, slice_: Slice = MACOS) -> dict[str, Path]:
    """Package a libglslang and a libplacebo that links it, linked the way the
    build links them."""
    prefix = tmp / "prefix"
    (prefix / "lib").mkdir(parents=True)
    records = privacy.read_records()
    frameworks: dict[str, Path] = {}
    linked: list[str] = []
    for name, dylib_name in (
        ("libglslang", "libglslang.dylib"),
        ("libplacebo", f"libplacebo.{FAKE_SOVERSION}.dylib"),
    ):
        headers = prefix / "include" / HEADER_DIRS[name]
        headers.mkdir(parents=True)
        (headers / "config.h").write_text(f"int {name}_version(void);\n")
        source = prefix / f"{name}.c"
        source.write_text(f"int {name}_version(void) {{ return 1; }}\n")
        dylib = prefix / "lib" / dylib_name
        capture(
            [
                *("xcrun", "--sdk", slice_.sdk, "clang", "-dynamiclib"),
                *("-target", slice_.target),
                f"-Wl,-install_name,@rpath/{dylib_name}",
                "-Wl,-headerpad_max_install_names",
                *("-o", str(dylib), str(source), *linked),
            ]
        )
        linked.append(str(dylib))
        frameworks[name] = make_framework(
            name,
            Library(dylib=dylib, headers=headers),
            info_plist(slice_, name, FAKE_TOOLCHAIN, "1"),
            records.libraries[name],
            tmp / "frameworks",
            slice_.layout,
        )
    return frameworks


def copy_framework(framework: Path, directory: Path) -> Path:
    copy = directory / framework.name
    shutil.copytree(framework, copy, symlinks=True)
    return copy


def install_name_tool(binary: Path, *args: str) -> None:
    capture(["xcrun", "install_name_tool", *args, str(binary)])


@counterexample
def framework_with_second_rpath(tmp: Path) -> tuple[Check, Check]:
    framework = fake_frameworks(tmp)["libplacebo"]
    good = check_rpath(
        read_macho(framework_binary(framework, MACOS.layout)), MACOS.layout.rpath
    )
    bad = framework_binary(copy_framework(framework, tmp / "bad"), MACOS.layout)
    install_name_tool(bad, "-add_rpath", str(tmp))
    return good, check_rpath(read_macho(bad), MACOS.layout.rpath)


@counterexample
def install_id_is_absolute(tmp: Path) -> tuple[Check, Check]:
    framework = fake_frameworks(tmp)["libplacebo"]
    expected = install_id("libplacebo", MACOS.layout)
    good = check_install_id(
        read_macho(framework_binary(framework, MACOS.layout)), expected
    )
    bad = framework_binary(copy_framework(framework, tmp / "bad"), MACOS.layout)
    install_name_tool(bad, "-id", str(bad))
    return good, check_install_id(read_macho(bad), expected)


@counterexample
def dependency_left_on_linked_install_name(tmp: Path) -> tuple[Check, Check]:
    framework = fake_frameworks(tmp)["libplacebo"]
    allowed = bundled(MACOS.layout, FRAMEWORKS)
    good = check_dependencies(
        read_macho(framework_binary(framework, MACOS.layout)), allowed
    )
    bad = framework_binary(copy_framework(framework, tmp / "bad"), MACOS.layout)
    install_name_tool(
        bad,
        "-change",
        install_id("libglslang", MACOS.layout),
        "@rpath/libglslang.dylib",
    )
    return good, check_dependencies(read_macho(bad), allowed)


@counterexample
def framework_dependency_loaded_by_absolute_path(tmp: Path) -> tuple[Check, Check]:
    framework = fake_frameworks(tmp)["libplacebo"]
    required = (install_id("libglslang", MACOS.layout),)
    good = check_linked_frameworks(
        read_macho(framework_binary(framework, MACOS.layout)), required
    )
    bad = framework_binary(copy_framework(framework, tmp / "bad"), MACOS.layout)
    install_name_tool(
        bad, "-change", required[0], str(tmp / "libglslang.framework" / "libglslang")
    )
    return good, check_linked_frameworks(read_macho(bad), required)


@counterexample
def info_plist_with_another_version(tmp: Path) -> tuple[Check, Check]:
    framework = fake_frameworks(tmp)["libplacebo"]
    good = check_info_plist(framework, MACOS, "1")
    bad = copy_framework(framework, tmp / "bad")
    with framework_plist(bad, MACOS.layout).open("rb") as file:
        plist = plistlib.load(file)
    plist["CFBundleShortVersionString"] = "7.386.0"
    with framework_plist(bad, MACOS.layout).open("wb") as file:
        plistlib.dump(plist, file)
    return good, check_info_plist(bad, MACOS, "1")


@counterexample
def info_plist_without_minimum_version(tmp: Path) -> tuple[Check, Check]:
    framework = fake_frameworks(tmp)["libplacebo"]
    good = check_info_plist(framework, MACOS, "1")
    bad = copy_framework(framework, tmp / "bad")
    with framework_plist(bad, MACOS.layout).open("rb") as file:
        plist = plistlib.load(file)
    del plist[MACOS.minimum_version_key]
    with framework_plist(bad, MACOS.layout).open("wb") as file:
        plistlib.dump(plist, file)
    return good, check_info_plist(bad, MACOS, "1")


@counterexample
def ios_info_plist_without_device_family(tmp: Path) -> tuple[Check, Check]:
    framework = fake_frameworks(tmp, IOS)["libplacebo"]
    good = check_info_plist(framework, IOS, "1")
    bad = copy_framework(framework, tmp / "bad")
    with framework_plist(bad, IOS.layout).open("rb") as file:
        plist = plistlib.load(file)
    del plist["UIDeviceFamily"]
    with framework_plist(bad, IOS.layout).open("wb") as file:
        plistlib.dump(plist, file)
    return good, check_info_plist(bad, IOS, "1")


@counterexample
def framework_without_privacy_manifest(tmp: Path) -> tuple[Check, Check]:
    framework = fake_frameworks(tmp)["libplacebo"]
    record = privacy.read_records().libraries["libplacebo"]
    good = privacy.check_manifest(framework, MACOS.layout, record)
    bad = copy_framework(framework, tmp / "bad")
    privacy.manifest_path(bad, MACOS.layout).unlink()
    return good, privacy.check_manifest(bad, MACOS.layout, record)


@counterexample
def privacy_manifest_differs_from_record(tmp: Path) -> tuple[Check, Check]:
    framework = fake_frameworks(tmp)["libplacebo"]
    records = privacy.read_records()
    declared = privacy.fake_record(
        tmp, "file_size", [privacy.FSTAT_RECORD], records.apple
    )
    privacy.write_manifest(framework, MACOS.layout, declared)
    good = privacy.check_manifest(framework, MACOS.layout, declared)
    return good, privacy.check_manifest(
        framework, MACOS.layout, records.libraries["libplacebo"]
    )
