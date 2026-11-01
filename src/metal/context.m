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

#include "gpu.h"

const struct pl_metal_params pl_metal_default_params = {0};

static id<MTLDevice> choose_device(pl_log log, const struct pl_metal_params *params,
                                   id<MTLCommandQueue> queue)
{
    id<MTLDevice> device = (__bridge id<MTLDevice>) params->device;
    if (queue && device && queue.device != device) {
        pl_fatal(log, "Metal command queue does not belong to the given device");
        return nil;
    }

    if (!device)
        device = queue ? queue.device : MTLCreateSystemDefaultDevice();
    if (!device)
        pl_fatal(log, "No Metal device available");
    return device;
}

pl_metal pl_metal_create(pl_log log, const struct pl_metal_params *params)
{
    @autoreleasepool {
        params = PL_DEF(params, &pl_metal_default_params);
        if (params->max_apple_family && params->max_apple_family < METAL_MIN_APPLE_FAMILY) {
            pl_fatal(log, "max_apple_family %d is below the lowest supported "
                     "family %d", params->max_apple_family, METAL_MIN_APPLE_FAMILY);
            return NULL;
        }

        id<MTLCommandQueue> queue = (__bridge id<MTLCommandQueue>) params->queue;
        id<MTLDevice> device = choose_device(log, params, queue);
        if (!device)
            return NULL;

        if (!queue)
            queue = [device newCommandQueue];
        if (!queue) {
            pl_fatal(log, "Failed creating a Metal command queue");
            return NULL;
        }

        struct pl_metal_t *mtl = pl_zalloc_ptr(NULL, mtl);
        mtl->device = (__bridge void *) device;
        mtl->queue = (__bridge void *) queue;
        mtl->gpu = pl_gpu_create_metal(log, mtl, device, queue, params->max_apple_family);
        if (!mtl->gpu) {
            pl_free(mtl);
            return NULL;
        }

        return mtl;
    }
}

void pl_metal_destroy(pl_metal *pmtl)
{
    @autoreleasepool {
        pl_metal mtl = *pmtl;
        if (!mtl)
            return;

        pl_gpu_destroy(mtl->gpu);
        pl_free((void *) mtl);
        *pmtl = NULL;
    }
}
