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
#include "../glsl/spirv.h"
#include "../pl_thread.h"
#include "msl.h"

#include <libplacebo/metal.h>

// Lowest supported Apple GPU family: A12, the GPU floor of iOS 17
#define METAL_MIN_APPLE_FAMILY 5

// Largest size of data set with `setVertexBytes` and `setFragmentBytes`
#define METAL_MAX_SET_BYTES 4096

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

// Operations recorded into one command buffer before it is committed
#define METAL_MAX_CMD_ENCODES 32

// Runs on a thread using the `pl_gpu` once its command buffer is retired,
// never concurrently with another callback.
struct metal_callback {
    void (*run)(void *priv);
    void *priv;
};

// Storage owned by one command buffer for data copied at encode time, so
// that the caller may change its copy once the encode returns. The command
// buffer retains every chunk it encodes, until it is retired.
struct metal_arena {
    id<MTLBuffer> chunk; // Shared storage, being filled
    size_t used;         // bytes used in `chunk`
};

struct metal_cmd {
    id<MTLCommandBuffer> buf; // retains the resources encoded into it
    uint64_t seq;
    int num_encodes;
    struct metal_arena arena;
    NSMutableArray *objects; // other objects encoded, kept until retired
    PL_ARRAY(struct metal_callback) callbacks;
    bool done; // under `done_lock`
};

// A range of a buffer
struct metal_slice {
    id<MTLBuffer> buf;
    size_t offset;
};

struct pl_mtl {
    struct pl_gpu_fns impl;
    pl_metal mtl;
    id<MTLDevice> device;
    id<MTLCommandQueue> queue;
    struct metal_caps caps;
    pl_spirv spirv;
    struct metal_msl_target msl_target;
    id<MTLSamplerState> samplers[PL_TEX_SAMPLE_MODE_COUNT][PL_TEX_ADDRESS_MODE_COUNT];

    // Recursive, taken before `lock`. Held to retire command buffers and run
    // their callbacks, which may call back into the `pl_gpu`.
    pl_mutex callback_lock;
    bool retiring; // under `callback_lock`

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

// Returns false, logging an error, once a command buffer of `gpu` failed.
bool metal_gpu_usable(pl_gpu gpu);

// Returns the command buffer being recorded with `lock` held, or NULL on
// failure. Encode one operation into `buf`, mark every resource it touches
// with `metal_cmd_use`, then call `metal_cmd_end`.
struct metal_cmd *metal_cmd_begin(pl_gpu gpu);
void metal_cmd_use(struct metal_cmd *cmd, _Atomic uint64_t *last_use);
void metal_cmd_callback(struct metal_cmd *cmd, void (*run)(void *priv), void *priv);
void metal_cmd_retain(struct metal_cmd *cmd, id obj);
void metal_cmd_end(pl_gpu gpu, struct metal_cmd *cmd);

// Copies `size` bytes of `data` into the arena of `cmd`. The slice's offset
// is a multiple of METAL_ARENA_ALIGN, and its buffer extends at least to the
// next multiple past the copy. Returns a slice with a nil buffer, logging an
// error, on failure.
#define METAL_ARENA_ALIGN 256
struct metal_slice metal_cmd_copy(pl_gpu gpu, struct metal_cmd *cmd,
                                  const void *data, size_t size);

// Returns whether a resource with this `last_use` is still in use after
// waiting up to `timeout` nanoseconds (UINT64_MAX: until it is not).
bool metal_poll(pl_gpu gpu, _Atomic uint64_t *last_use, uint64_t timeout);

// Returns a new buffer holding a copy of `data`, or of undefined contents if
// `data` is NULL. Logs an error and returns nil on failure.
id<MTLBuffer> metal_new_buffer(pl_gpu gpu, const void *data, size_t size,
                               MTLResourceOptions options);
void metal_encode_buf_copy(struct metal_cmd *cmd, id<MTLBuffer> dst, size_t dst_offset,
                           id<MTLBuffer> src, size_t src_offset, size_t size);

void metal_gpu_flush(pl_gpu gpu);
void metal_gpu_finish(pl_gpu gpu);

pl_buf metal_buf_create(pl_gpu gpu, const struct pl_buf_params *params);
void metal_buf_destroy(pl_gpu gpu, pl_buf buf);
void metal_buf_write(pl_gpu gpu, pl_buf buf, size_t offset, const void *data,
                     size_t size);
bool metal_buf_read(pl_gpu gpu, pl_buf buf, size_t offset, void *dest, size_t size);
void metal_buf_copy(pl_gpu gpu, pl_buf dst, size_t dst_offset, pl_buf src,
                    size_t src_offset, size_t size);
bool metal_buf_poll(pl_gpu gpu, pl_buf buf, uint64_t timeout);

pl_tex metal_tex_create(pl_gpu gpu, const struct pl_tex_params *params);
void metal_tex_destroy(pl_gpu gpu, pl_tex tex);
void metal_tex_clear_ex(pl_gpu gpu, pl_tex tex, const union pl_clear_color color);
void metal_tex_blit(pl_gpu gpu, const struct pl_tex_blit_params *params);
bool metal_tex_upload(pl_gpu gpu, const struct pl_tex_transfer_params *params);
bool metal_tex_download(pl_gpu gpu, const struct pl_tex_transfer_params *params);
bool metal_tex_poll(pl_gpu gpu, pl_tex tex, uint64_t timeout);

// Creates the shader compiler and sampler states, or logs an error and
// returns false. `metal_pass_uninit` is safe after a failure.
bool metal_pass_init(pl_gpu gpu);
void metal_pass_uninit(pl_gpu gpu);

pl_pass metal_pass_create(pl_gpu gpu, const struct pl_pass_params *params);
void metal_pass_destroy(pl_gpu gpu, pl_pass pass);
void metal_pass_run(pl_gpu gpu, const struct pl_pass_run_params *params);
