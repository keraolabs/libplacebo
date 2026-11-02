#include "metal_tests.h"

#include <libplacebo/dispatch.h>
#include <libplacebo/shaders/sampling.h>

#include <CoreVideo/CVPixelBuffer.h>
#include <CoreVideo/CVPixelBufferIOSurface.h>
#include <IOSurface/IOSurfaceRef.h>

#define POLL_TIMEOUT_NS     1000000000ULL // 1 s
#define BGRA_BPE            4
#define BGRA_ROW_PADDING    64
#define RGHA_BPE            8
#define RGHA_VALUES         (TEX_DIM * TEX_DIM * 4)
#define LUMA_FILL           0x10

#define HALF_DROPPED_BITS   13 // float mantissa bits below half precision
#define HALF_DROPPED_MASK   ((1u << HALF_DROPPED_BITS) - 1)
#define HALF_MANTISSA_MASK  0x3ffu
#define FLOAT_EXP_BIAS      127
#define HALF_EXP_BIAS       15
#define HALF_MIN_EXP        (-14)
#define HALF_EXPS           29 // exponents -14 to 14, which never round to infinity

struct surface_layout {
    OSType fourcc;
    int w, h;
    size_t bpe, bpr, elem_w;
};

static void set_number(CFMutableDictionaryRef dict, CFStringRef key, long value)
{
    CFNumberRef number = CFNumberCreate(NULL, kCFNumberLongType, &value);
    CFDictionarySetValue(dict, key, number);
    CFRelease(number);
}

static CFMutableDictionaryRef new_dictionary(void)
{
    return CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks,
                                     &kCFTypeDictionaryValueCallBacks);
}

static IOSurfaceRef create_surface(struct surface_layout layout)
{
    CFMutableDictionaryRef props = new_dictionary();
    set_number(props, kIOSurfacePixelFormat, layout.fourcc);
    set_number(props, kIOSurfaceWidth, layout.w);
    set_number(props, kIOSurfaceHeight, layout.h);
    set_number(props, kIOSurfaceBytesPerElement, layout.bpe);
    set_number(props, kIOSurfaceBytesPerRow, layout.bpr);
    set_number(props, kIOSurfaceElementWidth, layout.elem_w);
    IOSurfaceRef surface = IOSurfaceCreate(props);
    CFRelease(props);
    REQUIRE(surface);
    REQUIRE_CMP(IOSurfaceGetBytesPerRow(surface), ==, layout.bpr, "zu");
    return surface;
}

// Returns a TEX_DIM x TEX_DIM pixel buffer backed by an IOSurface
static CVPixelBufferRef create_pixel_buffer(OSType fourcc)
{
    CFMutableDictionaryRef attrs = new_dictionary();
    CFMutableDictionaryRef surface_props = new_dictionary();
    CFDictionarySetValue(attrs, kCVPixelBufferIOSurfacePropertiesKey, surface_props);
    CFRelease(surface_props);

    CVPixelBufferRef pixbuf = NULL;
    CVReturn ret = CVPixelBufferCreate(NULL, TEX_DIM, TEX_DIM, fourcc, attrs, &pixbuf);
    CFRelease(attrs);
    REQUIRE_CMP(ret, ==, kCVReturnSuccess, "d");
    REQUIRE(CVPixelBufferGetIOSurface(pixbuf));
    return pixbuf;
}

static struct pl_tex_params import_params(pl_gpu gpu, IOSurfaceRef surface, unsigned plane,
                                          const char *format, int w, int h)
{
    pl_fmt fmt = pl_find_named_fmt(gpu, format);
    REQUIRE(fmt);
    return (struct pl_tex_params) {
        .format         = fmt,
        .w              = w,
        .h              = h,
        .import_handle  = PL_HANDLE_IOSURFACE,
        .shared_mem     = {
            .handle.handle = surface,
            .plane         = plane,
        },
    };
}

static pl_tex import_target(pl_gpu gpu, IOSurfaceRef surface, unsigned plane,
                            const char *format, int w, int h)
{
    struct pl_tex_params params = import_params(gpu, surface, plane, format, w, h);
    params.renderable = true;
    params.host_readable = true;
    pl_tex tex = pl_tex_create(gpu, &params);
    REQUIRE(tex);
    return tex;
}

// Renders the texels of `src` to `target` of the same size, then waits until
// `target` is no longer in use
static void render_texels(pl_gpu gpu, pl_tex src, pl_tex target)
{
    pl_dispatch dp = pl_dispatch_create(gpu->log, gpu);
    pl_shader sh = pl_dispatch_begin(dp);
    REQUIRE(pl_shader_sample_nearest(sh, pl_sample_src(.tex = src)));
    REQUIRE(pl_dispatch_finish(dp, pl_dispatch_params(.shader = &sh, .target = target)));
    while (pl_tex_poll(gpu, target, POLL_TIMEOUT_NS))
        ;
    pl_dispatch_destroy(&dp);
}

static void fill_bytes(uint8_t *data, size_t size, int seed)
{
    for (size_t i = 0; i < size; i++)
        data[i] = (i * 13 + seed) % 251;
}

