#include "gpu_tests.h"

#include <objc/message.h>
#include <objc/runtime.h>

#include <libplacebo/metal.h>

// Metal feature set tables, Apple5 and later
#define APPLE5                  5
#define NEWEST_APPLE_FAMILY     11
#define LARGE_TEX_DIM_FAMILY    10
#define MAX_TEX_DIM             16384
#define MAX_TEX_DIM_LARGE       32768
#define MAX_TEX_3D_DIM          2048
#define MAX_SET_BYTES           4096

// Capabilities the feature set tables give a device configuration
struct expected {
    int apple_family;
    bool float32_filtering;
};

static unsigned long device_ulong(void *device, const char *getter)
{
    return ((unsigned long (*)(id, SEL)) objc_msgSend)((id) device,
            sel_registerName(getter));
}

static bool device_bool(void *device, const char *getter)
{
    return ((BOOL (*)(id, SEL)) objc_msgSend)((id) device, sel_registerName(getter));
}

static bool device_supports_apple_family(void *device, int n)
{
    const long mtl_gpu_family_apple0 = 1000;
    return ((BOOL (*)(id, SEL, long)) objc_msgSend)((id) device,
            sel_registerName("supportsFamily:"), mtl_gpu_family_apple0 + n);
}

static struct expected native_expected(void *device)
{
    struct expected exp = {
        .float32_filtering = device_bool(device, "supports32BitFloatFiltering"),
    };

    for (int n = APPLE5; n <= NEWEST_APPLE_FAMILY; n++) {
        if (device_supports_apple_family(device, n))
            exp.apple_family = n;
    }

    return exp;
}

static void require_caps(pl_gpu gpu, const char *name, enum pl_fmt_caps set,
                         enum pl_fmt_caps unset)
{
    pl_fmt fmt = pl_find_named_fmt(gpu, name);
    REQUIRE(fmt);
    REQUIRE_CMP(fmt->caps & set, ==, set, "x");
    REQUIRE_CMP(fmt->caps & unset, ==, 0, "x");
}

static void metal_limits_tests(pl_metal mtl, struct expected exp)
{
    pl_gpu gpu = mtl->gpu;
    const struct pl_gpu_limits *lim = &gpu->limits;
    uint32_t max_tex_dim = exp.apple_family >= LARGE_TEX_DIM_FAMILY
                         ? MAX_TEX_DIM_LARGE : MAX_TEX_DIM;
    size_t max_buf = device_ulong(mtl->device, "maxBufferLength");

    REQUIRE(lim->thread_safe);
    REQUIRE_CMP(lim->max_tex_1d_dim, ==, max_tex_dim, PRIu32);
    REQUIRE_CMP(lim->max_tex_2d_dim, ==, max_tex_dim, PRIu32);
    REQUIRE_CMP(lim->max_tex_3d_dim, ==, MAX_TEX_3D_DIM, PRIu32);
    REQUIRE_CMP(lim->max_pushc_size, ==, MAX_SET_BYTES, "zu");
    REQUIRE_CMP(lim->max_buf_size, ==, max_buf, "zu");
    REQUIRE_CMP(lim->max_ubo_size, ==, max_buf, "zu");
    REQUIRE_CMP(lim->max_vbo_size, ==, max_buf, "zu");
    REQUIRE_CMP(lim->max_ssbo_size, ==, 0, "zu");
    REQUIRE_CMP(lim->max_buffer_texels, ==, 0, PRIu64);

    REQUIRE_CMP(gpu->glsl.version, ==, 450, "d");
    REQUIRE(gpu->glsl.vulkan);
    REQUIRE(!gpu->glsl.compute);

    REQUIRE(!gpu->import_caps.tex && !gpu->import_caps.buf && !gpu->import_caps.sync);
    REQUIRE(!gpu->export_caps.tex && !gpu->export_caps.buf && !gpu->export_caps.sync);
}

