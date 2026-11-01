import dataclasses
from collections.abc import Callable
from pathlib import Path

from checkout import fake_git, git
from common import Check, Slice, capture, counterexample, scoped
from glslang import build_glslang
from spirv_cross import build_spirv_cross
from toolchain import NATIVE_FILE, meson_native_file

PKGCONFIG_DIR = Path("lib") / "pkgconfig"

Builder = Callable[[Slice, Path, Path, Path, Path], list[Check]]


@dataclasses.dataclass(frozen=True)
class Dep:
    """build(slice, sdk path, checkout, work dir, prefix) installs the dep under prefix
    and returns its checks."""

    name: str
    repository: str
    tag: str
    commit: str
    build: Builder


GLSLANG = Dep(
    name="glslang",
    repository="https://github.com/KhronosGroup/glslang.git",
    tag="16.6.0",
    commit="e1b562a8bed273a02f30b59b66a5d499793cede5",
    build=build_glslang,
)
SPIRV_CROSS = Dep(
    name="SPIRV-Cross",
    repository="https://github.com/KhronosGroup/SPIRV-Cross.git",
    tag="vulkan-sdk-1.4.363.0",
    commit="f11ba9f0b21ba8fc15153d50a2a1ae31ab1cf8f7",
    build=build_spirv_cross,
)
DEPS = (GLSLANG, SPIRV_CROSS)


def fetch(dep: Dep, checkout: Path) -> Path:
    capture(
        [
            *("git", "-c", "advice.detachedHead=false", "clone", "--quiet"),
            *("--depth", "1", "--branch", dep.tag, dep.repository, str(checkout)),
        ]
    )
    return checkout


def check_commit(dep: Dep, checkout: Path) -> Check:
    head = git(checkout, "rev-parse", "HEAD")
    return Check(
        f"{dep.name} commit",
        head == dep.commit,
        f"{dep.tag} is {head}"
        + ("" if head == dep.commit else f", pinned {dep.commit}"),
    )


def build_deps(
    slice_: Slice, sdk_path: Path, checkouts: dict[Dep, Path], work: Path, prefix: Path
) -> list[Check]:
    """Install every dep for slice_ under prefix, with a native file that finds them."""
    checks = [
        check
        for dep in DEPS
        for check in dep.build(
            slice_, sdk_path, checkouts[dep], work / dep.name, prefix
        )
    ]
    (prefix / NATIVE_FILE).write_text(
        meson_native_file(slice_, sdk_path, prefix / PKGCONFIG_DIR)
    )
    return [scoped(slice_.identifier, check) for check in checks]


def fake_upstream(tmp: Path) -> Dep:
    """GLSLANG pointed at a local repository whose tag sits on its second commit."""
    repo = tmp / "upstream"
    repo.mkdir()
    fake_git(repo, "init", "-q")
    fake_git(repo, "commit", "-q", "--allow-empty", "-m", "first")
    fake_git(repo, "commit", "-q", "--allow-empty", "-m", "release")
    fake_git(repo, "tag", "1.0.0")
    return dataclasses.replace(
        GLSLANG,
        repository=repo.as_uri(),
        tag="1.0.0",
        commit=git(repo, "rev-parse", "HEAD"),
    )


@counterexample
def dep_pinned_to_another_commit(tmp: Path) -> tuple[Check, Check]:
    dep = fake_upstream(tmp)
    checkout = fetch(dep, tmp / "checkout")
    parent = git(tmp / "upstream", "rev-parse", "HEAD~1")
    return (
        check_commit(dep, checkout),
        check_commit(dataclasses.replace(dep, commit=parent), checkout),
    )