static void bgra_target_tests(pl_gpu gpu)
{
    const size_t bpr = TEX_DIM * BGRA_BPE + BGRA_ROW_PADDING;
    IOSurfaceRef surface = create_surface((struct surface_layout) {
        .fourcc = kCVPixelFormatType_32BGRA,
        .w = TEX_DIM, .h = TEX_DIM,
        .bpe = BGRA_BPE, .bpr = bpr, .elem_w = 1,
    });
    pl_tex target = import_target(gpu, surface, 0, "bgra8", TEX_DIM, TEX_DIM);

    uint8_t pattern[TEX_BYTES];
    fill_bytes(pattern, TEX_BYTES, 1);
    pl_tex src = create_rgba8(gpu);
    REQUIRE(pl_tex_upload(gpu, pl_tex_transfer_params(.tex = src, .ptr = pattern)));
    render_texels(gpu, src, target);

    REQUIRE_CMP(IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL), ==, KERN_SUCCESS, "d");
    const uint8_t *base = IOSurfaceGetBaseAddress(surface);
    for (int y = 0; y < TEX_DIM; y++) {
        for (int x = 0; x < TEX_DIM; x++) {
            const uint8_t *rgba = &pattern[(y * TEX_DIM + x) * 4];
            const uint8_t bgra[4] = { rgba[2], rgba[1], rgba[0], rgba[3] };
            REQUIRE_MEMEQ(base + y * bpr + x * BGRA_BPE, bgra, sizeof(bgra));
        }
    }
    IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);

    pl_tex_destroy(gpu, &src);
    pl_tex_destroy(gpu, &target);
    CFRelease(surface);
}

static void plane_import_tests(pl_gpu gpu)
{
    CVPixelBufferRef pixbuf = create_pixel_buffer(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange);
    IOSurfaceRef surface = CVPixelBufferGetIOSurface(pixbuf);
    const int chroma_w = TEX_DIM / 2, chroma_h = TEX_DIM / 2;
    const size_t chroma_row = chroma_w * 2;
    uint8_t chroma[TEX_DIM / 2 * TEX_DIM / 2 * 2], out[sizeof(chroma)];
    fill_bytes(chroma, sizeof(chroma), 2);

    REQUIRE_CMP(IOSurfaceLock(surface, 0, NULL), ==, KERN_SUCCESS, "d");
    memset(IOSurfaceGetBaseAddressOfPlane(surface, 0), LUMA_FILL,
           IOSurfaceGetBytesPerRowOfPlane(surface, 0) * TEX_DIM);
    uint8_t *plane1 = IOSurfaceGetBaseAddressOfPlane(surface, 1);
    for (int y = 0; y < chroma_h; y++) {
        memcpy(plane1 + y * IOSurfaceGetBytesPerRowOfPlane(surface, 1),
               &chroma[y * chroma_row], chroma_row);
    }
    IOSurfaceUnlock(surface, 0, NULL);

    pl_tex tex = import_target(gpu, surface, 1, "rg8", chroma_w, chroma_h);
    REQUIRE(pl_tex_download(gpu, pl_tex_transfer_params(.tex = tex, .ptr = out)));
    REQUIRE_MEMEQ(out, chroma, sizeof(chroma));

    pl_tex_destroy(gpu, &tex);
    CVPixelBufferRelease(pixbuf);
}

// Floats in the normal range of half floats with every kind of rest below
// half precision: none, below, at and above the midpoint between two halves
static float rgha_value(int i)
{
    static const uint32_t rests[] = { 0, 1, 0xfff, 0x1000, 0x1001, HALF_DROPPED_MASK };
    const int num_rests = PL_ARRAY_SIZE(rests);
    uint32_t sign = (uint32_t) (i & 1) << 31;
    uint32_t rest = rests[i / 2 % num_rests];
    int exp = HALF_MIN_EXP + i / (2 * num_rests) % HALF_EXPS;
    uint32_t mantissa = ((i * 2654435761u) >> 7 & HALF_MANTISSA_MASK) << HALF_DROPPED_BITS;
    uint32_t bits = sign | (uint32_t) (exp + FLOAT_EXP_BIAS) << 23 | mantissa | rest;
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

// Valid for floats in the normal range of half floats
static uint16_t half_toward_zero(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    uint32_t sign = bits >> 16 & 0x8000;
    int exp = (int) (bits >> 23 & 0xff) - FLOAT_EXP_BIAS + HALF_EXP_BIAS;
    uint32_t mantissa = bits >> HALF_DROPPED_BITS & HALF_MANTISSA_MASK;
    return sign | (uint32_t) exp << 10 | mantissa;
}

static uint16_t half_nearest_even(float value)
{
    _Float16 half = (_Float16) value;
    uint16_t bits;
    memcpy(&bits, &half, sizeof(bits));
    return bits;
}

static enum half_rounding rgha_target_tests(pl_gpu gpu)
{
    float values[RGHA_VALUES];
    uint16_t toward_zero[RGHA_VALUES], nearest_even[RGHA_VALUES], out[RGHA_VALUES];
    for (int i = 0; i < RGHA_VALUES; i++) {
        values[i] = rgha_value(i);
        toward_zero[i] = half_toward_zero(values[i]);
        nearest_even[i] = half_nearest_even(values[i]);
    }
    REQUIRE(memcmp(toward_zero, nearest_even, sizeof(out)) != 0);

    pl_tex src = pl_tex_create(gpu, pl_tex_params(
        .format         = pl_find_named_fmt(gpu, "rgba32f"),
        .w              = TEX_DIM,
        .h              = TEX_DIM,
        .sampleable     = true,
        .initial_data   = values,
    ));
    REQUIRE(src);
    CVPixelBufferRef pixbuf = create_pixel_buffer(kCVPixelFormatType_64RGBAHalf);
    IOSurfaceRef surface = CVPixelBufferGetIOSurface(pixbuf);
    pl_tex target = import_target(gpu, surface, 0, "rgba16hf", TEX_DIM, TEX_DIM);
    render_texels(gpu, src, target);

    REQUIRE_CMP(IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL), ==, KERN_SUCCESS, "d");
    const uint8_t *base = IOSurfaceGetBaseAddress(surface);
    const size_t row = TEX_DIM * RGHA_BPE;
    for (int y = 0; y < TEX_DIM; y++)
        memcpy((uint8_t *) out + y * row, base + y * IOSurfaceGetBytesPerRow(surface), row);
    IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);

    enum half_rounding rounding = HALF_TOWARD_ZERO;
    if (memcmp(out, toward_zero, sizeof(out)) != 0) {
        REQUIRE_MEMEQ(out, nearest_even, sizeof(out));
        rounding = HALF_NEAREST_EVEN;
    }

    pl_tex_destroy(gpu, &src);
    pl_tex_destroy(gpu, &target);
    CVPixelBufferRelease(pixbuf);
    return rounding;
}

