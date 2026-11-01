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

#include "formats.h"
#include "../glsl/utils.h"

// Highest MSL version of the OS floors, iOS 17 and macOS 14
#define MSL_MAJOR 3
#define MSL_MINOR 1
#define MSL_LANGUAGE_VERSION MTLLanguageVersion3_1

#if TARGET_OS_IPHONE
#define MSL_PLATFORM SPVC_MSL_PLATFORM_IOS
#else
#define MSL_PLATFORM SPVC_MSL_PLATFORM_MACOS
#endif

// Argument table entries per shader stage, in every Apple GPU family
#define MAX_BUFFERS  31
#define MAX_TEXTURES 31
#define MAX_SAMPLERS 16

// Buffer argument table layout, the same in both stages
#define VERTEX_BUFFER_INDEX 0
#define PUSHC_BUFFER_INDEX  1
#define FIRST_UBO_INDEX     2

// Alignment of the structs MSL declares for push constant and uniform blocks
#define MSL_STRUCT_ALIGN 16

enum metal_stage_bits {
    STAGE_VERTEX   = 1 << GLSL_SHADER_VERTEX,
    STAGE_FRAGMENT = 1 << GLSL_SHADER_FRAGMENT,
};

// MSL argument table entries of a descriptor, and the stages using it
struct metal_binding {
    int index;   // buffer or texture index
    int sampler; // sampler index of a sampled texture
    enum metal_stage_bits stages;
};

struct pl_pass_mtl {
    id<MTLRenderPipelineState> pipeline;
    struct metal_binding *bindings; // one per `params.descriptors` entry
    enum metal_stage_bits pushc_stages;
};

// MSL translated from one shader stage
struct metal_msl {
    const char *source;
    const char *entry;
};

static const char *stage_names[] = {
    [GLSL_SHADER_VERTEX]   = "vertex",
    [GLSL_SHADER_FRAGMENT] = "fragment",
};

static const SpvExecutionModel stage_models[] = {
    [GLSL_SHADER_VERTEX]   = SpvExecutionModelVertex,
    [GLSL_SHADER_FRAGMENT] = SpvExecutionModelFragment,
};

bool metal_pass_init(pl_gpu gpu)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    uint32_t spirv_ver = PL_MIN(SPV_VERSION, PL_MAX_SPIRV_VER);
    p->spirv = pl_spirv_create(gpu->log, (struct pl_spirv_version) {
        .env_version = pl_spirv_version_to_vulkan(spirv_ver),
        .spv_version = spirv_ver,
    });
    if (!p->spirv)
        return false;

    static const MTLSamplerMinMagFilter filters[PL_TEX_SAMPLE_MODE_COUNT] = {
        [PL_TEX_SAMPLE_NEAREST] = MTLSamplerMinMagFilterNearest,
        [PL_TEX_SAMPLE_LINEAR]  = MTLSamplerMinMagFilterLinear,
    };
    static const MTLSamplerAddressMode addresses[PL_TEX_ADDRESS_MODE_COUNT] = {
        [PL_TEX_ADDRESS_CLAMP]  = MTLSamplerAddressModeClampToEdge,
        [PL_TEX_ADDRESS_REPEAT] = MTLSamplerAddressModeRepeat,
        [PL_TEX_ADDRESS_MIRROR] = MTLSamplerAddressModeMirrorRepeat,
    };

    for (int s = 0; s < PL_TEX_SAMPLE_MODE_COUNT; s++) {
        for (int a = 0; a < PL_TEX_ADDRESS_MODE_COUNT; a++) {
            MTLSamplerDescriptor *desc = [[MTLSamplerDescriptor alloc] init];
            desc.minFilter = desc.magFilter = filters[s];
            desc.sAddressMode = desc.tAddressMode = desc.rAddressMode = addresses[a];
            p->samplers[s][a] = [p->device newSamplerStateWithDescriptor:desc];
            if (!p->samplers[s][a]) {
                PL_ERR(gpu, "Metal: failed creating a sampler state");
                return false;
            }
        }
    }

    return true;
}

void metal_pass_uninit(pl_gpu gpu)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    for (int s = 0; s < PL_TEX_SAMPLE_MODE_COUNT; s++) {
        for (int a = 0; a < PL_TEX_ADDRESS_MODE_COUNT; a++)
            p->samplers[s][a] = nil;
    }
    pl_spirv_destroy(&p->spirv);
}

