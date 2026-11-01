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

#include "formats.h"

// Highest Apple GPU family named by the SDK
#define METAL_MAX_APPLE_FAMILY 11

// Metal feature set tables
#define METAL_FLOAT32_FILTER_FAMILY 9    // first family filtering 32-bit floats
#define METAL_MAX_TEX_DIM           16384
#define METAL_MAX_TEX_DIM_FAMILY    10   // first family with the larger limit
#define METAL_MAX_TEX_DIM_LARGE     32768
#define METAL_MAX_TEX_3D_DIM        2048
#define METAL_VERTEX_STRIDE_ALIGN   1

// Metal Shading Language specification
#define METAL_MIN_GATHER_OFFSET     (-8)
#define METAL_MAX_GATHER_OFFSET     7

#define METAL_GLSL_VERSION          450

static int apple_family(id<MTLDevice> device)
{
    int family = 0;
    for (int n = METAL_MIN_APPLE_FAMILY; n <= METAL_MAX_APPLE_FAMILY; n++) {
        if (![device supportsFamily:(MTLGPUFamily) (MTLGPUFamilyApple1 + n - 1)])
            break;
        family = n;
    }

    return family;
}

static struct metal_caps query_caps(id<MTLDevice> device, int max_apple_family)
{
    int family = apple_family(device);
    bool float32_filtering = device.supports32BitFloatFiltering;
    if (max_apple_family) {
        family = PL_MIN(family, max_apple_family);
        float32_filtering &= max_apple_family >= METAL_FLOAT32_FILTER_FAMILY;
    }

    return (struct metal_caps) {
        .apple_family = family,
        .float32_filtering = float32_filtering,
    };
}

static struct pl_glsl_version metal_glsl(void)
{
    return (struct pl_glsl_version) {
        .version = METAL_GLSL_VERSION,
        .vulkan = true,
        .min_gather_offset = METAL_MIN_GATHER_OFFSET,
        .max_gather_offset = METAL_MAX_GATHER_OFFSET,
    };
}

static struct pl_gpu_limits metal_limits(id<MTLDevice> device, struct metal_caps caps)
{
    uint32_t max_tex_dim = caps.apple_family >= METAL_MAX_TEX_DIM_FAMILY
                         ? METAL_MAX_TEX_DIM_LARGE : METAL_MAX_TEX_DIM;
    size_t max_buf = device.maxBufferLength;
    size_t align = metal_transfer_alignment(device);
    bool unified = device.hasUnifiedMemory;

    return (struct pl_gpu_limits) {
        .thread_safe         = true,
        .callbacks           = true,
        .max_buf_size        = max_buf,
        .max_ubo_size        = max_buf,
        .max_vbo_size        = max_buf,
        .max_mapped_size     = max_buf,
        .max_mapped_vram     = unified ? max_buf : 0,
        .host_cached         = unified,
        .max_tex_1d_dim      = max_tex_dim,
        .max_tex_2d_dim      = max_tex_dim,
        .max_tex_3d_dim      = METAL_MAX_TEX_3D_DIM,
        .buf_transfer        = true,
        .align_tex_xfer_pitch  = align,
        .align_tex_xfer_offset = align,
        .max_pushc_size      = METAL_MAX_SET_BYTES,
        .align_vertex_stride = METAL_VERTEX_STRIDE_ALIGN,
        .fragment_queues     = 1,
    };
}

static void metal_gpu_destroy(pl_gpu gpu)
{
    @autoreleasepool {
        struct pl_mtl *p = PL_PRIV(gpu);
        metal_gpu_finish(gpu);
        pl_assert(!p->cur && !p->submitted.num && !p->retired.num);
        pl_free(p->submitted.elem);
        pl_free(p->retired.elem);
        metal_pass_uninit(gpu);

        pl_cond_destroy(&p->done_cond);
        pl_mutex_destroy(&p->done_lock);
        pl_mutex_destroy(&p->lock);
        pl_mutex_destroy(&p->callback_lock);
        p->queue = nil;
        p->device = nil;
        pl_free((void *) gpu);
    }
}

