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

#define FMT(_name, num, size, ftype, bits, idx) \
    (struct pl_fmt_t) {                         \
        .name = _name,                          \
        .type = PL_FMT_##ftype,                 \
        .num_components  = num,                 \
        .component_depth = bits,                \
        .internal_size   = size,                \
        .opaque          = false,               \
        .texel_size      = size,                \
        .texel_align     = size,                \
        .host_bits       = bits,                \
        .sample_order    = idx,                 \
    }

#define IDX(...)  {__VA_ARGS__}
#define BITS(...) {__VA_ARGS__}

#define REGFMT(name, num, bits, type)           \
    FMT(name, num, (num) * (bits) / 8, type,    \
        BITS(bits, bits, bits, bits),           \
        IDX(0, 1, 2, 3))

#define ALL  (METAL_FMT_FILTER | METAL_FMT_COLOR | METAL_FMT_BLEND)
#define ALL32 (METAL_FMT_FILTER32 | METAL_FMT_COLOR | METAL_FMT_BLEND)
#define INT  METAL_FMT_COLOR

#define V(name) MTLVertexFormat##name
#define NOV MTLVertexFormatInvalid
#define NOP MTLPixelFormatInvalid

static const struct metal_format metal_formats[] = {
    {MTLPixelFormatR8Unorm,     V(UCharNormalized),   ALL,  REGFMT("r8",       1,  8, UNORM)},
    {MTLPixelFormatRG8Unorm,    V(UChar2Normalized),  ALL,  REGFMT("rg8",      2,  8, UNORM)},
    {MTLPixelFormatRGBA8Unorm,  V(UChar4Normalized),  ALL,  REGFMT("rgba8",    4,  8, UNORM)},
    {MTLPixelFormatR16Unorm,    V(UShortNormalized),  ALL,  REGFMT("r16",      1, 16, UNORM)},
    {MTLPixelFormatRG16Unorm,   V(UShort2Normalized), ALL,  REGFMT("rg16",     2, 16, UNORM)},
    {MTLPixelFormatRGBA16Unorm, V(UShort4Normalized), ALL,  REGFMT("rgba16",   4, 16, UNORM)},

    {MTLPixelFormatR8Snorm,     V(CharNormalized),    ALL,  REGFMT("r8s",      1,  8, SNORM)},
    {MTLPixelFormatRG8Snorm,    V(Char2Normalized),   ALL,  REGFMT("rg8s",     2,  8, SNORM)},
    {MTLPixelFormatRGBA8Snorm,  V(Char4Normalized),   ALL,  REGFMT("rgba8s",   4,  8, SNORM)},
    {MTLPixelFormatR16Snorm,    V(ShortNormalized),   ALL,  REGFMT("r16s",     1, 16, SNORM)},
    {MTLPixelFormatRG16Snorm,   V(Short2Normalized),  ALL,  REGFMT("rg16s",    2, 16, SNORM)},
    {MTLPixelFormatRGBA16Snorm, V(Short4Normalized),  ALL,  REGFMT("rgba16s",  4, 16, SNORM)},

    {MTLPixelFormatR16Float,    V(Half),              ALL,  REGFMT("r16hf",    1, 16, FLOAT)},
    {MTLPixelFormatRG16Float,   V(Half2),             ALL,  REGFMT("rg16hf",   2, 16, FLOAT)},
    {MTLPixelFormatRGBA16Float, V(Half4),             ALL,  REGFMT("rgba16hf", 4, 16, FLOAT)},
    {MTLPixelFormatR32Float,    V(Float),             ALL32, REGFMT("r32f",    1, 32, FLOAT)},
    {MTLPixelFormatRG32Float,   V(Float2),            ALL32, REGFMT("rg32f",   2, 32, FLOAT)},
    {MTLPixelFormatRGBA32Float, V(Float4),            ALL32, REGFMT("rgba32f", 4, 32, FLOAT)},

    {MTLPixelFormatR8Uint,      V(UChar),             INT,  REGFMT("r8u",      1,  8, UINT)},
    {MTLPixelFormatRG8Uint,     V(UChar2),            INT,  REGFMT("rg8u",     2,  8, UINT)},
    {MTLPixelFormatRGBA8Uint,   V(UChar4),            INT,  REGFMT("rgba8u",   4,  8, UINT)},
    {MTLPixelFormatR16Uint,     V(UShort),            INT,  REGFMT("r16u",     1, 16, UINT)},
    {MTLPixelFormatRG16Uint,    V(UShort2),           INT,  REGFMT("rg16u",    2, 16, UINT)},
    {MTLPixelFormatRGBA16Uint,  V(UShort4),           INT,  REGFMT("rgba16u",  4, 16, UINT)},
    {MTLPixelFormatR32Uint,     V(UInt),              INT,  REGFMT("r32u",     1, 32, UINT)},
    {MTLPixelFormatRG32Uint,    V(UInt2),             INT,  REGFMT("rg32u",    2, 32, UINT)},
    {MTLPixelFormatRGBA32Uint,  V(UInt4),             INT,  REGFMT("rgba32u",  4, 32, UINT)},

    {MTLPixelFormatR8Sint,      V(Char),              INT,  REGFMT("r8i",      1,  8, SINT)},
    {MTLPixelFormatRG8Sint,     V(Char2),             INT,  REGFMT("rg8i",     2,  8, SINT)},
    {MTLPixelFormatRGBA8Sint,   V(Char4),             INT,  REGFMT("rgba8i",   4,  8, SINT)},
    {MTLPixelFormatR16Sint,     V(Short),             INT,  REGFMT("r16i",     1, 16, SINT)},
    {MTLPixelFormatRG16Sint,    V(Short2),            INT,  REGFMT("rg16i",    2, 16, SINT)},
    {MTLPixelFormatRGBA16Sint,  V(Short4),            INT,  REGFMT("rgba16i",  4, 16, SINT)},
    {MTLPixelFormatR32Sint,     V(Int),               INT,  REGFMT("r32i",     1, 32, SINT)},
    {MTLPixelFormatRG32Sint,    V(Int2),              INT,  REGFMT("rg32i",    2, 32, SINT)},
    {MTLPixelFormatRGBA32Sint,  V(Int4),              INT,  REGFMT("rgba32i",  4, 32, SINT)},

    // Packed formats, components named from the least significant bit
    {MTLPixelFormatBGRA8Unorm,   V(UChar4Normalized_BGRA), ALL,
        FMT("bgra8",    4, 4, UNORM, BITS(8,  8,  8,  8), IDX(2, 1, 0, 3))},
    {MTLPixelFormatRGB10A2Unorm, V(UInt1010102Normalized), ALL,
        FMT("rgb10a2",  4, 4, UNORM, BITS(10, 10, 10, 2), IDX(0, 1, 2, 3))},
    {MTLPixelFormatBGR10A2Unorm, NOV, ALL,
        FMT("bgr10a2",  4, 4, UNORM, BITS(10, 10, 10, 2), IDX(2, 1, 0, 3))},
    {MTLPixelFormatRGB10A2Uint,  NOV, INT,
        FMT("rgb10a2u", 4, 4, UINT,  BITS(10, 10, 10, 2), IDX(0, 1, 2, 3))},
    {MTLPixelFormatB5G6R5Unorm,  NOV, ALL,
        FMT("bgr565",   3, 2, UNORM, BITS(5,  6,  5),     IDX(2, 1, 0))},
    {MTLPixelFormatA1BGR5Unorm,  NOV, ALL,
        FMT("a1bgr5",   4, 2, UNORM, BITS(1,  5,  5,  5), IDX(3, 2, 1, 0))},
    {MTLPixelFormatABGR4Unorm,   NOV, ALL,
        FMT("abgr4",    4, 2, UNORM, BITS(4,  4,  4,  4), IDX(3, 2, 1, 0))},
    {MTLPixelFormatBGR5A1Unorm,  NOV, ALL,
        FMT("bgr5a1",   4, 2, UNORM, BITS(5,  5,  5,  1), IDX(2, 1, 0, 3))},

    // Vertex formats without a pixel format
    {NOP, V(UChar3Normalized),  0, REGFMT("rgb8",    3,  8, UNORM)},
    {NOP, V(Char3Normalized),   0, REGFMT("rgb8s",   3,  8, SNORM)},
    {NOP, V(UChar3),            0, REGFMT("rgb8u",   3,  8, UINT)},
    {NOP, V(Char3),             0, REGFMT("rgb8i",   3,  8, SINT)},
    {NOP, V(UShort3Normalized), 0, REGFMT("rgb16",   3, 16, UNORM)},
    {NOP, V(Short3Normalized),  0, REGFMT("rgb16s",  3, 16, SNORM)},
    {NOP, V(Half3),             0, REGFMT("rgb16hf", 3, 16, FLOAT)},
    {NOP, V(UShort3),           0, REGFMT("rgb16u",  3, 16, UINT)},
    {NOP, V(Short3),            0, REGFMT("rgb16i",  3, 16, SINT)},
    {NOP, V(Float3),            0, REGFMT("rgb32f",  3, 32, FLOAT)},
    {NOP, V(UInt3),             0, REGFMT("rgb32u",  3, 32, UINT)},
    {NOP, V(Int3),              0, REGFMT("rgb32i",  3, 32, SINT)},
};

