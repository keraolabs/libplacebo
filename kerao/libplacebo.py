import re
from pathlib import Path

from common import PKGCONFIG_DIR, ROOT, BuildError, Library, Slice, build_env, stream
from ffmpeg import FFmpeg, compile_args
from toolchain import meson_native_file

MESON_OPTIONS = (
    "--auto-features=disabled",
    "-Dmetal=enabled",
    "-Dglslang=enabled",
    "-Ddovi=enabled",
    "-Dlibdovi=disabled",
    "-Ddemos=false",
)
TEST_OPTIONS = ("-Dtests=true",)
HEADERPAD = "-Wl,-headerpad_max_install_names"
NATIVE_FILE = "native.ini"
DYLIB = re.compile(r"libplacebo\.[0-9]+\.dylib")
NOTICE = "LICENSE"


def build_libplacebo(
    slice_: Slice,
    sdk_path: Path,
    deps_prefix: Path,
    ffmpeg: FFmpeg,
    work: Path,
    options: tuple[str, ...],
    link_args: list[str],
) -> Path:
    """Configure and compile this checkout for slice_ against the deps installed
    under deps_prefix; returns the meson build directory."""
    work.mkdir(parents=True, exist_ok=True)
    native = work / NATIVE_FILE
    native.write_text(
        meson_native_file(
            slice_,
            sdk_path,
            deps_prefix / PKGCONFIG_DIR,
            compile_args(ffmpeg, slice_),
            [HEADERPAD, *link_args],
        )
    )
    build_dir = work / "build"
    what = f"libplacebo for {slice_.identifier}"
    stream(
        [
            *("meson", "setup", str(build_dir), str(ROOT)),
            *("--native-file", str(native), f"--prefix={work / 'prefix'}"),
            *options,
        ],
        f"meson setup of {what}",
        env=build_env(),
    )
    stream(
        ["meson", "compile", "-C", str(build_dir)],
        f"meson compile of {what}",
        env=build_env(),
    )
    return build_dir


def install_libplacebo(build_dir: Path, prefix: Path) -> Library:
    """Install the build into prefix, the --prefix it was configured with."""
    stream(
        ["meson", "install", "-C", str(build_dir), "--no-rebuild", "--quiet"],
        "meson install of libplacebo",
        env=build_env(),
    )
    lib = prefix / "lib"
    found = [
        path
        for path in lib.iterdir()
        if DYLIB.fullmatch(path.name) and not path.is_symlink()
    ]
    if len(found) != 1:
        raise BuildError(
            f"expected one libplacebo.<soversion>.dylib in {lib}, found {len(found)}"
        )
    return Library(dylib=found[0], headers=prefix / "include" / "libplacebo")
