import dataclasses
import json
import plistlib
import subprocess
from pathlib import Path
from typing import Any

from common import (
    FRAMEWORKS,
    KERAO,
    MACOS,
    BuildError,
    Check,
    Layout,
    capture,
    counterexample,
)

RECORDS = KERAO / "required-reason-api"
APPLE_LIST = "apple.json"
MANIFEST_NAME = "PrivacyInfo.xcprivacy"
CATEGORY_PREFIX = "NSPrivacyAccessedAPICategory"
METHNAME_SECTION = ("__TEXT", "__objc_methname")
METHNAME_HEADER = "Contents of (__TEXT,__objc_methname) section"


@dataclasses.dataclass(frozen=True)
class Api:
    """An API of Apple's list, found in a binary by its imported symbol or its selector."""

    name: str
    category: str
    symbol: str | None
    selector: str | None


@dataclasses.dataclass(frozen=True)
class AppleList:
    reasons: dict[str, frozenset[str]]
    apis: tuple[Api, ...]


@dataclasses.dataclass(frozen=True)
class Entry:
    category: str
    apis: tuple[str, ...]
    purpose: str
    reasons: tuple[str, ...]
    rationale: str


Record = tuple[Entry, ...]


@dataclasses.dataclass(frozen=True)
class Records:
    apple: AppleList
    libraries: dict[str, Record]


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise BuildError(f"cannot read {path}: {error}") from error


def read_apple_list(path: Path) -> AppleList:
    try:
        categories = read_json(path)["categories"]
        apis = tuple(
            Api(name, category, reference.get("symbol"), reference.get("selector"))
            for category, body in categories.items()
            for name, reference in body["apis"].items()
        )
        reasons = {
            category: frozenset(body["reasons"])
            for category, body in categories.items()
        }
    except (KeyError, TypeError, AttributeError) as error:
        raise BuildError(f"{path} is malformed: {error!r}") from error
    unfindable = [
        api.name for api in apis if (api.symbol is None) == (api.selector is None)
    ]
    if unfindable:
        raise BuildError(
            f"{path}: {', '.join(unfindable)} need exactly one of symbol and selector"
        )
    return AppleList(reasons=reasons, apis=apis)


def entry_problems(entry: Entry, apple: AppleList) -> list[str]:
    if entry.category not in apple.reasons:
        return [f"{entry.category} is not a category of {APPLE_LIST}"]
    known = {api.name for api in apple.apis if api.category == entry.category}
    problems = [
        f"{entry.category} has no API {name}"
        for name in entry.apis
        if name not in known
    ]
    problems += [
        f"{reason} is not an approved reason for {entry.category}"
        for reason in entry.reasons
        if reason not in apple.reasons[entry.category]
    ]
    problems += [
        f"{entry.category} has no {field}"
        for field in ("apis", "purpose", "reasons", "rationale")
        if not getattr(entry, field)
    ]
    return problems


def read_record(path: Path, apple: AppleList) -> Record:
    try:
        record = tuple(
            Entry(
                category=item["category"],
                apis=tuple(item["apis"]),
                purpose=item["purpose"],
                reasons=tuple(item["reasons"]),
                rationale=item["rationale"],
            )
            for item in read_json(path)["accessed_api_types"]
        )
    except (KeyError, TypeError) as error:
        raise BuildError(f"{path} is malformed: {error!r}") from error
    problems = [problem for entry in record for problem in entry_problems(entry, apple)]
    categories = [entry.category for entry in record]
    problems += [
        f"{category} appears more than once"
        for category in sorted(set(categories))
        if categories.count(category) > 1
    ]
    if problems:
        raise BuildError(f"{path}: {'; '.join(problems)}")
    return record


def read_records(directory: Path = RECORDS) -> Records:
    apple = read_apple_list(directory / APPLE_LIST)
    return Records(
        apple=apple,
        libraries={
            name: read_record(directory / f"{name}.json", apple) for name in FRAMEWORKS
        },
    )


def manifest(record: Record) -> dict[str, object]:
    return {
        "NSPrivacyTracking": False,
        "NSPrivacyTrackingDomains": [],
        "NSPrivacyCollectedDataTypes": [],
        "NSPrivacyAccessedAPITypes": [
            {
                "NSPrivacyAccessedAPIType": entry.category,
                "NSPrivacyAccessedAPITypeReasons": list(entry.reasons),
            }
            for entry in record
        ],
    }


def short(category: str) -> str:
    return category.removeprefix(CATEGORY_PREFIX)


def manifest_path(framework: Path, layout: Layout) -> Path:
    return framework / layout.resources / MANIFEST_NAME


def write_manifest(framework: Path, layout: Layout, record: Record) -> None:
    with manifest_path(framework, layout).open("wb") as file:
        plistlib.dump(manifest(record), file)


def lint(path: Path) -> str | None:
    """None when plutil accepts path, else plutil's complaint."""
    try:
        result = subprocess.run(
            ["plutil", "-lint", str(path)], check=False, capture_output=True, text=True
        )
    except FileNotFoundError as error:
        raise BuildError("plutil not found") from error
    if result.returncode == 0:
        return None
    return (result.stdout or result.stderr).strip()


