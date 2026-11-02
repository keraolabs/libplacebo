#include "metal_tests.h"
#include "metal/msl_target.h"

#include <libplacebo/renderer.h>
#include <libplacebo/utils/upload.h>

#define FRAME_W         37
#define FRAME_H         23
#define FRAME_PLANES    3
#define TARGET_W        64
#define TARGET_H        48
#define TARGET_BYTES    (TARGET_W * TARGET_H * 4)

// Every MSL source SPIRV-Cross writes starts with this line
#define MSL_MARKER              "#include <metal_stdlib>"
#define STAGES_PER_PASS         2
// Bytes of a cached pass besides its MSL sources: a few per descriptor and
// for the entry point names, far less than any compiled code
#define MAX_REFLECTION_BYTES    64

// An MSL version every supported OS compiles, other than the one in use
#define OTHER_MSL_MAJOR         3
#define OTHER_MSL_MINOR         0
#define OTHER_COMPILER          "glslang 0.0.0 0, SPIRV-Cross 0.0.0 0"

#define MAX_CACHED_PASSES       32

struct translation_count {
    int glslang;
    int spirv_cross;
    int warnings;
    int errors;
};

// Prints messages at INFO and above; the DEBUG output of every pass creation
// is only counted
static void count_translation_logs(void *priv, enum pl_log_level level, const char *msg)
{
    struct translation_count *count = priv;
    if (level <= PL_LOG_ERR)
        count->errors++;
    if (level == PL_LOG_WARN)
        count->warnings++;
    if (level == PL_LOG_DEBUG && strstr(msg, "with glslang"))
        count->glslang++;
    if (level == PL_LOG_DEBUG && strstr(msg, "with SPIRV-Cross"))
        count->spirv_cross++;
    if (level <= PL_LOG_INFO)
        pl_log_timestamp(stdout, level, msg);
}

static void upload_frame(pl_gpu gpu, struct pl_frame *frame, pl_tex planes[FRAME_PLANES])
{
    *frame = (struct pl_frame) {
        .num_planes = FRAME_PLANES,
        .repr       = pl_color_repr_hdtv,
        .color      = pl_color_space_bt709,
    };

    uint8_t pixels[FRAME_W * FRAME_H];
    for (int i = 0; i < FRAME_PLANES; i++) {
        const int sub = i > 0;
        const int w = (FRAME_W + sub) >> sub, h = (FRAME_H + sub) >> sub;
        for (int p = 0; p < w * h; p++)
            pixels[p] = (p * (7 + 2 * i) + 31 * i) % 251;
        REQUIRE(pl_upload_plane(gpu, &frame->planes[i], &planes[i], &(struct pl_plane_data) {
            .type           = PL_FMT_UNORM,
            .width          = w,
            .height         = h,
            .component_size = {8},
            .component_map  = {i},
            .pixel_stride   = 1,
            .pixels         = pixels,
        }));
    }

    pl_frame_set_chroma_location(frame, PL_CHROMA_LEFT);
}

// Renders the same 4:2:0 frame with the default parameters on every call,
// and creates the snapshot pass
static void run_fixed_workload(pl_gpu gpu, uint8_t out[TARGET_BYTES])
{
    pl_tex planes[FRAME_PLANES] = {0};
    struct pl_frame image;
    upload_frame(gpu, &image, planes);

    pl_tex target_tex = pl_tex_create(gpu, pl_tex_params(
        .format         = pl_find_named_fmt(gpu, "rgba8"),
        .w              = TARGET_W,
        .h              = TARGET_H,
        .renderable     = true,
        .host_readable  = true,
    ));
    REQUIRE(target_tex);
    struct pl_frame target = {
        .num_planes = 1,
        .planes[0]  = {
            .texture            = target_tex,
            .components         = 4,
            .component_mapping  = {0, 1, 2, 3},
        },
        .repr       = pl_color_repr_rgb,
        .color      = pl_color_space_srgb,
    };

    pl_renderer rr = pl_renderer_create(gpu->log, gpu);
    REQUIRE(rr);
    REQUIRE(pl_render_image(rr, &image, &target, &pl_render_default_params));
    REQUIRE(pl_tex_download(gpu, pl_tex_transfer_params(.tex = target_tex, .ptr = out)));

    struct pl_pass_params params = snapshot_pass_params(gpu);
    pl_pass pass = pl_pass_create(gpu, &params);
    REQUIRE(pass);

    pl_pass_destroy(gpu, &pass);
    pl_renderer_destroy(&rr);
    pl_tex_destroy(gpu, &target_tex);
    for (int i = 0; i < FRAME_PLANES; i++)
        pl_tex_destroy(gpu, &planes[i]);
}

