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

- A Mac with Apple silicon. The test script runs the Metal tests on it.
- Xcode, selected and set up:

  ```sh
  sudo xcode-select --switch /Applications/Xcode.app
  sudo xcodebuild -runFirstLaunch
  ```

- Python 3.11 or newer as `python3`, meson 1.3.0 or newer, ninja, CMake 3.22.1 or newer and pkg-config, all from Homebrew: `brew install python meson ninja cmake pkgconf`. The `/usr/bin/python3` macOS ships is too old. The minimums are libplacebo's and glslang's own; the summary prints the versions a build used. The build was last verified with Xcode 27.0 (27A266a), meson 1.11.1, ninja 1.13.2, CMake 4.3.2 and pkgconf 2.5.1.
- Four of upstream's submodules, at the commits the checkout records:

  ```sh
  git submodule update --init 3rdparty/Vulkan-Headers 3rdparty/fast_float 3rdparty/jinja 3rdparty/markupsafe
  ```

  Without them meson would take Jinja from the host or build without fast_float, so both scripts refuse to start.
- A booted iOS Simulator running iOS 17.0 or newer, for the build's consumer program. `xcrun simctl list devices available` lists them and `xcrun simctl boot <udid>` boots one. Without one the build stops before it starts; the test script does not need it.
- Network access to the two repositories under [Dependencies](#dependencies).
- The output directory of the FFmpeg fork's `kerao/build.py` for the pinned FFmpeg tag, `n9.0.2-kerao.1` (commit `ae49c6ac4a2409553ca0f9a00f9e252cbce632a0`). The scripts link against it and never build FFmpeg themselves.

## Usage

Both scripts build the checkout they live in, from any working directory.

```sh
python3 kerao/build.py --output <dir> --mode dev --ffmpeg-artifacts <ffmpeg-output>
python3 kerao/build.py --output <dir> --mode release --ffmpeg-artifacts <ffmpeg-output>
python3 kerao/build.py --self-test
python3 kerao/test.py --ffmpeg-artifacts <ffmpeg-output>
```

- `--output`: a new or empty directory for the build products, outside the checkout.
- `--mode`: `release` or `dev`, see [Modes](#modes).
- `--ffmpeg-artifacts`: the output directory of the FFmpeg fork's build.
- `--self-test`: runs the [self-test](#self-test) instead of a build.

Unknown or missing arguments exit with status 2. Each script runs every check before it exits and prints a summary on stdout: commit, toolchain, dependencies and FFmpeg input, then one `pass` or `FAIL` line per check. Build tools write to stderr. The exit status is 0 only when every check passes. A failing tool or a missing file stops the script at once with `error:` on stderr. When a precondition fails, the summary lists only the preconditions and ends with `not built: preconditions failed` (`not tested` for the test script).

### Modes

A `release` build needs HEAD to carry exactly one annotated `v7.387.0-kerao.<n>` tag and the worktree to be clean, untracked files included. It accepts only an FFmpeg output whose `artifact.json` has `mode` `release`. Its frameworks carry `CFBundleVersion` `<n>`, and its `pl_version()` must be exactly `v7.387.0 (v7.387.0-kerao.<n>)`: upstream's `src/version.py` takes the version from `git describe --dirty`, which sees only annotated tags.

A `dev` build takes any commit and any FFmpeg mode, and its `artifact.json` says `dev`. Its frameworks carry `CFBundleVersion` `0`, and `pl_version()` must equal what `src/version.py` makes of the checkout.

In both modes the FFmpeg output's `tag` and `commit` must equal the pins in `kerao/ffmpeg.py`, and every binary its `artifact.json` lists must have the recorded SHA-256. That identity goes into `inputs` of the build's own `artifact.json`.

### Output

```text
<dir>/
  libplacebo.xcframework/ios-arm64/libplacebo.framework
  libplacebo.xcframework/ios-arm64-simulator/libplacebo.framework
  libplacebo.xcframework/macos-arm64/libplacebo.framework
  libglslang.xcframework/ios-arm64/libglslang.framework
  libglslang.xcframework/ios-arm64-simulator/libglslang.framework
  libglslang.xcframework/macos-arm64/libglslang.framework
  include/libplacebo/…
  include/glslang/…
  artifact.json
  notices/libplacebo/LICENSE
  notices/glslang/LICENSE.txt
  notices/SPIRV-Cross/MIT.txt
  notices/SPIRV-Cross/LicenseRef-KhronosFreeUse.txt
```

Each xcframework has three slices, all arm64 only and none x86_64:

| Slice | Target | Layout | Install name | Only `LC_RPATH` |
| --- | --- | --- | --- | --- |
| `ios-arm64` | `arm64-apple-ios17.0` | flat | `@rpath/<name>.framework/<name>` | `@loader_path/..` |
| `ios-arm64-simulator` | `arm64-apple-ios17.0-simulator` | flat | `@rpath/<name>.framework/<name>` | `@loader_path/..` |
| `macos-arm64` | `arm64-apple-macos14.0` | `Versions/A` | `@rpath/<name>.framework/Versions/A/<name>` | `@loader_path/../../..` |

The rpath is the directory holding the frameworks. Libraries are linked with `-Wl,-headerpad_max_install_names`, install names are rewritten after linking, and nothing is signed. libplacebo loads `libglslang`, `libavutil` and `libavcodec` through `@rpath`, so an app embeds all of them, and `libswresample`, which `libavcodec` loads, next to each other.

Each framework has an `Info.plist` with the keys of Xcode's framework template, `CFBundleShortVersionString` `7.387.0` and the slice's minimum OS: `MinimumOSVersion` `17.0` with `UIDeviceFamily` `[1, 2]` on iOS, plus `UIRequiredDeviceCapabilities` `[arm64]` on the device slice, and `LSMinimumSystemVersion` `14.0` on macOS. Next to it is a `PrivacyInfo.xcprivacy` generated from the library's record in `kerao/required-reason-api/`; both sit at the framework's root in the flat layout and in `Resources/` on macOS. Neither library calls an API of Apple's required-reason list, which the build checks against the binaries' imported symbols and Objective-C selectors.

`Headers/` holds the library's public headers, libplacebo's with the `config.h` meson generates. `include/` holds the same trees for bindgen. The build checks that the three slices' headers and `include/` are identical byte for byte.

`notices/` holds the license texts of libplacebo (LGPL-2.1), glslang and SPIRV-Cross. libplacebo takes SPIRV-Cross under MIT, and the Khronos headers it compiles carry the notice in `LicenseRef-KhronosFreeUse.txt`.

`artifact.json` follows the delivery contract the FFmpeg fork shares:

| Field | Content |
| --- | --- |
| `libraries` | `["libplacebo", "libglslang"]` |
| `repository` | `https://github.com/keraolabs/libplacebo` |
| `tag` | the release tag, or `git describe --tags --always` in dev mode |
| `commit`, `mode`, `dirty`, `built_at` | the checkout's commit, `release` or `dev`, whether the worktree had changes, UTC build time |
| `xcode_build`, `sdks` | Xcode's build version, and each of the three slices' SDK name and version |
| `tools` | meson, ninja, CMake and pkg-config versions |
| `deps` | glslang and SPIRV-Cross: name, repository, tag, commit |
| `inputs` | the FFmpeg output's repository, tag, commit, mode and binaries with their SHA-256, for every framework and each of the three slices |
| `frameworks` | each framework's slices: identifier, path of the binary relative to `<dir>`, SHA-256 |
| `configure` | each slice's meson arguments |
| `unverified` | checks the build skipped; always empty |

### Build

Each slice builds the [dependencies](#dependencies), then configures libplacebo with meson:

```text
--auto-features=disabled -Dmetal=enabled -Dglslang=enabled -Ddovi=enabled -Dlibdovi=disabled -Ddemos=false
```

and a machine file the script writes: a native file for `macos-arm64`, a cross file for each iOS slice, whose `[host_machine]` is `darwin` on `aarch64` with subsystem `ios` or `ios-simulator`. Its `c`, `cpp` and `objc` compilers are `xcrun --sdk <sdk> clang` (`iphoneos`, `iphonesimulator` or `macosx`) with the slice's `-target` and `--sysroot` set to the SDK `xcrun` reports, its compile arguments find FFmpeg's `include/` and the `libavutil` and `libavcodec` frameworks of the slice, and its `pkg_config_libdir` holds only the dependencies, so meson never finds a package of the host.

After packaging, the build checks each framework binary: platform and minimum OS of `LC_BUILD_VERSION`, arm64 only, install name, the single `LC_RPATH`, and that every `LC_LOAD_DYLIB` is a delivered framework, an FFmpeg framework or a system library. It checks that libplacebo exports only symbols its public headers declare `PL_API`, all 35 `PL_LIBAV_API` functions of `utils/libav.h` among them. Upstream's headers for D3D11 and Vulkan declare a few symbols that a build without those backends does not export; that is not a failure.

Last, the build compiles `kerao/consumer.c` against the delivered `macos-arm64` and `ios-arm64-simulator` slices and the FFmpeg frameworks of the same slice, with one `-rpath` per framework directory. The macOS program runs directly, the Simulator program in the booted Simulator through `xcrun simctl spawn`, both without any `DYLD_*` variable reaching them. The program prints `pl_version()` and the plane count `pl_plane_data_from_pixfmt()` gives for NV12, which must match the mode's version and 2. It creates no `pl_gpu`: the Metal backend compiles and links for the Simulator, but is not meant to run there.

### Test

`kerao/test.py` builds the `macos-arm64` and `ios-arm64` slices the same way with `-Dtests=true` added. It links the macOS test programs with an `LC_RPATH` per FFmpeg framework directory and runs `meson test` on this Mac. Its summary has one line per test, then one per Metal configuration.

For `ios-arm64` it checks the Metal test program, `src/tests/test.metal.c`: built for iOS 17.0, arm64 only, and loading nothing but system libraries, `@rpath/libglslang.dylib` and FFmpeg frameworks. It links libplacebo's objects statically, so an app running it embeds `libglslang.dylib` and the FFmpeg frameworks `libavutil`, `libavcodec` and `libswresample`, but no `libplacebo.framework`. The script does not run it.

The Metal test, `src/tests/metal.c`, runs with `MTL_DEBUG_LAYER=1` and creates two `pl_gpu`s on the same device in one run: one with the device's own capabilities and one capped to Apple5. It prints `kerao-metal-test config <name> Apple<family>` after every test of a configuration has passed, and `kerao-metal-test end <count>` as its last line. A configuration passes only when its line is there and the end line follows with the right count. An FFmpeg output of either mode is accepted.

### Self-test

`--self-test` runs each check against counterexamples without building anything. Every counterexample gives a check an input it must pass and a broken copy it must fail, such as a framework with a second `LC_RPATH`, an altered install name, a library missing a `PL_LIBAV_API` export, an `Info.plist` with another version, or a `config.h` that differs in one slice. It needs Xcode, CMake and ninja, but no network or Simulator, and takes a few seconds.

A check is a `check_*` function in `kerao/`, and the self-test also fails when no counterexample calls one. A new check therefore comes with a counterexample registered through `@counterexample`.

### Dependencies

Each build clones the pinned tags into a temporary directory and stops if a checkout's commit differs from its pin in `kerao/deps.py`. Both projects' tags are lightweight, so the commit is the pin.

| Library | Repository | Tag | Commit |
| --- | --- | --- | --- |
| glslang | `https://github.com/KhronosGroup/glslang.git` | `16.6.0` | `e1b562a8bed273a02f30b59b66a5d499793cede5` |
| SPIRV-Cross | `https://github.com/KhronosGroup/SPIRV-Cross.git` | `vulkan-sdk-1.4.363.0` | `f11ba9f0b21ba8fc15153d50a2a1ae31ab1cf8f7` |

glslang 16.6.0 is glslang's newest release, and it is the glslang of Vulkan SDK 1.4.363.0. SPIRV-Cross has no releases of its own: Khronos tags the revision it ships in each Vulkan SDK, and `vulkan-sdk-1.4.363.0` is that revision for the same SDK, so the two were released and tested together.

Both are built with CMake and Ninja in Release for each slice, with `CMAKE_SYSTEM_NAME` `iOS` for the two iOS slices and `Darwin` for macOS, and the slice's `-target` in the compiler flags.

#### glslang

glslang is built static with `ENABLE_OPT=0`, so without SPIRV-Tools, with `ENABLE_HLSL=OFF`, and without its tools, tests and `External` sources. Its three static libraries `glslang`, `SPIRV` and `glslang-default-resource-limits` are linked with `-force_load` into one `libglslang.dylib`, whose install name is `@rpath/libglslang.dylib` and which is linked with `-Wl,-headerpad_max_install_names`. `glslang.pc` names that dylib as the only library, and libplacebo's glsl meson configuration takes glslang only as this pkg-config package.

glslang compiles in `SPIRV/hex_float.h` and `SPIRV/bitutils.h`, which are under Apache-2.0 alone. Apache-2.0 code must not go into the LGPL libplacebo binary, so glslang stays outside it as a library of its own and ships as its own framework. glslang's Bison output, `glslang/MachineIndependent/glslang_tab.cpp`, is under GPL-3.0-or-later with the Bison exception 2.2, which allows it to be distributed under any terms.

#### SPIRV-Cross

SPIRV-Cross is built as the static libraries the C API and MSL output use: `spirv-cross-core`, `spirv-cross-glsl`, `spirv-cross-msl` and `spirv-cross-c`, with HLSL, C++, reflection, util, the CLI and tests off. libplacebo links them statically under MIT.

`spirv-cross-c.pc` is the script's own: it names the four libraries in link order and `include/spirv_cross` as the header directory, and the Metal backend takes SPIRV-Cross only as this pkg-config package. Upstream's static `spirv-cross-c.pc` names only `spirv-cross-c`. The build checks that pkg-config, finding packages only in `lib/pkgconfig`, gives `-l` and `-L` flags reaching all four archives and an `-I` directory holding `spirv_cross_c.h`.

SPIRV-Cross is compiled with `-fvisibility=hidden -fvisibility-inlines-hidden`, so libplacebo's dylib exports none of its symbols. Apple's linker has no `--exclude-libs`, and an object compiled with default visibility keeps its symbols exported from any dylib it is linked into. The build links every object of the four archives into a probe dylib and fails if it exports any symbol.

The build passes `-UHAVE_SPIRV_CROSS_GIT_VERSION`, so the C API does not compile the generated `gitversion.h`, whose template `cmake/gitversion.in.h` is under Apache-2.0 alone. `spvc_get_commit_revision_and_timestamp()` returns an empty string instead.

After the build, every file ninja recorded as an input of a SPIRV-Cross object, except headers of the SDK and of the compiler, must offer MIT. Its `SPDX-License-Identifier:` and `License:` lines must each name `MIT` as an alternative, or, if it has no such line, it must carry the MIT permission notice. The check fails otherwise and names each file. At the pinned revision these files are SPIRV-Cross's own sources (`Apache-2.0 OR MIT`), `GLSL.std.450.h` (`MIT`), `spirv.h` and `spirv.hpp` (`License: MIT`), and `NonSemanticShaderDebugInfo100.h`, which carries Khronos' MIT notice (`MIT-Khronos-old`) without a tag line. SPIRV-Cross's `.reuse/dep5` lists the four Khronos headers as `LicenseRef-KhronosFreeUse`, which is that same notice; the check reads the files themselves.