static bool assign_bindings(pl_gpu gpu, struct pl_pass_t *pass)
{
    struct pl_pass_mtl *pass_mtl = PL_PRIV(pass);
    const struct pl_pass_params *params = &pass->params;
    int buffers = FIRST_UBO_INDEX, textures = 0, samplers = 0;

    pass_mtl->bindings = pl_calloc_ptr(pass, params->num_descriptors, pass_mtl->bindings);
    for (int i = 0; i < params->num_descriptors; i++) {
        struct metal_binding *b = &pass_mtl->bindings[i];
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
static bool add_bindings(pl_gpu gpu, pl_pass pass, spvc_context sc,
                         spvc_compiler comp, SpvExecutionModel model)
{
    const struct pl_pass_mtl *pass_mtl = PL_PRIV(pass);
    SC(add_binding(comp, model, SPVC_MSL_PUSH_CONSTANT_DESC_SET,
                   SPVC_MSL_PUSH_CONSTANT_BINDING, PUSHC_BUFFER_INDEX, 0, 0));
    for (int i = 0; i < pass->params.num_descriptors; i++) {
        const struct metal_binding *b = &pass_mtl->bindings[i];
        SC(add_binding(comp, model, 0, pass->params.descriptors[i].binding,
                       b->index, b->index, b->sampler));
    }
    return true;

error:
    return false;
}

static void reflect_usage(pl_pass pass, spvc_compiler comp, enum glsl_shader_stage stage)
{
    struct pl_pass_mtl *pass_mtl = PL_PRIV(pass);
    SpvExecutionModel model = stage_models[stage];
    if (spvc_compiler_msl_is_resource_used(comp, model, SPVC_MSL_PUSH_CONSTANT_DESC_SET,
                                           SPVC_MSL_PUSH_CONSTANT_BINDING))
        pass_mtl->pushc_stages |= 1 << stage;
    for (int i = 0; i < pass->params.num_descriptors; i++) {
        unsigned binding = pass->params.descriptors[i].binding;
        if (spvc_compiler_msl_is_resource_used(comp, model, 0, binding))
            pass_mtl->bindings[i].stages |= 1 << stage;
    }
}

static bool spirv_to_msl(pl_gpu gpu, pl_pass pass, void *alloc, pl_str spirv,
                         enum glsl_shader_stage stage, struct metal_msl *out)
{
    SpvExecutionModel model = stage_models[stage];
    spvc_context sc = NULL;
    spvc_compiler comp;
    spvc_parsed_ir ir;
    spvc_compiler_options opts;
    const char *msl = NULL;
    bool ok = false;

    PL_DEBUG(gpu, "Metal: translating %s shader SPIR-V to MSL %d.%d with SPIRV-Cross",
             stage_names[stage], MSL_MAJOR, MSL_MINOR);
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
                                      SPVC_MAKE_MSL_VERSION(MSL_MAJOR, MSL_MINOR, 0)));
    SC(spvc_compiler_options_set_uint(opts, SPVC_COMPILER_OPTION_MSL_PLATFORM,
                                      MSL_PLATFORM));
    SC(spvc_compiler_options_set_bool(opts, SPVC_COMPILER_OPTION_FLIP_VERTEX_Y,
                                      stage == GLSL_SHADER_VERTEX));
    SC(spvc_compiler_install_compiler_options(comp, opts));
    if (!add_bindings(gpu, pass, sc, comp, model))
        goto error;

    SC(spvc_compiler_compile(comp, &msl));
    reflect_usage(pass, comp, stage);
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

static bool translate(pl_gpu gpu, pl_pass pass, void *alloc,
                      enum glsl_shader_stage stage, const char *glsl,
                      struct metal_msl *out)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    PL_DEBUG(gpu, "Metal: compiling %s shader GLSL with glslang", stage_names[stage]);
    pl_str spirv = pl_spirv_compile_glsl(p->spirv, alloc, gpu->glsl, stage, glsl);
    if (!spirv.len)
        return false;

    return spirv_to_msl(gpu, pass, alloc, spirv, stage, out);
}

static id<MTLFunction> build_function(pl_gpu gpu, enum glsl_shader_stage stage,
                                      struct metal_msl msl)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    MTLCompileOptions *opts = [[MTLCompileOptions alloc] init];
    opts.languageVersion = MSL_LANGUAGE_VERSION;
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