struct msl_census {
    int entries;            // cached objects holding MSL
    int malformed;          // entries without exactly two NUL-terminated sources
    size_t max_other_bytes; // most bytes of an entry outside its MSL sources
};

static bool is_text(uint8_t c)
{
    return c == '\n' || c == '\t' || (c >= ' ' && c <= '~');
}

// Returns the run of text in `data` around the first MSL marker, which must
// end with a NUL, or {0}
static pl_str find_msl_source(pl_str data)
{
    int marker = pl_str_find(data, pl_str0(MSL_MARKER));
    if (marker < 0)
        return (pl_str) {0};

    size_t start = marker;
    while (start > 0 && is_text(data.buf[start - 1]))
        start--;
    pl_str source = pl_str_drop(data, start);
    size_t len = strnlen((const char *) source.buf, source.len);
    return len < source.len ? pl_str_take(source, len) : (pl_str) {0};
}

static void count_msl_entry(void *priv, pl_cache_obj obj)
{
    struct msl_census *census = priv;
    const pl_str data = { obj.data, obj.size };
    if (pl_str_find(data, pl_str0(MSL_MARKER)) < 0)
        return;

    census->entries++;
    pl_str rest = data;
    size_t msl_bytes = 0;
    for (int i = 0; i < STAGES_PER_PASS; i++) {
        pl_str source = find_msl_source(rest);
        if (!source.len) {
            census->malformed++;
            return;
        }
        msl_bytes += source.len;
        rest = pl_str_drop(rest, source.buf - rest.buf + source.len);
    }

    if (pl_str_find(rest, pl_str0(MSL_MARKER)) >= 0)
        census->malformed++;
    census->max_other_bytes = PL_MAX(census->max_other_bytes, obj.size - msl_bytes);
}

// Requires `cache` to hold `passes` entries of MSL and reflection, and no
// compiled code
static void require_msl_entries(pl_cache cache, int passes)
{
    struct msl_census census = {0};
    pl_cache_iterate(cache, count_msl_entry, &census);
    REQUIRE_CMP(census.entries, ==, passes, "d");
    REQUIRE_CMP(census.malformed, ==, 0, "d");
    REQUIRE_CMP(census.max_other_bytes, <=, MAX_REFLECTION_BYTES, "zu");
}

// Creates the snapshot pass for `target`, requiring `translations` glslang
// and SPIRV-Cross runs
static void require_translations(pl_gpu gpu, const struct translation_count *count,
                                 struct metal_msl_target target, int translations)
{
    metal_set_msl_target(gpu, target);
    const struct translation_count before = *count;
    struct pl_pass_params params = snapshot_pass_params(gpu);
    pl_pass pass = pl_pass_create(gpu, &params);
    REQUIRE(pass);
    REQUIRE_CMP(count->glslang - before.glslang, ==, translations, "d");
    REQUIRE_CMP(count->spirv_cross - before.spirv_cross, ==, translations, "d");
    pl_pass_destroy(gpu, &pass);
}

// Requires a miss in the cache of `gpu`, which holds the snapshot pass, for
// another platform, MSL version or compiler, and still a hit for its own
static void require_target_in_key(pl_gpu gpu, const struct translation_count *count)
{
    const struct metal_msl_target own = metal_get_msl_target(gpu);
    REQUIRE(own.major != OTHER_MSL_MAJOR || own.minor != OTHER_MSL_MINOR);
    REQUIRE(strcmp(own.compiler, OTHER_COMPILER) != 0);

    struct metal_msl_target platform = own, version = own, compiler = own;
    platform.platform = own.platform == METAL_PLATFORM_IOS ? METAL_PLATFORM_MACOS
                                                           : METAL_PLATFORM_IOS;
    version.major = OTHER_MSL_MAJOR;
    version.minor = OTHER_MSL_MINOR;
    compiler.compiler = OTHER_COMPILER;
    require_translations(gpu, count, platform, STAGES_PER_PASS);
    require_translations(gpu, count, version, STAGES_PER_PASS);
    require_translations(gpu, count, compiler, STAGES_PER_PASS);
    require_translations(gpu, count, own, 0);
}

struct damaged_entries {
    pl_cache_obj objs[MAX_CACHED_PASSES];
    int num;
};

static void copy_first_half(void *priv, pl_cache_obj obj)
{
    struct damaged_entries *damaged = priv;
    if (pl_str_find((pl_str) { obj.data, obj.size }, pl_str0(MSL_MARKER)) < 0)
        return;
    REQUIRE_CMP(damaged->num, <, MAX_CACHED_PASSES, "d");
    damaged->objs[damaged->num++] = (pl_cache_obj) {
        .key  = obj.key,
        .data = pl_memdup(NULL, obj.data, obj.size / 2),
        .size = obj.size / 2,
        .free = pl_free,
    };
}