def check_manifest(framework: Path, layout: Layout, record: Record) -> Check:
    path = manifest_path(framework, layout)
    location = path.relative_to(framework).as_posix()
    if not path.is_file():
        return Check("privacy manifest", False, f"no {location}")
    complaint = lint(path)
    if complaint is not None:
        return Check("privacy manifest", False, complaint)
    with path.open("rb") as file:
        found = plistlib.load(file)
    expected = manifest(record)
    problems = [
        (
            f"{key} is {found[key]!r}, record gives {expected.get(key)!r}"
            if key in found
            else f"no {key}"
        )
        for key in sorted(expected.keys() | found.keys())
        if found.get(key) != expected.get(key)
    ]
    declared = ", ".join(
        f"{short(entry.category)} {' '.join(entry.reasons)}" for entry in record
    )
    return Check(
        "privacy manifest",
        not problems,
        "; ".join(problems) or f"{location}: {declared or 'no accessed API types'}",
    )


def imported_symbols(binary: Path) -> frozenset[str]:
    return frozenset(capture(["xcrun", "nm", "-u", str(binary)]).split())


def referenced_selectors(binary: Path) -> frozenset[str]:
    output = capture(["xcrun", "otool", "-v", "-s", *METHNAME_SECTION, str(binary)])
    _, _, contents = output.partition(METHNAME_HEADER)
    return frozenset(
        line.split(maxsplit=1)[1]
        for line in contents.splitlines()
        if len(line.split(maxsplit=1)) == 2
    )


def reference(api: Api) -> str:
    return api.symbol or f"@selector({api.selector})"


def used_apis(binary: Path, apple: AppleList) -> dict[str, list[Api]]:
    """The APIs of Apple's list that binary uses, keyed by how binary refers to them."""
    symbols = imported_symbols(binary)
    selectors = referenced_selectors(binary)
    used: dict[str, list[Api]] = {}
    for api in apple.apis:
        if api.symbol in symbols or api.selector in selectors:
            used.setdefault(reference(api), []).append(api)
    return used


def check_required_reason_apis(binary: Path, record: Record, apple: AppleList) -> Check:
    """Fail when binary uses an API of Apple's list that record does not declare."""
    used = used_apis(binary, apple)
    recorded = {(entry.category, name) for entry in record for name in entry.apis}
    problems: list[str] = []
    hits: list[str] = []
    for found, apis in used.items():
        declared = [api for api in apis if (api.category, api.name) in recorded]
        categories = "/".join(short(api.category) for api in declared or apis)
        if declared:
            hits.append(f"{found} {categories}")
        else:
            problems.append(f"{found} {categories} not in the record")
    seen = {(api.category, api.name) for apis in used.values() for api in apis}
    unseen = [f"{name} {short(category)}" for category, name in sorted(recorded - seen)]
    notes = [f"recorded, not seen: {', '.join(unseen)}"] if unseen else []
    return Check(
        "required-reason APIs",
        not problems,
        "; ".join([*(problems or hits or ["none used"]), *notes]),
    )


def fake_dylib(
    directory: Path, source_name: str, source: str, flags: tuple[str, ...] = ()
) -> Path:
    """A macOS dylib compiled from source; source_name's suffix picks the language."""
    directory.mkdir(parents=True)
    path = directory / source_name
    path.write_text(source)
    dylib = directory / "fake.dylib"
    capture(
        [
            *("xcrun", "--sdk", MACOS.sdk, "clang", "-dynamiclib", *flags),
            *("-target", MACOS.target, "-o", str(dylib), str(path)),
        ]
    )
    return dylib


FSTAT_SOURCE = """\
#include <sys/stat.h>
long long file_size(int fd) { struct stat st; return fstat(fd, &st) ? -1 : st.st_size; }
"""
FSTAT_RECORD = {
    "category": "NSPrivacyAccessedAPICategoryFileTimestamp",
    "apis": ["fstat"],
    "purpose": "Reads the size of a file the app opened.",
    "reasons": ["0A2A.1"],
    "rationale": "file_size() returns the size of the file its caller passes.",
}
UPTIME_SOURCE = """\
#import <Foundation/Foundation.h>
double uptime(void) { return NSProcessInfo.processInfo.systemUptime; }
"""
UPTIME_RECORD = {
    "category": "NSPrivacyAccessedAPICategorySystemBootTime",
    "apis": ["systemUptime"],
    "purpose": "Measures time between two events in the app.",
    "reasons": ["35F9.1"],
    "rationale": "uptime() returns the system uptime to its caller.",
}


def fake_record(tmp: Path, name: str, entries: list[dict], apple: AppleList) -> Record:
    path = tmp / f"{name}.json"
    path.write_text(json.dumps({"accessed_api_types": entries}))
    return read_record(path, apple)


@counterexample
def record_without_an_imported_api(tmp: Path) -> tuple[Check, Check]:
    records = read_records()
    binary = fake_dylib(tmp / "dylib", "file_size.c", FSTAT_SOURCE)
    declared = fake_record(tmp, "file_size", [FSTAT_RECORD], records.apple)
    return (
        check_required_reason_apis(binary, declared, records.apple),
        check_required_reason_apis(
            binary, records.libraries["libplacebo"], records.apple
        ),
    )


@counterexample
def selector_not_in_record(tmp: Path) -> tuple[Check, Check]:
    records = read_records()
    binary = fake_dylib(
        tmp / "dylib", "uptime.m", UPTIME_SOURCE, ("-framework", "Foundation")
    )
    declared = fake_record(tmp, "uptime", [UPTIME_RECORD], records.apple)
    return (
        check_required_reason_apis(binary, declared, records.apple),
        check_required_reason_apis(
            binary, records.libraries["libglslang"], records.apple
        ),
    )
