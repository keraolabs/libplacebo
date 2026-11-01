import plistlib
import shutil
from pathlib import Path

from common import (
    FRAMEWORKS,
    HEADER_DIRS,
    SLICES,
    BuildError,
    Check,
    Slice,
    capture,
    counterexample,
    listed,
)
from framework import (
    delivered_framework,
    fake_frameworks,
    framework_headers,
)

INCLUDE_DIR = "include"


def make_xcframework(
    name: str, frameworks_dirs: dict[Slice, Path], output: Path
) -> Path:
    xcframework = output / f"{name}.xcframework"
    command = ["xcrun", "xcodebuild", "-create-xcframework"]
    for frameworks_dir in frameworks_dirs.values():
        command += ["-framework", str(frameworks_dir / f"{name}.framework")]
    capture([*command, "-output", str(xcframework)])
    for slice_ in frameworks_dirs:
        if not delivered_framework(output, slice_, name).is_dir():
            found = sorted(path.name for path in xcframework.iterdir())
            raise BuildError(
                f"{xcframework} has no {slice_.identifier} slice, has: {', '.join(found)}"
            )
    return xcframework


def make_xcframeworks(frameworks_dirs: dict[Slice, Path], output: Path) -> None:
    for name in FRAMEWORKS:
        make_xcframework(name, frameworks_dirs, output)


def check_library_identifiers(xcframework: Path) -> Check:
    with (xcframework / "Info.plist").open("rb") as file:
        plist = plistlib.load(file)
    found = sorted(
        library["LibraryIdentifier"] for library in plist["AvailableLibraries"]
    )
    return Check(
        f"{xcframework.name} library identifiers",
        found == sorted(slice_.identifier for slice_ in SLICES),
        ", ".join(found) or "none",
    )


def header_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def check_public_headers(output: Path, name: str, include_dir: str) -> Check:
    """Compare the Headers of every slice of name and include/<include_dir> byte
    for byte."""
    trees = {
        slice_.identifier: header_files(
            framework_headers(delivered_framework(output, slice_, name), slice_.layout)
        )
        for slice_ in SLICES
    }
    trees[INCLUDE_DIR] = header_files(output / INCLUDE_DIR / include_dir)
    (reference_label, reference), *others = trees.items()
    problems = [f"{label} has no headers" for label, tree in trees.items() if not tree]
    problems += [
        f"{path} differs between {reference_label} and {label}"
        for label, tree in others
        for path in sorted(reference.keys() | tree.keys())
        if tree.get(path) != reference.get(path)
    ]
    return Check(
        f"{name} public headers",
        not problems,
        listed(problems) or f"{len(reference)} identical in {', '.join(trees)}",
    )


def public_headers_checks(output: Path) -> list[Check]:
    return [
        check_public_headers(output, name, HEADER_DIRS[name]) for name in FRAMEWORKS
    ]


def fake_output(tmp: Path) -> Path:
    """An output holding a libplacebo.xcframework with every slice and its include/ tree."""
    output = tmp / "output"
    output.mkdir()
    frameworks_dirs = {}
    for slice_ in SLICES:
        frameworks = fake_frameworks(tmp / slice_.identifier, slice_)
        frameworks_dirs[slice_] = frameworks["libplacebo"].parent
    make_xcframework("libplacebo", frameworks_dirs, output)
    shutil.copytree(
        tmp / SLICES[0].identifier / "prefix" / INCLUDE_DIR / HEADER_DIRS["libplacebo"],
        output / INCLUDE_DIR / HEADER_DIRS["libplacebo"],
    )
    return output


def copy_output(output: Path, directory: Path) -> Path:
    copy = directory / output.name
    shutil.copytree(output, copy, symlinks=True)
    return copy


@counterexample
def config_header_differs_in_one_slice(tmp: Path) -> tuple[Check, Check]:
    output = fake_output(tmp)
    include_dir = HEADER_DIRS["libplacebo"]
    good = check_public_headers(output, "libplacebo", include_dir)
    bad = copy_output(output, tmp / "bad")
    slice_ = SLICES[-1]
    framework = delivered_framework(bad, slice_, "libplacebo")
    header = framework_headers(framework, slice_.layout) / "config.h"
    header.write_text(header.read_text().replace("int", "long"))
    return good, check_public_headers(bad, "libplacebo", include_dir)


@counterexample
def xcframework_without_a_slice(tmp: Path) -> tuple[Check, Check]:
    xcframework = fake_output(tmp) / "libplacebo.xcframework"
    good = check_library_identifiers(xcframework)
    bad = copy_output(xcframework.parent, tmp / "bad") / xcframework.name
    removed = SLICES[-1].identifier
    shutil.rmtree(bad / removed)
    with (bad / "Info.plist").open("rb") as file:
        plist = plistlib.load(file)
    plist["AvailableLibraries"] = [
        library
        for library in plist["AvailableLibraries"]
        if library["LibraryIdentifier"] != removed
    ]
    with (bad / "Info.plist").open("wb") as file:
        plistlib.dump(plist, file)
    return good, check_library_identifiers(bad)