static void count_errors(void *priv, enum pl_log_level level, const char *msg)
{
    int *errors = priv;
    if (level == PL_LOG_ERR)
        (*errors)++;
    pl_log_timestamp(stdout, level, msg);
}

struct mismatch {
    struct surface_layout layout;
    const char *format;
    int w, h;
    unsigned plane;
};

#define BGRA_SURFACE {                                          \
    .fourcc = kCVPixelFormatType_32BGRA, .w = TEX_DIM, .h = TEX_DIM, \
    .bpe = BGRA_BPE, .bpr = TEX_DIM * BGRA_BPE, .elem_w = 1,    \
}

static const struct mismatch mismatches[] = {
    { BGRA_SURFACE, "bgra8", TEX_DIM / 2, TEX_DIM },
    { BGRA_SURFACE, "bgra8", TEX_DIM, TEX_DIM * 2 },
    { BGRA_SURFACE, "rgba16hf", TEX_DIM, TEX_DIM },
    { BGRA_SURFACE, "bgra8", TEX_DIM, TEX_DIM, .plane = 1 },
    {
        .layout = {
            .fourcc = kCVPixelFormatType_422YpCbCr8, .w = TEX_DIM, .h = TEX_DIM,
            .bpe = BGRA_BPE, .bpr = TEX_DIM * BGRA_BPE / 2, .elem_w = 2,
        },
        .format = "bgra8", .w = TEX_DIM, .h = TEX_DIM,
    },
    {
        .layout = {
            .fourcc = kCVPixelFormatType_32BGRA, .w = TEX_DIM, .h = TEX_DIM,
            .bpe = BGRA_BPE, .bpr = TEX_DIM * BGRA_BPE + BGRA_BPE, .elem_w = 1,
        },
        .format = "bgra8", .w = TEX_DIM, .h = TEX_DIM,
    },
    {
        .layout = {
            .fourcc = kCVPixelFormatType_128RGBAFloat, .w = TEX_DIM, .h = TEX_DIM,
            .bpe = 4 * sizeof(float), .bpr = TEX_DIM * 4 * sizeof(float), .elem_w = 1,
        },
        .format = "rgba16f", .w = TEX_DIM, .h = TEX_DIM,
    },
};

static void mismatch_tests(pl_gpu gpu)
{
    int errors = 0;
    struct pl_log_params old = pl_log_update(gpu->log, pl_log_params(
        .log_cb     = count_errors,
        .log_priv   = &errors,
        .log_level  = PL_LOG_DEBUG,
    ));

    for (int i = 0; i < PL_ARRAY_SIZE(mismatches); i++) {
        const struct mismatch *m = &mismatches[i];
        IOSurfaceRef surface = create_surface(m->layout);
        struct pl_tex_params params = import_params(gpu, surface, m->plane, m->format,
                                                    m->w, m->h);
        params.sampleable = true;
        int before = errors;
        REQUIRE(!pl_tex_create(gpu, &params));
        REQUIRE_CMP(errors, >, before, "d");
        CFRelease(surface);
    }

    pl_log_update(gpu->log, &old);
}

enum half_rounding metal_iosurface_tests(pl_gpu gpu)
{
    bgra_target_tests(gpu);
    plane_import_tests(gpu);
    enum half_rounding rounding = rgha_target_tests(gpu);
    mismatch_tests(gpu);
    return rounding;
}
