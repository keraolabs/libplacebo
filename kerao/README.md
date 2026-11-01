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

- A Mac with Apple silicon.
- Xcode, selected and set up:

  ```sh
  sudo xcode-select --switch /Applications/Xcode.app
  sudo xcodebuild -runFirstLaunch
  ```

- Python 3.11 or newer as `python3`, meson 1.3.0 or newer, ninja, CMake 3.22.1 or newer and pkg-config, all from Homebrew: `brew install python meson ninja cmake pkgconf`. The `/usr/bin/python3` macOS ships is too old. The minimums are libplacebo's and glslang's own; the summary prints the versions a build used. The build was last verified with Xcode 27.0 (27A266a), meson 1.11.1, ninja 1.13.2, CMake 4.3.2 and pkgconf 2.5.1.
- Network access to the two repositories under [Dependencies](#dependencies).
- The output directory of the FFmpeg fork's `kerao/build.py`.

## Usage

`kerao/build.py` is the entry point. It builds the checkout it lives in from any working directory.

```sh
python3 kerao/build.py --output <dir> --mode dev --ffmpeg-artifacts <ffmpeg-output>
python3 kerao/build.py --output <dir> --mode release --ffmpeg-artifacts <ffmpeg-output>
python3 kerao/build.py --self-test
```

- `--output`: a new or empty directory for the build products, outside the checkout.
- `--mode`: `release` or `dev`. Both run the same steps; the summary records which was asked for.
- `--ffmpeg-artifacts`: the output directory of the FFmpeg fork's build. It must exist; nothing in it is read yet.
- `--self-test`: runs the [self-test](#self-test) instead of a build.

Unknown or missing arguments exit with status 2. A build runs every check before it exits and prints a summary on stdout: commit, toolchain and dependencies, then one `pass` or `FAIL` line per check. Build tools write to stderr. The exit status is 0 only when every check passes. A failing tool or a missing file stops the build at once with `error:` on stderr.

### Output

```text
<dir>/deps/macos-arm64/
  include/glslang/…
  include/spirv_cross/…
  lib/libglslang.dylib
  lib/libspirv-cross-core.a
  lib/libspirv-cross-glsl.a
  lib/libspirv-cross-msl.a
  lib/libspirv-cross-c.a
  lib/pkgconfig/glslang.pc
  native.ini
```

The slice is `arm64-apple-macos14.0`. Every binary holds only arm64 and is built for macOS 14.0.

`native.ini` is a meson native file for the slice. Its `c`, `cpp` and `objc` compilers are `xcrun --sdk macosx clang` with `-target arm64-apple-macos14.0` and `--sysroot` set to the SDK `xcrun` reports, and its `pkg_config_libdir` is only the `lib/pkgconfig` directory beside it, so meson finds glslang there and never a package of the host. It leaves `c_args`, `cpp_args` and the link arguments free, so a second `--native-file` given before it can add them.

### Self-test

`--self-test` runs each check against counterexamples without building the dependencies. Every counterexample gives a check an input it must pass and a broken copy it must fail, such as a dylib with an x86_64 slice or a compiled header whose license line no longer offers MIT. It needs Xcode, CMake and ninja, but no network, and takes a few seconds.

A check is a `check_*` function in `kerao/`, and the self-test also fails when no counterexample calls one. A new check therefore comes with a counterexample registered through `@counterexample`.

### Dependencies

Each build clones the pinned tags into a temporary directory and stops if a checkout's commit differs from its pin in `kerao/deps.py`. Both projects' tags are lightweight, so the commit is the pin.

| Library | Repository | Tag | Commit |
| --- | --- | --- | --- |
| glslang | `https://github.com/KhronosGroup/glslang.git` | `16.6.0` | `e1b562a8bed273a02f30b59b66a5d499793cede5` |
| SPIRV-Cross | `https://github.com/KhronosGroup/SPIRV-Cross.git` | `vulkan-sdk-1.4.363.0` | `f11ba9f0b21ba8fc15153d50a2a1ae31ab1cf8f7` |

glslang 16.6.0 is glslang's newest release, and it is the glslang of Vulkan SDK 1.4.363.0. SPIRV-Cross has no releases of its own: Khronos tags the revision it ships in each Vulkan SDK, and `vulkan-sdk-1.4.363.0` is that revision for the same SDK, so the two were released and tested together.

Both are built with CMake and Ninja in Release for each slice.

#### glslang

glslang is built static with `ENABLE_OPT=0`, so without SPIRV-Tools, with `ENABLE_HLSL=OFF`, and without its tools, tests and `External` sources. Its three static libraries `glslang`, `SPIRV` and `glslang-default-resource-limits` are linked with `-force_load` into one `libglslang.dylib`, whose install name is `@rpath/libglslang.dylib` and which is linked with `-Wl,-headerpad_max_install_names`. `glslang.pc` names that dylib as the only library, and libplacebo's glsl meson configuration takes glslang only as this pkg-config package.

glslang compiles in `SPIRV/hex_float.h` and `SPIRV/bitutils.h`, which are under Apache-2.0 alone. Apache-2.0 code must not go into the LGPL libplacebo binary, so glslang stays outside it as a library of its own and ships as its own framework. glslang's Bison output, `glslang/MachineIndependent/glslang_tab.cpp`, is under GPL-3.0-or-later with the Bison exception 2.2, which allows it to be distributed under any terms.

#### SPIRV-Cross

SPIRV-Cross is built as the static libraries the C API and MSL output use: `spirv-cross-core`, `spirv-cross-glsl`, `spirv-cross-msl` and `spirv-cross-c`, with HLSL, C++, reflection, util, the CLI and tests off. libplacebo links them statically under MIT.

The build passes `-UHAVE_SPIRV_CROSS_GIT_VERSION`, so the C API does not compile the generated `gitversion.h`, whose template `cmake/gitversion.in.h` is under Apache-2.0 alone. `spvc_get_commit_revision_and_timestamp()` returns an empty string instead.

After the build, every file ninja recorded as an input of a SPIRV-Cross object, except headers of the SDK and of the compiler, must offer MIT. Its `SPDX-License-Identifier:` and `License:` lines must each name `MIT` as an alternative, or, if it has no such line, it must carry the MIT permission notice. The check fails otherwise and names each file. At the pinned revision these files are SPIRV-Cross's own sources (`Apache-2.0 OR MIT`), `GLSL.std.450.h` (`MIT`), `spirv.h` and `spirv.hpp` (`License: MIT`), and `NonSemanticShaderDebugInfo100.h`, which carries Khronos' MIT notice (`MIT-Khronos-old`) without a tag line. SPIRV-Cross's `.reuse/dep5` lists the four Khronos headers as `LicenseRef-KhronosFreeUse`, which is that same notice; the check reads the files themselves.
