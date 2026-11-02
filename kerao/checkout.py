import dataclasses
import os
import re
from pathlib import Path

from common import BuildError, Check, capture, counterexample, listed

UPSTREAM_TAG = "v7.387.0"
UPSTREAM_VERSION = UPSTREAM_TAG.removeprefix("v")
RELEASE_TAG = re.compile(rf"{re.escape(UPSTREAM_TAG)}-kerao\.([1-9][0-9]*)")
DEV_BUNDLE_VERSION = "0"
SUBMODULES = (
    "3rdparty/Vulkan-Headers",
    "3rdparty/fast_float",
    "3rdparty/jinja",
    "3rdparty/markupsafe",
)
SUBMODULE_STATUS = re.compile(r"(?P<state>[-+U ]?)[0-9a-f]+ (?P<path>\S+)(?: .*)?")
SUBMODULE_AT_RECORDED_COMMIT = " "


@dataclasses.dataclass(frozen=True)
class Source:
    """describe is `git describe --dirty`, None when no annotated tag is reachable."""

    root: Path
    commit: str
    version: str
    describe: str | None
    head_tags: tuple[tuple[str, str], ...]
    changes: tuple[str, ...]
    submodules: tuple[str, ...]

    @property
    def dirty(self) -> bool:
        return bool(self.changes)


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return capture(["git", "-C", str(root), *args], env=env)


def read_describe(root: Path) -> str | None:
    try:
        return git(root, "describe", "--dirty")
    except BuildError:
        return None


def read_source(root: Path) -> Source:
    tags = git(
        root,
        "for-each-ref",
        "--points-at",
        "HEAD",
        "--format=%(refname:strip=2) %(objecttype)",
        "refs/tags",
    )
    return Source(
        root=root,
        commit=git(root, "rev-parse", "HEAD"),
        version=git(root, "describe", "--tags", "--always"),
        describe=read_describe(root),
        head_tags=tuple(tuple(line.split(" ")) for line in tags.splitlines()),
        changes=tuple(
            git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
        ),
        submodules=tuple(
            git(root, "submodule", "status", "--", *SUBMODULES).splitlines()
        ),
    )


def release_tag(source: Source) -> str | None:
    """The annotated v7.387.0-kerao.<n> tag at HEAD, or None unless there is exactly one."""
    tags = [
        name
        for name, kind in source.head_tags
        if kind == "tag" and RELEASE_TAG.fullmatch(name)
    ]
    return tags[0] if len(tags) == 1 else None


def bundle_version(source: Source, mode: str) -> str:
    tag = release_tag(source) if mode == "release" else None
    return RELEASE_TAG.fullmatch(tag)[1] if tag else DEV_BUNDLE_VERSION


def artifact_tag(source: Source, mode: str) -> str:
    tag = release_tag(source) if mode == "release" else None
    return tag or source.version


def expected_pl_version(source: Source, mode: str) -> str:
    """What pl_version() returns for a build of source: src/version.py's string."""
    if mode == "release":
        return f"{UPSTREAM_TAG} ({release_tag(source)})"
    return f"{UPSTREAM_TAG} ({source.describe})" if source.describe else UPSTREAM_TAG


def check_release_tag(source: Source) -> Check:
    tag = release_tag(source)
    if tag is None:
        found = (
            ", ".join(f"{name} ({kind})" for name, kind in source.head_tags) or "none"
        )
        return Check(
            "release tag",
            False,
            f"HEAD needs one annotated {UPSTREAM_TAG}-kerao.<n> tag, has: {found}",
        )
    return Check("release tag", True, tag)


def check_clean_worktree(source: Source) -> Check:
    return Check("clean worktree", not source.dirty, listed(list(source.changes)))