static void metal_format_tests(pl_gpu gpu, struct expected exp)
{
    const enum pl_fmt_caps all = PL_FMT_CAP_SAMPLEABLE | PL_FMT_CAP_LINEAR |
                                 PL_FMT_CAP_RENDERABLE | PL_FMT_CAP_BLENDABLE |
                                 PL_FMT_CAP_BLITTABLE | PL_FMT_CAP_HOST_READABLE;
    const enum pl_fmt_caps integer = PL_FMT_CAP_SAMPLEABLE | PL_FMT_CAP_RENDERABLE |
                                     PL_FMT_CAP_HOST_READABLE;
    const enum pl_fmt_caps not_integer = PL_FMT_CAP_LINEAR | PL_FMT_CAP_BLENDABLE |
                                         PL_FMT_CAP_BLITTABLE;
    const enum pl_fmt_caps compute = PL_FMT_CAP_STORABLE | PL_FMT_CAP_READWRITE |
                                     PL_FMT_CAP_TEXEL_UNIFORM | PL_FMT_CAP_TEXEL_STORAGE;

    for (int i = 0; i < gpu->num_formats; i++)
        REQUIRE_CMP(gpu->formats[i]->caps & compute, ==, 0, "x");

    require_caps(gpu, "rgba16hf", all, 0);
    require_caps(gpu, "r8", all, 0);
    require_caps(gpu, "rgb10a2", all, 0);
    require_caps(gpu, "bgra8", all, 0);
    require_caps(gpu, "rgba8u", integer, not_integer);
    require_caps(gpu, "r32i", integer, not_integer);

    const enum pl_fmt_caps linear32 = exp.float32_filtering ? PL_FMT_CAP_LINEAR : 0;
    const char *float32[] = { "r32f", "rg32f", "rgba32f" };
    for (int i = 0; i < PL_ARRAY_SIZE(float32); i++) {
        require_caps(gpu, float32[i], (all & ~PL_FMT_CAP_LINEAR) | linear32,
                     PL_FMT_CAP_LINEAR & ~linear32);
    }

    pl_fmt bgra8 = pl_find_named_fmt(gpu, "bgra8");
    const int bgra[4] = { 2, 1, 0, 3 };
    for (int i = 0; i < 4; i++)
        REQUIRE_CMP(bgra8->sample_order[i], ==, bgra[i], "d");

    for (int comps = 1; comps <= 4; comps++) {
        REQUIRE(pl_find_vertex_fmt(gpu, PL_FMT_FLOAT, comps));
        REQUIRE(pl_find_vertex_fmt(gpu, PL_FMT_UINT, comps));
        REQUIRE(pl_find_vertex_fmt(gpu, PL_FMT_SINT, comps));
    }

    require_caps(gpu, "rgb32f", PL_FMT_CAP_VERTEX, ~PL_FMT_CAP_VERTEX);
    require_caps(gpu, "rgb8", PL_FMT_CAP_VERTEX, ~PL_FMT_CAP_VERTEX);
}

static void require_caps_within(pl_gpu capped, pl_gpu gpu)
{
    REQUIRE_CMP(capped->num_formats, ==, gpu->num_formats, "d");
    for (int i = 0; i < capped->num_formats; i++) {
        pl_fmt fmt = capped->formats[i];
        pl_fmt ref = pl_find_named_fmt(gpu, fmt->name);
        REQUIRE(ref);
        REQUIRE_CMP(fmt->caps & ~ref->caps, ==, 0, "x");
    }

    REQUIRE_CMP(capped->limits.max_tex_2d_dim, <=, gpu->limits.max_tex_2d_dim, PRIu32);
}

static void metal_teardown(pl_metal *mtl)
{
    REQUIRE(!pl_gpu_is_failed((*mtl)->gpu));
    pl_metal_destroy(mtl);
    REQUIRE(!*mtl);
}

int main()
{
    const char *validation = getenv("MTL_DEBUG_LAYER");
    REQUIRE(validation && strcmp(validation, "1") == 0);

    pl_log log = pl_test_logger();

    pl_metal native = pl_metal_create(log, NULL);
    REQUIRE(native);
    REQUIRE(pl_metal_get(native->gpu) == native);

    pl_metal apple5 = pl_metal_create(log, pl_metal_params(
        .device = native->device,
        .max_apple_family = APPLE5,
    ));
    REQUIRE(apple5);
    REQUIRE(apple5->device == native->device);

    REQUIRE(!pl_metal_create(log, pl_metal_params(.max_apple_family = APPLE5 - 1)));

    pl_metal shared = pl_metal_create(log, pl_metal_params(.queue = native->queue));
    REQUIRE(shared);
    REQUIRE(shared->queue == native->queue);
    REQUIRE(shared->device == native->device);
    metal_teardown(&shared);

    struct expected native_exp = native_expected(native->device);
    struct expected apple5_exp = { .apple_family = APPLE5 };
    metal_limits_tests(native, native_exp);
    metal_limits_tests(apple5, apple5_exp);
    metal_format_tests(native->gpu, native_exp);
    metal_format_tests(apple5->gpu, apple5_exp);
    require_caps_within(apple5->gpu, native->gpu);

    metal_teardown(&apple5);
    metal_teardown(&native);
    pl_log_destroy(&log);
}
