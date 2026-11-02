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

#include <TargetConditionals.h>
#include <spirv_cross_c.h>

#include "gpu.h"
#include "../cache.h"

// Highest MSL version of the OS floors, iOS 17 and macOS 14
#define MSL_MAJOR 3
#define MSL_MINOR 1

#if TARGET_OS_IPHONE
#define MSL_PLATFORM METAL_PLATFORM_IOS
#else
#define MSL_PLATFORM METAL_PLATFORM_MACOS
#endif

// Argument table entries per shader stage, in every Apple GPU family
#define MAX_BUFFERS  31
#define MAX_TEXTURES 31
#define MAX_SAMPLERS 16

static const char *stage_names[METAL_STAGES] = {
    [GLSL_SHADER_VERTEX]   = "vertex",
    [GLSL_SHADER_FRAGMENT] = "fragment",
};

static const SpvExecutionModel stage_models[METAL_STAGES] = {
    [GLSL_SHADER_VERTEX]   = SpvExecutionModelVertex,
    [GLSL_SHADER_FRAGMENT] = SpvExecutionModelFragment,
};

static const spvc_msl_platform spvc_platforms[] = {
    [METAL_PLATFORM_MACOS] = SPVC_MSL_PLATFORM_MACOS,
    [METAL_PLATFORM_IOS]   = SPVC_MSL_PLATFORM_IOS,
};

static const char *stage_glsl(const struct pl_pass_params *params,
                              enum glsl_shader_stage stage)
{
    return stage == GLSL_SHADER_VERTEX ? params->vertex_shader : params->glsl_shader;
}

bool metal_msl_init(pl_gpu gpu)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    uint32_t spirv_ver = PL_MIN(SPV_VERSION, PL_MAX_SPIRV_VER);
    p->spirv = pl_spirv_create(gpu->log, (struct pl_spirv_version) {
        .env_version = pl_spirv_version_to_vulkan(spirv_ver),
        .spv_version = spirv_ver,
    });
    p->msl_target = (struct metal_msl_target) {
        .platform = MSL_PLATFORM,
        .major = MSL_MAJOR,
        .minor = MSL_MINOR,
        .compiler = PL_METAL_COMPILER,
    };
    return p->spirv;
}

void metal_msl_uninit(pl_gpu gpu)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    pl_spirv_destroy(&p->spirv);
}

struct metal_msl_target metal_get_msl_target(pl_gpu gpu)
{
    const struct pl_mtl *p = PL_PRIV(gpu);
    return p->msl_target;
}

void metal_set_msl_target(pl_gpu gpu, struct metal_msl_target target)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    p->msl_target = target;
}

static bool assign_bindings(pl_gpu gpu, const struct pl_pass_params *params,
                            struct metal_binding *bindings)
{
    int buffers = FIRST_UBO_INDEX, textures = 0, samplers = 0;
    for (int i = 0; i < params->num_descriptors; i++) {
        struct metal_binding *b = &bindings[i];
        switch (params->descriptors[i].type) {
        case PL_DESC_BUF_UNIFORM:
            b->index = buffers++;
            break;
        case PL_DESC_SAMPLED_TEX:
            b->index = textures++;
            b->sampler = samplers++;
            break;
        default:
            PL_ERR(gpu, "Metal: descriptor '%s' has an unsupported type",
                   params->descriptors[i].name);
            return false;
        }
    }

    if (buffers > MAX_BUFFERS || textures > MAX_TEXTURES || samplers > MAX_SAMPLERS) {
        PL_ERR(gpu, "Metal: pass needs %d buffers, %d textures and %d samplers, "
               "more than the limits of %d, %d and %d", buffers, textures,
               samplers, MAX_BUFFERS, MAX_TEXTURES, MAX_SAMPLERS);
        return false;
    }

    return true;
}

#define SC(cmd)                                                             \
    do {                                                                    \
        spvc_result res = (cmd);                                            \
        if (res != SPVC_SUCCESS) {                                          \
            PL_ERR(gpu, "Metal: %s: %s (%d)", #cmd,                         \
                   spvc_context_get_last_error_string(sc), res);            \
            goto error;                                                     \
        }                                                                   \
    } while (0)