static MTLVertexDescriptor *vertex_descriptor(const struct pl_pass_params *params)
{
    MTLVertexDescriptor *desc = [MTLVertexDescriptor vertexDescriptor];
    for (int i = 0; i < params->num_vertex_attribs; i++) {
        const struct pl_vertex_attrib *va = &params->vertex_attribs[i];
        MTLVertexAttributeDescriptor *attr = desc.attributes[va->location];
        attr.format = metal_format(va->fmt)->vertex;
        attr.offset = va->offset;
        attr.bufferIndex = VERTEX_BUFFER_INDEX;
    }

    desc.layouts[VERTEX_BUFFER_INDEX].stride = params->vertex_stride;
    return desc;
}

static void set_blending(MTLRenderPipelineColorAttachmentDescriptor *color,
                         const struct pl_blend_params *blend)
{
    static const MTLBlendFactor factors[PL_BLEND_MODE_COUNT] = {
        [PL_BLEND_ZERO]                = MTLBlendFactorZero,
        [PL_BLEND_ONE]                 = MTLBlendFactorOne,
        [PL_BLEND_SRC_ALPHA]           = MTLBlendFactorSourceAlpha,
        [PL_BLEND_ONE_MINUS_SRC_ALPHA] = MTLBlendFactorOneMinusSourceAlpha,
    };

    color.blendingEnabled = YES;
    color.sourceRGBBlendFactor = factors[blend->src_rgb];
    color.destinationRGBBlendFactor = factors[blend->dst_rgb];
    color.sourceAlphaBlendFactor = factors[blend->src_alpha];
    color.destinationAlphaBlendFactor = factors[blend->dst_alpha];
}

static id<MTLRenderPipelineState> build_pipeline(pl_gpu gpu, const struct pl_pass_params *params,
                                                 id<MTLFunction> vs, id<MTLFunction> fs)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    MTLRenderPipelineDescriptor *desc = [[MTLRenderPipelineDescriptor alloc] init];
    desc.vertexFunction = vs;
    desc.fragmentFunction = fs;
    desc.vertexDescriptor = vertex_descriptor(params);
    desc.colorAttachments[0].pixelFormat = metal_format(params->target_format)->pixel;
    if (params->blend_params)
        set_blending(desc.colorAttachments[0], params->blend_params);

    NSError *error = nil;
    id<MTLRenderPipelineState> pipeline =
        [p->device newRenderPipelineStateWithDescriptor:desc error:&error];
    if (!pipeline) {
        PL_ERR(gpu, "Metal: failed creating a render pipeline: %s",
               error ? error.localizedDescription.UTF8String : "no error");
    }

    return pipeline;
}

void metal_pass_destroy(pl_gpu gpu, pl_pass pass)
{
    @autoreleasepool {
        struct pl_pass_mtl *pass_mtl = PL_PRIV(pass);
        pass_mtl->pipeline = nil;
        pl_free((void *) pass);
    }
}

pl_pass metal_pass_create(pl_gpu gpu, const struct pl_pass_params *params)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return NULL;

        pl_assert(params->type == PL_PASS_RASTER);
        struct pl_pass_t *pass = pl_zalloc_obj(NULL, pass, struct pl_pass_mtl);
        pass->params = pl_pass_params_copy(pass, params);
        struct pl_pass_mtl *pass_mtl = PL_PRIV(pass);
        void *tmp = pl_tmp(NULL);
        struct metal_msl vert, frag;
        id<MTLFunction> vs = nil, fs = nil;

        if (!assign_bindings(gpu, pass))
            goto error;
        if (!translate(gpu, pass, tmp, GLSL_SHADER_VERTEX, params->vertex_shader, &vert))
            goto error;
        if (!translate(gpu, pass, tmp, GLSL_SHADER_FRAGMENT, params->glsl_shader, &frag))
            goto error;
        if (!(vs = build_function(gpu, GLSL_SHADER_VERTEX, vert)))
            goto error;
        if (!(fs = build_function(gpu, GLSL_SHADER_FRAGMENT, frag)))
            goto error;
        if (!(pass_mtl->pipeline = build_pipeline(gpu, params, vs, fs)))
            goto error;

        pl_free(tmp);
        return pass;