def check_submodules(source: Source) -> Check:
    """source.submodules are `git submodule status` lines of SUBMODULES."""
    found = {
        match["path"]: match["state"] or SUBMODULE_AT_RECORDED_COMMIT
        for line in source.submodules
        if (match := SUBMODULE_STATUS.fullmatch(line))
    }
    wrong = [
        f"{path} is {'not initialised' if found.get(path, '-') == '-' else 'not at its recorded commit'}"
        for path in SUBMODULES
        if found.get(path) != SUBMODULE_AT_RECORDED_COMMIT
    ]
    return Check(
        "submodules",
        not wrong,
        (listed(wrong) + "; run git submodule update --init " + " ".join(SUBMODULES))
        if wrong
        else f"{len(SUBMODULES)} at their recorded commits",
    )


def check_output_outside(source: Source, output: Path) -> Check:
    """output is resolved."""
    inside = output.is_relative_to(source.root)
    return Check(
        "output outside the checkout",
        not inside,
        f"{output} is in the checkout {source.root}" if inside else str(output),
    )


def preconditions(source: Source, mode: str) -> list[Check]:
    checks = [check_submodules(source)]
    if mode == "release":
        checks += [check_release_tag(source), check_clean_worktree(source)]
    return checks


FAKE_GIT_ENV = {
    "PATH": os.environ["PATH"],
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "self-test",
    "GIT_AUTHOR_EMAIL": "self-test@invalid",
    "GIT_COMMITTER_NAME": "self-test",
    "GIT_COMMITTER_EMAIL": "self-test@invalid",
    "GIT_ALLOW_PROTOCOL": "file",
}


def fake_git(repo: Path, *args: str) -> None:
    git(repo, "-c", "protocol.file.allow=always", *args, env=FAKE_GIT_ENV)


def fake_release_checkout(tmp: Path) -> Path:
    """A repository tagged like the fork, whose SUBMODULES are initialised."""
    module = tmp / "module"
    module.mkdir(parents=True)
    fake_git(module, "init", "-q")
    fake_git(module, "commit", "-q", "--allow-empty", "-m", "module")
    repo = tmp / "checkout"
    repo.mkdir()
    fake_git(repo, "init", "-q")
    for path in SUBMODULES:
        fake_git(repo, "submodule", "--quiet", "add", module.as_uri(), path)
    fake_git(repo, "commit", "-q", "-m", "upstream")
    fake_git(repo, "tag", "-a", UPSTREAM_TAG, "-m", UPSTREAM_TAG)
    fake_git(repo, "commit", "-q", "--allow-empty", "-m", "kerao")
    fake_git(repo, "tag", "-a", f"{UPSTREAM_TAG}-kerao.1", "-m", "release")
    return repo


@counterexample
def head_one_commit_after_release_tag(tmp: Path) -> tuple[Check, Check]:
    repo = fake_release_checkout(tmp)
    good = check_release_tag(read_source(repo))
    fake_git(repo, "commit", "-q", "--allow-empty", "-m", "after the tag")
    return good, check_release_tag(read_source(repo))


@counterexample
def release_tag_is_lightweight(tmp: Path) -> tuple[Check, Check]:
    repo = fake_release_checkout(tmp)
    good = check_release_tag(read_source(repo))
    fake_git(repo, "commit", "-q", "--allow-empty", "-m", "after the tag")
    fake_git(repo, "tag", f"{UPSTREAM_TAG}-kerao.2")
    return good, check_release_tag(read_source(repo))


@counterexample
def untracked_file_in_worktree(tmp: Path) -> tuple[Check, Check]:
    repo = fake_release_checkout(tmp)
    good = check_clean_worktree(read_source(repo))
    (repo / "untracked.txt").write_text("stray\n")
    return good, check_clean_worktree(read_source(repo))


@counterexample
def submodule_not_initialised(tmp: Path) -> tuple[Check, Check]:
    repo = fake_release_checkout(tmp)
    good = check_submodules(read_source(repo))
    fake_git(repo, "submodule", "--quiet", "deinit", "--force", SUBMODULES[-1])
    return good, check_submodules(read_source(repo))


@counterexample
def output_inside_checkout(tmp: Path) -> tuple[Check, Check]:
    source = read_source(fake_release_checkout(tmp))
    return (
        check_output_outside(source, tmp / "output"),
        check_output_outside(source, source.root / "output"),
    )
