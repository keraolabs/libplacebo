import dataclasses
import os
from pathlib import Path

from common import capture


@dataclasses.dataclass(frozen=True)
class Source:
    root: Path
    commit: str
    version: str
    changes: tuple[str, ...]

    @property
    def dirty(self) -> bool:
        return bool(self.changes)


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return capture(["git", "-C", str(root), *args], env=env)


def read_source(root: Path) -> Source:
    return Source(
        root=root,
        commit=git(root, "rev-parse", "HEAD"),
        version=git(root, "describe", "--tags", "--always"),
        changes=tuple(
            git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
        ),
    )


FAKE_GIT_ENV = {
    "PATH": os.environ["PATH"],
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "self-test",
    "GIT_AUTHOR_EMAIL": "self-test@invalid",
    "GIT_COMMITTER_NAME": "self-test",
    "GIT_COMMITTER_EMAIL": "self-test@invalid",
}


def fake_git(repo: Path, *args: str) -> None:
    git(repo, *args, env=FAKE_GIT_ENV)