error:
        pl_free(tmp);
        metal_pass_destroy(gpu, pass);
        return NULL;
    }
}

static void set_buffer(id<MTLRenderCommandEncoder> enc, enum metal_stage_bits stages,
                       struct metal_slice slice, int index)
{
    if (stages & STAGE_VERTEX)
        [enc setVertexBuffer:slice.buf offset:slice.offset atIndex:index];
    if (stages & STAGE_FRAGMENT)
        [enc setFragmentBuffer:slice.buf offset:slice.offset atIndex:index];
}

static void set_texture(id<MTLRenderCommandEncoder> enc, enum metal_stage_bits stages,
                        id<MTLTexture> tex, id<MTLSamplerState> sampler,
                        const struct metal_binding *b)
{
    if (stages & STAGE_VERTEX) {
        [enc setVertexTexture:tex atIndex:b->index];
        [enc setVertexSamplerState:sampler atIndex:b->sampler];
    }
    if (stages & STAGE_FRAGMENT) {
        [enc setFragmentTexture:tex atIndex:b->index];
        [enc setFragmentSamplerState:sampler atIndex:b->sampler];
    }
}

static void set_push_constants(id<MTLRenderCommandEncoder> enc, pl_pass pass,
                               const void *data)
{
    const struct pl_pass_mtl *pass_mtl = PL_PRIV(pass);
    size_t size = pass->params.push_constants_size;
    if (!pass_mtl->pushc_stages)
        return;

    uint8_t padded[METAL_MAX_SET_BYTES] = {0};
    memcpy(padded, data, size);
    size = PL_ALIGN2(size, MSL_STRUCT_ALIGN);
    if (pass_mtl->pushc_stages & STAGE_VERTEX)
        [enc setVertexBytes:padded length:size atIndex:PUSHC_BUFFER_INDEX];
    if (pass_mtl->pushc_stages & STAGE_FRAGMENT)
        [enc setFragmentBytes:padded length:size atIndex:PUSHC_BUFFER_INDEX];
}

// Binds the descriptors, copying uniform buffers into the arena
static bool bind_descriptors(pl_gpu gpu, struct metal_cmd *cmd,
                             id<MTLRenderCommandEncoder> enc,
                             const struct pl_pass_run_params *params)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    pl_pass pass = params->pass;
    const struct pl_pass_mtl *pass_mtl = PL_PRIV(pass);

    for (int i = 0; i < pass->params.num_descriptors; i++) {
        const struct metal_binding *b = &pass_mtl->bindings[i];
        struct pl_desc_binding db = params->desc_bindings[i];
        if (!b->stages)
            continue;

        if (pass->params.descriptors[i].type == PL_DESC_BUF_UNIFORM) {
            pl_buf buf = db.object;
            const struct pl_buf_mtl *buf_mtl = PL_PRIV(buf);
            struct metal_slice slice = metal_cmd_copy(gpu, cmd, buf_mtl->buf.contents,
                                                      buf->params.size);
            if (!slice.buf)
                return false;
            set_buffer(enc, b->stages, slice, b->index);
        } else {
            pl_tex tex = db.object;
            struct pl_tex_mtl *tex_mtl = PL_PRIV(tex);
            set_texture(enc, b->stages, tex_mtl->tex,
                        p->samplers[db.sample_mode][db.address_mode], b);
            metal_cmd_use(cmd, &tex_mtl->last_use);
        }
    }

    return true;
}

