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

#pragma once

#import <Metal/Metal.h>

#include "../gpu.h"
#include "../pl_thread.h"

#include <libplacebo/metal.h>

// Lowest supported Apple GPU family: A12, the GPU floor of iOS 17
#define METAL_MIN_APPLE_FAMILY 5

// Device capabilities, capped by `pl_metal_params.max_apple_family`
struct metal_caps {
    int apple_family;       // highest supported MTLGPUFamilyApple<n>
    bool float32_filtering; // MTLDevice.supports32BitFloatFiltering
};

// Returns NULL if the device does not support METAL_MIN_APPLE_FAMILY, or
// `max_apple_family` is nonzero and below it.
pl_gpu pl_gpu_create_metal(pl_log log, pl_metal mtl, id<MTLDevice> device,
                           id<MTLCommandQueue> queue, int max_apple_family);

// --- Command model
//
// Command buffers are numbered by `seq` in commit order, starting at 1. A
// resource is in use while its `last_use` is above `completed_seq`.

// Runs on a thread using the `pl_gpu` once its command buffer is retired,
// never concurrently with another callback.
struct metal_callback {
    void (*run)(void *priv);
    void *priv;
};

// Storage owned by one command buffer for data copied at encode time, so
// that the caller may change its copy once the encode returns. Released when
// the command buffer is retired.
struct metal_arena {
    NSMutableArray<id<MTLBuffer>> *chunks; // Shared storage, the last one filling
    size_t used;                           // bytes used in the last chunk
};

struct metal_cmd {
    id<MTLCommandBuffer> buf; // retains the resources encoded into it
    uint64_t seq;
    int num_encodes;
    struct metal_arena arena;
    PL_ARRAY(struct metal_callback) callbacks;
    bool done; // under `done_lock`
};

struct pl_mtl {
    struct pl_gpu_fns impl;
    pl_metal mtl;
    id<MTLDevice> device;
    id<MTLCommandQueue> queue;
    struct metal_caps caps;

    // Recursive, taken before `lock`. Held to retire command buffers and run
    // their callbacks, which may call back into the `pl_gpu`.
    pl_mutex callback_lock;

    // Held to record into `cur`, to commit and to write a resource's
    // `last_use`; never held while waiting for the GPU or running callbacks.
    pl_mutex lock;
    struct metal_cmd *cur;  // being recorded, or NULL
    uint64_t next_seq;      // `seq` of the next command buffer
    uint64_t submitted_seq; // `seq` of the last committed command buffer

    // The only lock taken by command buffer completion handlers. Taken after
    // `lock`.
    pl_mutex done_lock;
    pl_cond done_cond;                      // broadcast as `completed_seq` grows
    PL_ARRAY(struct metal_cmd *) submitted; // committed, not retired, by `seq`
    PL_ARRAY(struct metal_cmd *) retired;   // completed, callbacks pending
    _Atomic uint64_t completed_seq;         // all `seq` up to this completed
    _Atomic bool failed;
};

// `last_use` is the `seq` of the last command buffer that encoded work on the
// resource, or 0 if there was none. Written under `lock`.
struct pl_tex_mtl {
    id<MTLTexture> tex;
    _Atomic uint64_t last_use;
};

struct pl_buf_mtl {
    id<MTLBuffer> buf;
    _Atomic uint64_t last_use;
};