#undef NOP

#undef NOV
#undef V
#undef INT
#undef ALL32
#undef ALL
#undef REGFMT
#undef BITS
#undef IDX
#undef FMT

static enum pl_fmt_caps format_caps(const struct metal_format *mfmt,
                                    struct metal_caps caps)
{
    enum metal_format_features f = mfmt->features;
    bool integer = mfmt->fmt.type == PL_FMT_UINT || mfmt->fmt.type == PL_FMT_SINT;
    bool filter = (f & METAL_FMT_FILTER) ||
                  ((f & METAL_FMT_FILTER32) && caps.float32_filtering);

    enum pl_fmt_caps ret = 0;
    if (mfmt->pixel != MTLPixelFormatInvalid)
        ret |= PL_FMT_CAP_SAMPLEABLE | PL_FMT_CAP_HOST_READABLE;
    if (filter)
        ret |= PL_FMT_CAP_LINEAR;
    if (f & METAL_FMT_COLOR)
        ret |= PL_FMT_CAP_RENDERABLE;
    if (f & METAL_FMT_BLEND)
        ret |= PL_FMT_CAP_BLENDABLE;
    if ((f & METAL_FMT_COLOR) && !integer)
        ret |= PL_FMT_CAP_BLITTABLE;
    if (mfmt->vertex != MTLVertexFormatInvalid)
        ret |= PL_FMT_CAP_VERTEX;
    return ret;
}

