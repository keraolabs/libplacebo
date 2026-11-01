# kerao

Kerao's fork of libplacebo for Apple platforms, with a Metal backend. Files only the fork has, such as this README and the build and test scripts, live in this directory; changes to libplacebo itself go where upstream keeps that code.

## Branches

- `kerao` is the default branch and holds every change of the fork. It is pinned to upstream's `v7.387.0`: commit `275f7dea`, which the annotated tag object `451d8ea5` points to.
- `master` mirrors upstream and never takes a commit.
- Moving to a newer upstream release needs a stated reason, and is done by rebasing `kerao` onto it.
- The fork is maintained here for good and never goes upstream. Upstream takes a backend only with someone maintaining it there, which Kerao does not take on.

## Release tags

A release is an annotated tag `v7.387.0-kerao.<n>` on `kerao`. `<n>` starts at 1 and counts separately for each upstream tag: the first release on a later upstream tag `v7.x.y` is `v7.x.y-kerao.1`. A tag is never moved, deleted or reused, so a fix ships as the next `<n>`. Several releases of Kerao itself can ship the same fork tag.

## API version

`PL_API_VER` keeps upstream's value, and the fork has no API version of its own. The Metal backend is the meson component `metal`, which generates `PL_HAVE_METAL`.

## Reference implementations

Three earlier Metal backends are references only:

- lhc70000's [`metal-expr`](https://github.com/lhc70000/libplacebo/tree/metal-expr)
- ab300819's [`metal-backend`](https://github.com/ab300819/libplacebo/tree/metal-backend)
- simonch's [MR !858](https://code.videolan.org/videolan/libplacebo/-/merge_requests/858)

A commit that takes code from one of them credits it in its message. The backend here is a redesign because of the defects the three share, and none of them may reappear:

- one command buffer for the whole `pl_gpu`
- ring slots chosen by the swapchain's `frame_index`, so offscreen rendering always writes slot 0
- CPU writes without conflict tracking
- `tex_poll` always returning true
- `pl_timer` never returning data
- macOS-only APIs that break the iOS build

## Pull requests

Changes reach `kerao` only through pull requests. A pull request is merged only when the test script passes in full, and its description carries the script's summary as evidence.

The fork has no CI: the Metal device of GitHub-hosted macOS runners is "Apple Paravirtual device", not real hardware. The test script runs on a Mac and an iPhone instead.

## Prerequisites

To be written with the build script.

## Usage

To be written with the build script and the test script.