static spvc_result add_binding(spvc_compiler comp, SpvExecutionModel model,
                               unsigned set, unsigned binding, unsigned buffer,
                               unsigned texture, unsigned sampler)
{
    spvc_msl_resource_binding_2 b;
    spvc_msl_resource_binding_init_2(&b);
    b.stage = model;
    b.desc_set = set;
    b.binding = binding;
    b.count = 1;
    b.msl_buffer = buffer;
    b.msl_texture = texture;
    b.msl_sampler = sampler;
    return spvc_compiler_msl_add_resource_binding_2(comp, &b);
}

// Maps every descriptor and the push constants to their argument table entries
static bool add_bindings(pl_gpu gpu, const struct pl_pass_params *params,
                         const struct metal_shaders *shaders, spvc_context sc,
                         spvc_compiler comp, SpvExecutionModel model)
{
    SC(add_binding(comp, model, SPVC_MSL_PUSH_CONSTANT_DESC_SET,
                   SPVC_MSL_PUSH_CONSTANT_BINDING, PUSHC_BUFFER_INDEX, 0, 0));
    for (int i = 0; i < params->num_descriptors; i++) {
        const struct metal_binding *b = &shaders->bindings[i];
        SC(add_binding(comp, model, 0, params->descriptors[i].binding,
                       b->index, b->index, b->sampler));
    }
    return true;

error:
    return false;
}

static void reflect_usage(const struct pl_pass_params *params, spvc_compiler comp,
                          enum glsl_shader_stage stage, struct metal_shaders *shaders)
{
    SpvExecutionModel model = stage_models[stage];
    if (spvc_compiler_msl_is_resource_used(comp, model, SPVC_MSL_PUSH_CONSTANT_DESC_SET,
                                           SPVC_MSL_PUSH_CONSTANT_BINDING))
        shaders->pushc_stages |= 1 << stage;
    for (int i = 0; i < params->num_descriptors; i++) {
        unsigned binding = params->descriptors[i].binding;
        if (spvc_compiler_msl_is_resource_used(comp, model, 0, binding))
            shaders->bindings[i].stages |= 1 << stage;
    }
}

// MSL of one shader stage
struct metal_msl {
    const char *source;
    const char *entry;
};

static bool spirv_to_msl(pl_gpu gpu, const struct pl_pass_params *params, void *alloc,
                         pl_str spirv, enum glsl_shader_stage stage,
                         struct metal_shaders *shaders, struct metal_msl *out)
{
    const struct pl_mtl *p = PL_PRIV(gpu);
    const struct metal_msl_target *target = &p->msl_target;
    SpvExecutionModel model = stage_models[stage];
    spvc_context sc = NULL;
    spvc_compiler comp;
    spvc_parsed_ir ir;
    spvc_compiler_options opts;
    const char *msl = NULL;
    bool ok = false;

    PL_DEBUG(gpu, "Metal: translating %s shader SPIR-V to MSL %d.%d with SPIRV-Cross",
             stage_names[stage], target->major, target->minor);
    if (spvc_context_create(&sc) != SPVC_SUCCESS) {
        PL_ERR(gpu, "Metal: failed creating a SPIRV-Cross context");
        return false;
    }

    SC(spvc_context_parse_spirv(sc, (const SpvId *) spirv.buf,
                                spirv.len / sizeof(SpvId), &ir));
    SC(spvc_context_create_compiler(sc, SPVC_BACKEND_MSL, ir,
                                    SPVC_CAPTURE_MODE_TAKE_OWNERSHIP, &comp));
    SC(spvc_compiler_create_compiler_options(comp, &opts));
    SC(spvc_compiler_options_set_uint(opts, SPVC_COMPILER_OPTION_MSL_VERSION,
                                      SPVC_MAKE_MSL_VERSION(target->major, target->minor, 0)));
    SC(spvc_compiler_options_set_uint(opts, SPVC_COMPILER_OPTION_MSL_PLATFORM,
                                      spvc_platforms[target->platform]));
    SC(spvc_compiler_options_set_bool(opts, SPVC_COMPILER_OPTION_FLIP_VERTEX_Y,
                                      stage == GLSL_SHADER_VERTEX));
    SC(spvc_compiler_install_compiler_options(comp, opts));
    if (!add_bindings(gpu, params, shaders, sc, comp, model))
        goto error;