// Binds the vertices and draws, copying host vertex and index data into the
// arena
static bool draw(pl_gpu gpu, struct metal_cmd *cmd, id<MTLRenderCommandEncoder> enc,
                 const struct pl_pass_run_params *params)
{
    static const MTLPrimitiveType prims[PL_PRIM_TYPE_COUNT] = {
        [PL_PRIM_TRIANGLE_LIST]  = MTLPrimitiveTypeTriangle,
        [PL_PRIM_TRIANGLE_STRIP] = MTLPrimitiveTypeTriangleStrip,
    };
    static const MTLIndexType index_types[PL_INDEX_FORMAT_COUNT] = {
        [PL_INDEX_UINT16] = MTLIndexTypeUInt16,
        [PL_INDEX_UINT32] = MTLIndexTypeUInt32,
    };

    struct metal_slice vertices, indices = {0};
    if (params->vertex_data) {
        vertices = metal_cmd_copy(gpu, cmd, params->vertex_data, pl_vertex_buf_size(params));
    } else {
        struct pl_buf_mtl *buf_mtl = PL_PRIV(params->vertex_buf);
        vertices = (struct metal_slice) { buf_mtl->buf, params->buf_offset };
        metal_cmd_use(cmd, &buf_mtl->last_use);
    }

    if (params->index_data) {
        indices = metal_cmd_copy(gpu, cmd, params->index_data, pl_index_buf_size(params));
    } else if (params->index_buf) {
        struct pl_buf_mtl *buf_mtl = PL_PRIV(params->index_buf);
        indices = (struct metal_slice) { buf_mtl->buf, params->index_offset };
        metal_cmd_use(cmd, &buf_mtl->last_use);
    }

    bool indexed = params->index_data || params->index_buf;
    if (!vertices.buf || (indexed && !indices.buf))
        return false;

    MTLPrimitiveType prim = prims[params->pass->params.vertex_type];
    [enc setVertexBuffer:vertices.buf offset:vertices.offset atIndex:VERTEX_BUFFER_INDEX];
    if (indexed) {
        [enc drawIndexedPrimitives:prim
                        indexCount:params->vertex_count
                         indexType:index_types[params->index_fmt]
                       indexBuffer:indices.buf
                 indexBufferOffset:indices.offset];
    } else {
        [enc drawPrimitives:prim vertexStart:0 vertexCount:params->vertex_count];
    }

    return true;
}

static void encode_pass(pl_gpu gpu, struct metal_cmd *cmd, id<MTLRenderCommandEncoder> enc,
                        const struct pl_pass_run_params *params)
{
    const struct pl_pass_mtl *pass_mtl = PL_PRIV(params->pass);
    pl_rect2d vp = params->viewport, sc = params->scissors;
    [enc setRenderPipelineState:pass_mtl->pipeline];
    [enc setViewport:(MTLViewport) {
        .originX = vp.x0, .originY = vp.y0,
        .width = pl_rect_w(vp), .height = pl_rect_h(vp),
        .znear = 0.0, .zfar = 1.0,
    }];
    [enc setScissorRect:(MTLScissorRect) {
        .x = sc.x0, .y = sc.y0,
        .width = pl_rect_w(sc), .height = pl_rect_h(sc),
    }];
    set_push_constants(enc, params->pass, params->push_constants);
    if (bind_descriptors(gpu, cmd, enc, params) && draw(gpu, cmd, enc, params))
        return;

    PL_ERR(gpu, "Metal: failed encoding a pass, dropping its draw");
}

// Waits until the GPU is done with the uniform buffers, whose contents are
// copied at encode time
static void wait_uniform_buffers(pl_gpu gpu, const struct pl_pass_run_params *params)
{
    const struct pl_pass_params *pass_params = &params->pass->params;
    for (int i = 0; i < pass_params->num_descriptors; i++) {
        if (pass_params->descriptors[i].type != PL_DESC_BUF_UNIFORM)
            continue;
        pl_buf buf = params->desc_bindings[i].object;
        struct pl_buf_mtl *buf_mtl = PL_PRIV(buf);
        while (metal_poll(gpu, &buf_mtl->last_use, UINT64_MAX))
            ;
    }
}

void metal_pass_run(pl_gpu gpu, const struct pl_pass_run_params *params)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return;

        pl_pass pass = params->pass;
        struct pl_pass_mtl *pass_mtl = PL_PRIV(pass);
        struct pl_tex_mtl *target = PL_PRIV(params->target);
        wait_uniform_buffers(gpu, params);

        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture = target->tex;
        rp.colorAttachments[0].loadAction = pass->params.load_target
                                          ? MTLLoadActionLoad : MTLLoadActionDontCare;
        rp.colorAttachments[0].storeAction = MTLStoreActionStore;

        struct metal_cmd *cmd = metal_cmd_begin(gpu);
        if (!cmd)
            return;

        id<MTLRenderCommandEncoder> enc = [cmd->buf renderCommandEncoderWithDescriptor:rp];
        encode_pass(gpu, cmd, enc, params);
        [enc endEncoding];
        metal_cmd_use(cmd, &target->last_use);
        metal_cmd_retain(cmd, pass_mtl->pipeline);
        metal_cmd_end(gpu, cmd);
    }
}
