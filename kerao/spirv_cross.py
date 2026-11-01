import re
import shutil
from pathlib import Path

from common import MACOS, Check, Slice, capture, counterexample, listed, scoped
from macho import (
    check_architectures,
    check_object_build_versions,
    read_archive_builds,
    read_archs,
)
from toolchain import cmake_build, compiler, read_sdk

OPTIONS = (
    "-DSPIRV_CROSS_STATIC=ON",
    "-DSPIRV_CROSS_SHARED=OFF",
    "-DSPIRV_CROSS_CLI=OFF",
    "-DSPIRV_CROSS_ENABLE_TESTS=OFF",
    "-DSPIRV_CROSS_ENABLE_GLSL=ON",
    "-DSPIRV_CROSS_ENABLE_MSL=ON",
    "-DSPIRV_CROSS_ENABLE_C_API=ON",
    "-DSPIRV_CROSS_ENABLE_HLSL=OFF",
    "-DSPIRV_CROSS_ENABLE_CPP=OFF",
    "-DSPIRV_CROSS_ENABLE_REFLECT=OFF",
    "-DSPIRV_CROSS_ENABLE_UTIL=OFF",
)
WITHOUT_GIT_VERSION_HEADER = "-UHAVE_SPIRV_CROSS_GIT_VERSION"
ARCHIVES = (
    "libspirv-cross-core.a",
    "libspirv-cross-glsl.a",
    "libspirv-cross-msl.a",
    "libspirv-cross-c.a",
)
HEADERS = Path("include") / "spirv_cross"
LICENSE_TAG = re.compile(
    r"\W*(?:SPDX-License-Identifier|License):\s*(?P<expression>.+?)\s*(?:\*/)?\s*"
)
COMMENT_MARKER = re.compile(r"^\s*(?://+|/?\*+|#+)")
MIT = "MIT"
MIT_GRANT = (
    "Permission is hereby granted, free of charge, to any person obtaining a copy"
)


def read_compiled_files(build_dir: Path, system_roots: list[Path]) -> list[Path]:
    """Every input ninja recorded for an object in build_dir, outside system_roots."""
    output = capture(["ninja", "-C", str(build_dir), "-t", "deps"])
    paths = {
        (build_dir / line.strip()).resolve()
        for line in output.splitlines()
        if line.startswith(" ") and line.strip()
    }
    roots = [root.resolve() for root in system_roots]
    return sorted(
        path for path in paths if not any(path.is_relative_to(root) for root in roots)
    )


def system_roots(slice_: Slice, sdk_path: Path) -> list[Path]:
    clang = compiler(slice_, sdk_path, "clang")
    return [sdk_path, Path(capture([*clang, "-print-resource-dir"]))]


def offers_mit(expression: str) -> bool:
    return MIT in (term.strip() for term in expression.split(" OR "))


def license_problem(path: Path) -> str | None:
    """Why path's license declaration does not offer MIT, or None when it does."""
    lines = path.read_text(errors="replace").splitlines()
    tags = [
        match["expression"]
        for line in lines
        if (match := LICENSE_TAG.fullmatch(line))
    ]
    if tags:
        refused = [tag for tag in tags if not offers_mit(tag)]
        return f"declares {', '.join(refused)}" if refused else None
    prose = " ".join(" ".join(COMMENT_MARKER.sub("", line).split()) for line in lines)
    return None if MIT_GRANT in prose else "no license declaration"


def display_name(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def check_licenses(files: list[Path], root: Path) -> Check:
    """files are compiled into the library; each must offer it under MIT."""
    root = root.resolve()
    problems = [
        f"{display_name(path, root)} {problem}"
        for path in files
        if (problem := license_problem(path))
    ]
    offered = f"{len(files)} compiled files offer MIT" if files else "no compiled files"
    return Check(
        "SPIRV-Cross licenses",
        bool(files) and not problems,
        listed(problems) or offered,
    )


def archive_checks(slice_: Slice, archive: Path) -> list[Check]:
    checks = [
        check_architectures(read_archs(archive)),
        check_object_build_versions(
            read_archive_builds(archive), slice_.platform, slice_.floor
        ),
    ]
    return [scoped(archive.name, check) for check in checks]


def build_spirv_cross(
    slice_: Slice, sdk_path: Path, checkout: Path, work: Path, prefix: Path
) -> list[Check]:
    """Install SPIRV-Cross's headers and static libraries under prefix."""
    build_dir, install = cmake_build(
        slice_,
        sdk_path,
        checkout,
        work,
        OPTIONS,
        f"SPIRV-Cross for {slice_.identifier}",
        cxx_flags=(WITHOUT_GIT_VERSION_HEADER,),
    )
    shutil.copytree(install / HEADERS, prefix / HEADERS)
    (prefix / "lib").mkdir(parents=True, exist_ok=True)
    archives = [
        Path(shutil.copy(install / "lib" / name, prefix / "lib")) for name in ARCHIVES
    ]
    files = read_compiled_files(build_dir, system_roots(slice_, sdk_path))
    return [
        *(check for archive in archives for check in archive_checks(slice_, archive)),
        check_licenses(files, checkout),
    ]


FAKE_CMAKE_PROJECT = """\
cmake_minimum_required(VERSION 3.20)
project(fake CXX)
add_library(fake STATIC fake.cpp)
install(TARGETS fake ARCHIVE DESTINATION lib)
"""
FAKE_SOURCE = """\
/*
 * SPDX-License-Identifier: Apache-2.0 OR MIT
 */
#include <string.h>
#include "tagged.h"
#include "granted.h"
int fake(const char *text) { return (int)strlen(text) + TAGGED + GRANTED; }
"""
FAKE_TAGGED_HEADER = """\
// Copyright: 2014-2024 The Khronos Group Inc.
// License: MIT
#define TAGGED 1
"""
FAKE_GRANTED_HEADER = """\
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and/or associated documentation files (the "Materials"),
#define GRANTED 2
"""


def fake_compiled_files(tmp: Path) -> tuple[Path, list[Path]]:
    """The source directory and compiled files of a fake CMake project, built."""
    source = tmp / "source"
    source.mkdir()
    (source / "CMakeLists.txt").write_text(FAKE_CMAKE_PROJECT)
    (source / "fake.cpp").write_text(FAKE_SOURCE)
    (source / "tagged.h").write_text(FAKE_TAGGED_HEADER)
    (source / "granted.h").write_text(FAKE_GRANTED_HEADER)
    sdk_path = read_sdk(MACOS.sdk).path
    build_dir, _ = cmake_build(MACOS, sdk_path, source, tmp / "work", (), "fake")
    return source, read_compiled_files(build_dir, system_roots(MACOS, sdk_path))


@counterexample
def compiled_header_license_changed(tmp: Path) -> tuple[Check, Check]:
    source, files = fake_compiled_files(tmp)
    good = check_licenses(files, source)
    (source / "tagged.h").write_text(
        FAKE_TAGGED_HEADER.replace("License: MIT", "License: Apache-2.0")
    )
    return good, check_licenses(files, source)


@counterexample
def compiled_source_license_removed(tmp: Path) -> tuple[Check, Check]:
    source, files = fake_compiled_files(tmp)
    good = check_licenses(files, source)
    (source / "fake.cpp").write_text(
        FAKE_SOURCE.replace("SPDX-License-Identifier", "Note")
    )
    return good, check_licenses(files, source)
