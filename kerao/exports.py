import re
from pathlib import Path

from common import MACOS, BuildError, Check, capture, counterexample, listed
from macho import read_exports

COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
DIRECTIVE = re.compile(r"^[ \t]*#(?:[^\n]*\\\n)*[^\n]*", re.MULTILINE)
DEPRECATION = re.compile(r"\bPL_DEPRECATED(?:_ENUM)?_IN\s*\([^)]*\)")
API_DECLARATION = re.compile(r"\bPL_(?:LIBAV_)?API\b(?!_)(?P<declaration>[^;{]*)")
LIBAV_DECLARATION = re.compile(r"\bPL_LIBAV_API\b(?P<declaration>[^;{]*)")
DECLARED_NAME = re.compile(r"(?P<name>[A-Za-z_]\w*)\s*(?:\(|\[|$)")
API_VERSION_DEFINE = re.compile(r"#define PL_API_VER (\d+)")
VERSIONED_NAME = re.compile(
    r"^[ \t]*#define[ \t]+(?P<name>\w+)[ \t]+\w+\(\s*(?P<prefix>\w+)\s*,\s*PL_API_VER\s*\)",
    re.MULTILINE,
)
CONFIG_HEADER = "config.h"
LIBAV_HEADER = Path("utils") / "libav.h"
SYMBOL_PREFIX = "_"


def declarations(text: str, pattern: re.Pattern[str]) -> list[str]:
    """The declarations pattern finds in C text, without comments, preprocessor
    lines and deprecation markers."""
    code = DEPRECATION.sub("", DIRECTIVE.sub("", COMMENT.sub(" ", text)))
    return [" ".join(match["declaration"].split()) for match in pattern.finditer(code)]


def declared_name(declaration: str) -> str:
    match = DECLARED_NAME.search(declaration)
    if match is None:
        raise BuildError(f"cannot find the name declared by PL_API {declaration}")
    return match["name"]


def api_version(headers: Path) -> str:
    match = API_VERSION_DEFINE.search((headers / CONFIG_HEADER).read_text())
    if match is None:
        raise BuildError(f"no PL_API_VER in {headers / CONFIG_HEADER}")
    return match[1]


def declared_symbols(headers: Path) -> frozenset[str]:
    """The symbols the public headers under headers declare with PL_API, named
    as the library exports them."""
    version = api_version(headers)
    names: set[str] = set()
    renamed: dict[str, str] = {}
    for header in sorted(headers.rglob("*.h")):
        text = header.read_text()
        names.update(map(declared_name, declarations(text, API_DECLARATION)))
        renamed.update(
            (match["name"], match["prefix"] + version)
            for match in VERSIONED_NAME.finditer(text)
        )
    return frozenset(renamed.get(name, name) for name in names)


def libav_functions(headers: Path) -> frozenset[str]:
    text = (headers / LIBAV_HEADER).read_text()
    return frozenset(map(declared_name, declarations(text, LIBAV_DECLARATION)))


def exported_symbols(binary: Path) -> frozenset[str]:
    return frozenset(name.removeprefix(SYMBOL_PREFIX) for name in read_exports(binary))


def check_exports(exports: frozenset[str], declared: frozenset[str]) -> Check:
    """exports are a library's exported symbols; declared are its PL_API symbols."""
    undeclared = sorted(exports - declared)
    return Check(
        "exports",
        bool(exports) and not undeclared,
        f"not declared PL_API: {listed(undeclared)}"
        if undeclared
        else f"{len(exports)} symbols, all declared PL_API",
    )


def check_libav_exports(exports: frozenset[str], functions: frozenset[str]) -> Check:
    """functions are the PL_LIBAV_API functions of libav.h."""
    missing = sorted(functions - exports)
    return Check(
        "PL_LIBAV_API exports",
        bool(functions) and not missing,
        f"not exported: {listed(missing)}"
        if missing
        else f"{len(functions)} functions exported",
    )


def export_checks(binary: Path, headers: Path) -> list[Check]:
    exports = exported_symbols(binary)
    return [
        check_exports(exports, declared_symbols(headers)),
        check_libav_exports(exports, libav_functions(headers)),
    ]


FAKE_CONFIG = (
    '#define PL_API_VER 7\n#define PL_API __attribute__((visibility("default")))\n'
)
FAKE_LOG_HEADER = """\
#define pl_log_glue1(x, y) x##y
#define pl_log_glue2(x, y) pl_log_glue1(x, y)
#define pl_log_create pl_log_glue2(pl_log_create_, PL_API_VER)
// PL_API void pl_commented_out(void);
PL_API int pl_log_create(int api_ver);
PL_DEPRECATED_IN(v6.1) PL_API extern const int pl_table[];
"""
FAKE_LIBAV_HEADER = """\
#define PL_LIBAV_API PL_API
PL_LIBAV_API int pl_system_from_av(int spc);
PL_LIBAV_API void pl_frame_from_avframe(void *out,
                                        const void *frame);
"""
FAKE_DEFINITIONS = {
    "pl_log_create_7": "int pl_log_create_7(int v) { return v; }",
    "pl_table": "const int pl_table[] = { 1 };",
    "pl_system_from_av": "int pl_system_from_av(int spc) { return spc; }",
    "pl_frame_from_avframe": "void pl_frame_from_avframe(void *o, const void *f) {}",
    "spvc_context_create": "int spvc_context_create(void) { return 0; }",
}


def fake_headers(tmp: Path) -> Path:
    headers = tmp / "headers"
    (headers / LIBAV_HEADER.parent).mkdir(parents=True)
    (headers / CONFIG_HEADER).write_text(FAKE_CONFIG)
    (headers / "log.h").write_text(FAKE_LOG_HEADER)
    (headers / LIBAV_HEADER).write_text(FAKE_LIBAV_HEADER)
    return headers


def fake_library(directory: Path, symbols: list[str]) -> Path:
    """A macOS dylib defining symbols from FAKE_DEFINITIONS."""
    directory.mkdir(parents=True)
    source = directory / "library.c"
    source.write_text("\n".join(FAKE_DEFINITIONS[symbol] for symbol in symbols) + "\n")
    dylib = directory / "library.dylib"
    capture(
        [
            *("xcrun", "--sdk", MACOS.sdk, "clang", "-dynamiclib"),
            *("-target", MACOS.target, "-o", str(dylib), str(source)),
        ]
    )
    return dylib


DECLARED = ["pl_log_create_7", "pl_table", "pl_system_from_av", "pl_frame_from_avframe"]


@counterexample
def internal_symbol_exported(tmp: Path) -> tuple[Check, Check]:
    declared = declared_symbols(fake_headers(tmp))
    good = exported_symbols(fake_library(tmp / "good", DECLARED))
    bad = exported_symbols(
        fake_library(tmp / "bad", [*DECLARED, "spvc_context_create"])
    )
    return check_exports(good, declared), check_exports(bad, declared)


@counterexample
def libav_function_not_exported(tmp: Path) -> tuple[Check, Check]:
    functions = libav_functions(fake_headers(tmp))
    good = exported_symbols(fake_library(tmp / "good", DECLARED))
    bad = exported_symbols(fake_library(tmp / "bad", DECLARED[:-1]))
    return check_libav_exports(good, functions), check_libav_exports(bad, functions)
