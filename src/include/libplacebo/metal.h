/*
 * This file is part of libplacebo.
 *
 * libplacebo is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * libplacebo is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with libplacebo. If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef LIBPLACEBO_METAL_H_
#define LIBPLACEBO_METAL_H_

#include <libplacebo/gpu.h>

PL_API_BEGIN

// Structure representing a Metal device and the `pl_gpu` rendering with it.
typedef const struct pl_metal_t {
    pl_gpu gpu;

    // The `id<MTLDevice>` and `id<MTLCommandQueue>` in use. The `pl_metal`
    // holds a strong reference to both until `pl_metal_destroy`; retain them
    // to use them beyond that.
    void *device;
    void *queue;
} *pl_metal;

struct pl_metal_params {
    // The `id<MTLDevice>` to use. Optional: if NULL, the queue's device is
    // used, or `MTLCreateSystemDefaultDevice()` if `queue` is NULL too.
    void *device;

    // The `id<MTLCommandQueue>` to submit to. It must belong to `device`.
    // Optional: if NULL, a new queue is created on the device.
    void *queue;

    // For testing only: caps the reported capabilities at those of the Apple
    // GPU family with this number, e.g. 5 for `MTLGPUFamilyApple5`. 0 means
    // no cap. Families below 5 are not supported.
    int max_apple_family;
};

#define pl_metal_params(...) (&(struct pl_metal_params) { __VA_ARGS__ })
PL_API extern const struct pl_metal_params pl_metal_default_params;

// Creates a Metal device and its `pl_gpu`. Returns NULL on failure.
PL_API pl_metal pl_metal_create(pl_log log, const struct pl_metal_params *params);

// Destroys a `pl_metal`. All resources allocated from its `pl_gpu` must have
// been destroyed first.
PL_API void pl_metal_destroy(pl_metal *mtl);

// For a `pl_gpu` backed by `pl_metal`, returns the `pl_metal`. Returns NULL
// for any other type of `gpu`.
PL_API pl_metal pl_metal_get(pl_gpu gpu);

// IOSurface import: `pl_gpu.import_caps.tex` is PL_HANDLE_IOSURFACE. To
// import one plane of an IOSurface with `pl_tex_create`, set `import_handle`
// to PL_HANDLE_IOSURFACE, `shared_mem.handle.handle` to the `IOSurfaceRef`
// and `shared_mem.plane` to the plane. The texture must be 2D, and its
// format must not be `emulated`. The plane's width and height must equal `w`
// and `h`, its bytes per element must equal the format's `texel_size`, and
// its bytes per row must be at least `w * texel_size` and a multiple of the
// device's `minimumLinearTextureAlignmentForPixelFormat:`. Otherwise
// `pl_tex_create` logs the mismatch and returns NULL. Whether the surface's
// pixel format matches `format` is up to the caller.
//
// The texture retains the IOSurface, so the caller may release its own
// reference after `pl_tex_create`, and `pl_tex_destroy` is safe while the GPU
// still uses the texture. That retain does not stop a pool from reusing the
// surface and overwriting its contents, and GPU access does not count towards
// the IOSurface's use count: the caller must keep the owner of the image
// contents (e.g. the `CVPixelBuffer` or the `AVFrame`) until `pl_tex_poll`
// reports the texture idle.

PL_API_END

#endif // LIBPLACEBO_METAL_H_
