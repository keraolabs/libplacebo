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

#include "gpu.h"

// Texture capabilities of a pixel format in the Metal feature set tables,
// shared by every Apple GPU family from METAL_MIN_APPLE_FAMILY on
enum metal_format_features {
    METAL_FMT_FILTER   = 1 << 0,
    METAL_FMT_FILTER32 = 1 << 1, // Filter where 32-bit float filtering is supported
    METAL_FMT_COLOR    = 1 << 2,
    METAL_FMT_BLEND    = 1 << 3,
};

struct metal_format {
    MTLPixelFormat pixel;
    MTLVertexFormat vertex; // MTLVertexFormatInvalid if not a vertex format
    enum metal_format_features features;
    struct pl_fmt_t fmt;
};

static inline const struct metal_format *metal_format(pl_fmt fmt)
{
    return *(const struct metal_format **) PL_PRIV(fmt);
}

void metal_setup_formats(struct pl_gpu_t *gpu);

// Alignment of texture transfer offsets and pitches suiting every pixel
// format: a power of two, and a multiple of every pixel size
size_t metal_transfer_alignment(id<MTLDevice> device);