static bool metal_gpu_is_failed(pl_gpu gpu)
{
    @autoreleasepool {
        struct pl_mtl *p = PL_PRIV(gpu);
        return atomic_load(&p->failed);
    }
}

static int metal_desc_namespace(pl_gpu gpu, enum pl_desc_type type)
{
    @autoreleasepool {
        return 0;
    }
}

static pl_timer metal_timer_create(pl_gpu gpu)
{
    @autoreleasepool {
        return NULL;
    }
}

static void metal_timer_destroy(pl_gpu gpu, pl_timer timer)
{
    @autoreleasepool {
        pl_unreachable();
    }
}

static uint64_t metal_timer_query(pl_gpu gpu, pl_timer timer)
{
    @autoreleasepool {
        pl_unreachable();
    }
}

static const struct pl_gpu_fns metal_fns = {
    .destroy        = metal_gpu_destroy,
    .tex_destroy    = metal_tex_destroy,
    .buf_destroy    = metal_buf_destroy,
    .pass_destroy   = metal_pass_destroy,
    .timer_destroy  = metal_timer_destroy,
    .tex_create     = metal_tex_create,
    .tex_clear_ex   = metal_tex_clear_ex,
    .tex_blit       = metal_tex_blit,
    .tex_upload     = metal_tex_upload,
    .tex_download   = metal_tex_download,
    .tex_poll       = metal_tex_poll,
    .buf_create     = metal_buf_create,
    .buf_write      = metal_buf_write,
    .buf_read       = metal_buf_read,
    .buf_copy       = metal_buf_copy,
    .buf_poll       = metal_buf_poll,
    .desc_namespace = metal_desc_namespace,
    .pass_create    = metal_pass_create,
    .pass_run       = metal_pass_run,
    .timer_create   = metal_timer_create,
    .timer_query    = metal_timer_query,
    .gpu_flush      = metal_gpu_flush,
    .gpu_finish     = metal_gpu_finish,
    .gpu_is_failed  = metal_gpu_is_failed,
};

pl_gpu pl_gpu_create_metal(pl_log log, pl_metal mtl, id<MTLDevice> device,
                           id<MTLCommandQueue> queue, int max_apple_family)
{
    struct metal_caps caps = query_caps(device, max_apple_family);
    if (caps.apple_family < METAL_MIN_APPLE_FAMILY) {
        pl_fatal(log, "Metal device '%s' does not support MTLGPUFamilyApple%d",
                 device.name.UTF8String, METAL_MIN_APPLE_FAMILY);
        return NULL;
    }

    struct pl_gpu_t *gpu = pl_zalloc_obj(NULL, gpu, struct pl_mtl);
    struct pl_mtl *p = PL_PRIV(gpu);
    if (pl_cond_init(&p->done_cond) != 0) {
        pl_fatal(log, "Failed initializing a condition variable");
        pl_free(gpu);
        return NULL;
    }

    pl_mutex_init_type(&p->callback_lock, PL_MUTEX_RECURSIVE);
    pl_mutex_init(&p->lock);
    pl_mutex_init(&p->done_lock);
    p->impl = metal_fns;
    p->mtl = mtl;
    p->device = device;
    p->queue = queue;
    p->caps = caps;
    p->next_seq = 1;

    gpu->log = log;
    gpu->glsl = metal_glsl();
    gpu->limits = metal_limits(device, caps);
    gpu->import_caps.tex = PL_HANDLE_IOSURFACE;
    metal_setup_formats(gpu);
    if (!metal_pass_init(gpu)) {
        metal_gpu_destroy(gpu);
        return NULL;
    }

    PL_INFO(gpu, "Metal device: %s, MTLGPUFamilyApple%d%s", device.name.UTF8String,
            caps.apple_family, max_apple_family ? " (capped)" : "");
    return pl_gpu_finalize(gpu);
}

pl_metal pl_metal_get(pl_gpu gpu)
{
    @autoreleasepool {
        const struct pl_gpu_fns *impl = PL_PRIV(gpu);
        if (impl->destroy != metal_gpu_destroy)
            return NULL;

        const struct pl_mtl *p = PL_PRIV(gpu);
        return p->mtl;
    }
}