    SC(spvc_compiler_compile(comp, &msl));
    reflect_usage(params, comp, stage, shaders);
    *out = (struct metal_msl) {
        .source = pl_strdup0(alloc, pl_str0(msl)),
        .entry = pl_strdup0(alloc, pl_str0(
            spvc_compiler_get_cleansed_entry_point_name(comp, "main", model))),
    };
    ok = true;

error:
    spvc_context_destroy(sc);
    return ok;
}

static bool translate(pl_gpu gpu, const struct pl_pass_params *params, void *alloc,
                      enum glsl_shader_stage stage, struct metal_shaders *shaders,
                      struct metal_msl *out)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    PL_DEBUG(gpu, "Metal: compiling %s shader GLSL with glslang", stage_names[stage]);
    pl_str spirv = pl_spirv_compile_glsl(p->spirv, alloc, gpu->glsl, stage,
                                         stage_glsl(params, stage));
    if (!spirv.len)
        return false;

    return spirv_to_msl(gpu, params, alloc, spirv, stage, shaders, out);
}

// --- Cache
//
// An entry holds a `struct cache_header`, the stages using each descriptor
// as one byte each, then the source and entry point name of every stage as
// NUL-terminated strings.

struct cache_header {
    uint32_t num_descriptors;
    uint32_t pushc_stages;
};

static uint64_t cache_key(pl_gpu gpu, const struct pl_pass_params *params)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    uint64_t key = CACHE_KEY_METAL_MSL;
    for (int s = 0; s < METAL_STAGES; s++) {
        pl_hash_merge(&key, s);
        pl_hash_merge(&key, pl_str0_hash(stage_glsl(params, s)));
    }
    for (int i = 0; i < params->num_descriptors; i++) {
        pl_hash_merge(&key, params->descriptors[i].type);
        pl_hash_merge(&key, params->descriptors[i].binding);
    }
    pl_hash_merge(&key, p->msl_target.platform);
    pl_hash_merge(&key, p->msl_target.major);
    pl_hash_merge(&key, p->msl_target.minor);
    pl_hash_merge(&key, pl_str0_hash(p->msl_target.compiler));
    pl_hash_merge(&key, p->spirv->signature);
    return key;
}

static const char *take_string(pl_str *data, void *alloc)
{
    const uint8_t *nul = memchr(data->buf, 0, data->len);
    if (!nul)
        return NULL;

    pl_str str = pl_str_take(*data, nul - data->buf);
    *data = pl_str_drop(*data, str.len + 1);
    return pl_strdup0(alloc, str);
}

static bool parse_entry(const struct pl_pass_params *params, pl_str data, void *alloc,
                        struct metal_shaders *out, struct metal_msl msl[METAL_STAGES])
{
    struct cache_header header;
    if (data.len < sizeof(header))
        return false;
    memcpy(&header, data.buf, sizeof(header));
    data = pl_str_drop(data, sizeof(header));
    if (header.num_descriptors != params->num_descriptors ||
        data.len < header.num_descriptors || (header.pushc_stages & ~STAGE_ALL))
        return false;

    const uint8_t *stages = data.buf;
    for (int i = 0; i < params->num_descriptors; i++) {
        if (stages[i] & ~STAGE_ALL)
            return false;
    }

    data = pl_str_drop(data, header.num_descriptors);
    for (int s = 0; s < METAL_STAGES; s++) {
        msl[s].source = take_string(&data, alloc);
        msl[s].entry = take_string(&data, alloc);
        if (!msl[s].source || !msl[s].entry)
            return false;
    }
    if (data.len)
        return false;

    out->pushc_stages = header.pushc_stages;
    for (int i = 0; i < params->num_descriptors; i++)
        out->bindings[i].stages = stages[i];
    return true;
}

// Returns whether the cache held the shaders of `params`
static bool load_cached(pl_gpu gpu, const struct pl_pass_params *params, void *alloc,
                        struct metal_shaders *out, struct metal_msl msl[METAL_STAGES])
{
    pl_cache cache = pl_gpu_cache(gpu);
    if (!cache)
        return false;