void metal_setup_formats(struct pl_gpu_t *gpu)
{
    const struct pl_mtl *p = PL_PRIV(gpu);
    PL_ARRAY(pl_fmt) formats = {0};

    for (int i = 0; i < PL_ARRAY_SIZE(metal_formats); i++) {
        const struct metal_format *mfmt = &metal_formats[i];
        struct pl_fmt_t *fmt = pl_alloc_obj(gpu, fmt, const struct metal_format *);
        *fmt = mfmt->fmt;
        *(const struct metal_format **) PL_PRIV(fmt) = mfmt;
        for (int c = fmt->num_components; c < 4; c++) {
            fmt->component_depth[c] = 0;
            fmt->sample_order[c] = 0;
            fmt->host_bits[c] = 0;
        }

        fmt->signature = mfmt->pixel;
        fmt->caps = format_caps(mfmt, p->caps);
        fmt->gatherable = fmt->caps & PL_FMT_CAP_SAMPLEABLE;
        fmt->fourcc = pl_fmt_fourcc(fmt);
        if (fmt->caps & PL_FMT_CAP_VERTEX)
            fmt->glsl_type = pl_var_glsl_type_name(pl_var_from_fmt(fmt, ""));

        PL_ARRAY_APPEND(gpu, formats, fmt);
    }

    gpu->formats = formats.elem;
    gpu->num_formats = formats.num;
}

size_t metal_transfer_alignment(id<MTLDevice> device)
{
    size_t align = 1;
    for (int i = 0; i < PL_ARRAY_SIZE(metal_formats); i++) {
        const struct metal_format *mfmt = &metal_formats[i];
        if (mfmt->pixel == MTLPixelFormatInvalid)
            continue;
        align = PL_MAX(align, mfmt->fmt.texel_size);
        align = PL_MAX(align, [device minimumLinearTextureAlignmentForPixelFormat:mfmt->pixel]);
    }

    return align;
}