// Cuts every MSL entry of `cache` to its first half, returning their number
static int damage_msl_entries(pl_cache cache)
{
    struct damaged_entries damaged = {0};
    pl_cache_iterate(cache, copy_first_half, &damaged);
    for (int i = 0; i < damaged.num; i++)
        pl_cache_set(cache, &damaged.objs[i]);
    return damaged.num;
}

struct cache_test {
    pl_log log;
    struct translation_count count;
    struct pl_metal_params params;
    struct translation_count cold;  // the cold start's translations
    uint8_t cold_out[TARGET_BYTES];
    uint8_t *saved;                 // the cold start's cache, saved
    size_t saved_size;
};

// Returns a new `pl_metal` using `cache`, with every count reset
static pl_metal create_gpu(struct cache_test *t, pl_cache cache)
{
    pl_metal mtl = pl_metal_create(t->log, &t->params);
    REQUIRE(mtl);
    pl_gpu_set_cache(mtl->gpu, cache);
    t->count = (struct translation_count) {0};
    return mtl;
}

static pl_cache load_saved_cache(struct cache_test *t)
{
    pl_cache cache = pl_cache_create(pl_cache_params(.log = t->log));
    REQUIRE_CMP(pl_cache_load(cache, t->saved, t->saved_size), >, 0, "d");
    return cache;
}

static void destroy_gpu(struct cache_test *t, pl_metal *mtl, pl_cache *cache)
{
    REQUIRE_CMP(t->count.errors, ==, 0, "d");
    REQUIRE(!pl_gpu_is_failed((*mtl)->gpu));
    pl_metal_destroy(mtl);
    pl_cache_destroy(cache);
}

static void cold_start(struct cache_test *t)
{
    pl_cache cache = pl_cache_create(pl_cache_params(.log = t->log));
    pl_metal mtl = create_gpu(t, cache);
    run_fixed_workload(mtl->gpu, t->cold_out);
    REQUIRE_CMP(t->count.glslang, >, 0, "d");
    REQUIRE_CMP(t->count.spirv_cross, ==, t->count.glslang, "d");
    REQUIRE_CMP(t->count.warnings, ==, 0, "d");
    t->cold = t->count;
    require_msl_entries(cache, t->cold.glslang / STAGES_PER_PASS);

    t->saved_size = pl_cache_save(cache, NULL, 0);
    t->saved = malloc(t->saved_size);
    REQUIRE(t->saved);
    REQUIRE_CMP(pl_cache_save(cache, t->saved, t->saved_size), ==, t->saved_size, "zu");
    destroy_gpu(t, &mtl, &cache);
}

static void warm_start(struct cache_test *t)
{
    pl_cache cache = load_saved_cache(t);
    pl_metal mtl = create_gpu(t, cache);
    uint8_t out[TARGET_BYTES];
    run_fixed_workload(mtl->gpu, out);
    REQUIRE_CMP(t->count.glslang, ==, 0, "d");
    REQUIRE_CMP(t->count.spirv_cross, ==, 0, "d");
    REQUIRE_CMP(t->count.warnings, ==, 0, "d");
    REQUIRE_MEMEQ(out, t->cold_out, TARGET_BYTES);
    require_msl_entries(cache, t->cold.glslang / STAGES_PER_PASS);

    require_target_in_key(mtl->gpu, &t->count);
    destroy_gpu(t, &mtl, &cache);
}

static void damaged_start(struct cache_test *t)
{
    const int passes = t->cold.glslang / STAGES_PER_PASS;
    pl_cache cache = load_saved_cache(t);
    REQUIRE_CMP(damage_msl_entries(cache), ==, passes, "d");
    pl_metal mtl = create_gpu(t, cache);
    uint8_t out[TARGET_BYTES];
    run_fixed_workload(mtl->gpu, out);
    REQUIRE_CMP(t->count.warnings, ==, passes, "d");
    REQUIRE_CMP(t->count.glslang, ==, t->cold.glslang, "d");
    REQUIRE_CMP(t->count.spirv_cross, ==, t->cold.spirv_cross, "d");
    REQUIRE_MEMEQ(out, t->cold_out, TARGET_BYTES);
    require_msl_entries(cache, passes);
    destroy_gpu(t, &mtl, &cache);
}

void metal_cache_tests(pl_metal mtl, int max_apple_family)
{
    struct cache_test t = {
        .params = {
            .device             = mtl->device,
            .max_apple_family   = max_apple_family,
        },
    };
    t.log = pl_log_create(PL_API_VER, pl_log_params(
        .log_cb     = count_translation_logs,
        .log_priv   = &t.count,
        .log_level  = PL_LOG_DEBUG,
    ));

    cold_start(&t);
    warm_start(&t);
    damaged_start(&t);

    free(t.saved);
    pl_log_destroy(&t.log);
}