    pl_cache_obj obj = { .key = cache_key(gpu, params) };
    if (!pl_cache_get(cache, &obj))
        return false;

    bool ok = parse_entry(params, (pl_str) { obj.data, obj.size }, alloc, out, msl);
    if (ok) {
        PL_DEBUG(gpu, "Metal: using cached MSL shaders");
    } else {
        PL_WARN(gpu, "Metal: ignoring malformed cached MSL shaders 0x%"PRIx64, obj.key);
    }
    pl_cache_obj_free(&obj);
    return ok;
}

static void append_string(pl_str *data, const char *str)
{
    pl_str_append_raw(NULL, data, str, strlen(str) + 1);
}

static void store_cached(pl_gpu gpu, const struct pl_pass_params *params,
                         const struct metal_shaders *shaders,
                         const struct metal_msl msl[METAL_STAGES])
{
    pl_cache cache = pl_gpu_cache(gpu);
    if (!cache)
        return;

    const struct cache_header header = {
        .num_descriptors = params->num_descriptors,
        .pushc_stages = shaders->pushc_stages,
    };
    pl_str data = {0};
    pl_str_append_raw(NULL, &data, &header, sizeof(header));
    for (int i = 0; i < params->num_descriptors; i++) {
        const uint8_t stages = shaders->bindings[i].stages;
        pl_str_append_raw(NULL, &data, &stages, sizeof(stages));
    }
    for (int s = 0; s < METAL_STAGES; s++) {
        append_string(&data, msl[s].source);
        append_string(&data, msl[s].entry);
    }

    pl_cache_str(cache, cache_key(gpu, params), &data);
}

// Takes the MSL of `params` from the cache, or translates it
static bool get_msl(pl_gpu gpu, const struct pl_pass_params *params, void *alloc,
                    struct metal_shaders *out, struct metal_msl msl[METAL_STAGES])
{
    if (load_cached(gpu, params, alloc, out, msl))
        return true;

    for (int s = 0; s < METAL_STAGES; s++) {
        if (!translate(gpu, params, alloc, s, out, &msl[s]))
            return false;
    }

    return true;
}

static MTLLanguageVersion language_version(struct metal_msl_target target)
{
    return (MTLLanguageVersion) ((target.major << 16) + target.minor);
}

static id<MTLFunction> build_function(pl_gpu gpu, enum glsl_shader_stage stage,
                                      struct metal_msl msl)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    MTLCompileOptions *opts = [[MTLCompileOptions alloc] init];
    opts.languageVersion = language_version(p->msl_target);
    opts.fastMathEnabled = NO;

    NSError *error = nil;
    id<MTLLibrary> lib = [p->device newLibraryWithSource:@(msl.source)
                                                 options:opts
                                                   error:&error];
    id<MTLFunction> fn = [lib newFunctionWithName:@(msl.entry)];
    int level = fn ? PL_LOG_DEBUG : PL_LOG_ERR;
    PL_MSG(gpu, level, "%s shader MSL source:", stage_names[stage]);
    pl_msg_source(gpu->log, level, msl.source);
    if (!fn) {
        PL_ERR(gpu, "Metal: failed compiling the %s shader MSL: %s", stage_names[stage],
               error ? error.localizedDescription.UTF8String : "no entry point");
    }

    return fn;
}

bool metal_compile_shaders(pl_gpu gpu, const struct pl_pass_params *params,
                           void *alloc, struct metal_shaders *out)
{
    *out = (struct metal_shaders) {
        .bindings = pl_calloc_ptr(alloc, params->num_descriptors, out->bindings),
    };
    void *tmp = pl_tmp(NULL);
    struct metal_msl msl[METAL_STAGES];
    bool ok = assign_bindings(gpu, params, out->bindings) &&
              get_msl(gpu, params, tmp, out, msl);

    for (int s = 0; ok && s < METAL_STAGES; s++) {
        out->functions[s] = build_function(gpu, s, msl[s]);
        ok = out->functions[s];
    }
    if (ok)
        store_cached(gpu, params, out, msl);

    pl_free(tmp);
    return ok;
}
