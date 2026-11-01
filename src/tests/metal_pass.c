#include "metal_tests.h"

#define SNAPSHOT_RUNS   4
#define UBO_SIZE        16
#define UBO_BINDING     0
#define TEX_BINDING     1

// Each channel comes from a different per-run input: red from a push
// constant, green from a uniform buffer, blue from vertex data and alpha from
// a sampled texture
static const char *snapshot_vert =
    "#version 450                                       \n"
    "layout(location=0) in vec2 pos;                    \n"
    "layout(location=1) in float blue;                  \n"
    "layout(location=0) out float frag_blue;            \n"
    "void main() {                                      \n"
    "    gl_Position = vec4(pos, 0.0, 1.0);             \n"
    "    frag_blue = blue;                              \n"
    "}";

static const char *snapshot_frag =
    "#version 450                                       \n"
    "layout(std430, push_constant) uniform PushC {      \n"
    "    float red;                                     \n"
    "};                                                 \n"
    "layout(std140, binding=0) uniform UBO {            \n"
    "    float green;                                   \n"
    "};                                                 \n"
    "layout(binding=1) uniform sampler2D tex;           \n"
    "layout(location=0) in float frag_blue;             \n"
    "layout(location=0) out vec4 out_color;             \n"
    "void main() {                                      \n"
    "    float alpha = texture(tex, vec2(0.5)).a;       \n"
    "    out_color = vec4(red, green, frag_blue, alpha);\n"
    "}";

struct snapshot_vertex {
    float pos[2];
    float blue;
};

struct pl_pass_params snapshot_pass_params(pl_gpu gpu)
{
    static struct pl_desc descs[] = {
        { .name = "UBO", .type = PL_DESC_BUF_UNIFORM, .binding = UBO_BINDING },
        { .name = "tex", .type = PL_DESC_SAMPLED_TEX, .binding = TEX_BINDING },
    };
    static struct pl_vertex_attrib attribs[2];
    attribs[0] = (struct pl_vertex_attrib) {
        .name     = "pos",
        .fmt      = pl_find_vertex_fmt(gpu, PL_FMT_FLOAT, 2),
        .offset   = offsetof(struct snapshot_vertex, pos),
        .location = 0,
    };
    attribs[1] = (struct pl_vertex_attrib) {
        .name     = "blue",
        .fmt      = pl_find_vertex_fmt(gpu, PL_FMT_FLOAT, 1),
        .offset   = offsetof(struct snapshot_vertex, blue),
        .location = 1,
    };

    return (struct pl_pass_params) {
        .type               = PL_PASS_RASTER,
        .glsl_shader        = snapshot_frag,
        .vertex_shader      = snapshot_vert,
        .target_format      = pl_find_named_fmt(gpu, "rgba8"),
        .vertex_type        = PL_PRIM_TRIANGLE_STRIP,
        .vertex_stride      = sizeof(struct snapshot_vertex),
        .num_vertex_attribs = PL_ARRAY_SIZE(attribs),
        .vertex_attribs     = attribs,
        .num_descriptors    = PL_ARRAY_SIZE(descs),
        .descriptors        = descs,
        .push_constants_size = sizeof(float),
    };
}

static void set_quad(struct snapshot_vertex quad[4], float blue)
{
    const float corners[4][2] = { {-1, -1}, {1, -1}, {-1, 1}, {1, 1} };
    for (int i = 0; i < 4; i++) {
        quad[i] = (struct snapshot_vertex) {
            .pos = { corners[i][0], corners[i][1] },
            .blue = blue,
        };
    }
}

static uint8_t snapshot_channel(int run, int channel)
{
    return 40 * channel + 10 * run + 5;
}

static pl_tex create_alpha_tex(pl_gpu gpu, uint8_t alpha)
{
    const uint8_t texel[4] = { 0, 0, 0, alpha };
    pl_tex tex = pl_tex_create(gpu, pl_tex_params(
        .format         = pl_find_named_fmt(gpu, "rgba8"),
        .w              = 1,
        .h              = 1,
        .sampleable     = true,
        .initial_data   = texel,
    ));
    REQUIRE(tex);
    return tex;
}

void metal_snapshot_tests(pl_metal mtl)
{
    pl_gpu gpu = mtl->gpu;
    struct pl_pass_params params = snapshot_pass_params(gpu);
    pl_pass pass = pl_pass_create(gpu, &params);
    REQUIRE(pass);
    pl_buf ubo = pl_buf_create(gpu, pl_buf_params(
        .size = UBO_SIZE, .uniform = true, .host_writable = true,
    ));
    REQUIRE(ubo);

    pl_tex targets[SNAPSHOT_RUNS], textures[SNAPSHOT_RUNS];
    for (int i = 0; i < SNAPSHOT_RUNS; i++) {
        targets[i] = create_rgba8(gpu);
        textures[i] = create_alpha_tex(gpu, snapshot_channel(i, 3));
    }
    pl_gpu_finish(gpu);

    float push_constant, uniform[UBO_SIZE / sizeof(float)] = {0};
    struct snapshot_vertex quad[4];
    struct pl_desc_binding bindings[2];
    void *gate = gate_close(mtl);
    for (int i = 0; i < SNAPSHOT_RUNS; i++) {
        push_constant = snapshot_channel(i, 0) / 255.0f;
        uniform[0] = snapshot_channel(i, 1) / 255.0f;
        pl_buf_write(gpu, ubo, 0, uniform, sizeof(uniform));
        set_quad(quad, snapshot_channel(i, 2) / 255.0f);
        bindings[0] = (struct pl_desc_binding) { .object = ubo };
        bindings[1] = (struct pl_desc_binding) { .object = textures[i] };
        pl_pass_run(gpu, pl_pass_run_params(
            .pass           = pass,
            .target         = targets[i],
            .push_constants = &push_constant,
            .desc_bindings  = bindings,
            .vertex_data    = quad,
            .vertex_count   = PL_ARRAY_SIZE(quad),
        ));
    }

    for (int i = 0; i < SNAPSHOT_RUNS; i++)
        REQUIRE(pl_tex_poll(gpu, targets[i], 0));
    gate_open(gate);

    for (int i = 0; i < SNAPSHOT_RUNS; i++) {
        uint8_t expected[TEX_BYTES];
        for (int t = 0; t < TEX_DIM * TEX_DIM; t++) {
            for (int c = 0; c < 4; c++)
                expected[t * 4 + c] = snapshot_channel(i, c);
        }
        require_rgba8(gpu, targets[i], expected);
        pl_tex_destroy(gpu, &targets[i]);
        pl_tex_destroy(gpu, &textures[i]);
    }

    pl_buf_destroy(gpu, &ubo);
    pl_pass_destroy(gpu, &pass);
}

#define MSL_MAX_MAJOR 3
#define MSL_MAX_MINOR 1
#define MSL_VERSION_FORMAT "MSL %d.%d"

static const char *invalid_shader =
    "#version 450                                       \n"
    "void main() { undeclared = 1.0; }";

struct translation_log {
    int glslang;
    int spirv_cross;
    int errors;
    int msl_major;
    int msl_minor;
};

static void count_translations(void *priv, enum pl_log_level level, const char *msg)
{
    struct translation_log *t = priv;
    if (level == PL_LOG_ERR)
        t->errors++;
    if (level == PL_LOG_DEBUG && strstr(msg, "with glslang"))
        t->glslang++;
    if (level == PL_LOG_DEBUG && strstr(msg, "with SPIRV-Cross")) {
        const char *msl = strstr(msg, "MSL ");
        int parsed = msl ? sscanf(msl, MSL_VERSION_FORMAT, &t->msl_major, &t->msl_minor) : 0;
        REQUIRE_CMP(parsed, ==, 2, "d");
        t->spirv_cross++;
    }
    pl_log_timestamp(stdout, level, msg);
}

void metal_translation_tests(pl_gpu gpu)
{
    struct translation_log t = {0};
    struct pl_log_params old = pl_log_update(gpu->log, pl_log_params(
        .log_cb = count_translations,
        .log_priv = &t,
        .log_level = PL_LOG_DEBUG,
    ));

    struct pl_pass_params params = snapshot_pass_params(gpu);
    pl_pass pass = pl_pass_create(gpu, &params);
    REQUIRE(pass);
    REQUIRE_CMP(t.glslang, ==, 2, "d");
    REQUIRE_CMP(t.spirv_cross, ==, 2, "d");
    REQUIRE_CMP(t.errors, ==, 0, "d");
    REQUIRE(t.msl_major > 0);
    REQUIRE(t.msl_major < MSL_MAX_MAJOR ||
            (t.msl_major == MSL_MAX_MAJOR && t.msl_minor <= MSL_MAX_MINOR));

    pl_tex target = create_rgba8(gpu), tex = create_alpha_tex(gpu, 0);
    pl_buf ubo = pl_buf_create(gpu, pl_buf_params(.size = UBO_SIZE, .uniform = true));
    REQUIRE(ubo);
    struct snapshot_vertex quad[4];
    set_quad(quad, 0.0f);
    float push_constant = 0.0f;
    pl_pass_run(gpu, pl_pass_run_params(
        .pass           = pass,
        .target         = target,
        .push_constants = &push_constant,
        .desc_bindings  = (struct pl_desc_binding[]) {{ .object = ubo }, { .object = tex }},
        .vertex_data    = quad,
        .vertex_count   = PL_ARRAY_SIZE(quad),
    ));
    pl_gpu_finish(gpu);
    REQUIRE_CMP(t.glslang, ==, 2, "d");
    REQUIRE_CMP(t.spirv_cross, ==, 2, "d");
    pl_pass_destroy(gpu, &pass);

    struct pl_pass_params invalid = params;
    invalid.vertex_shader = invalid_shader;
    invalid.glsl_shader = invalid_shader;
    REQUIRE(!pl_pass_create(gpu, &invalid));
    REQUIRE_CMP(t.glslang, >, 2, "d");
    REQUIRE_CMP(t.spirv_cross, ==, 2, "d");
    REQUIRE_CMP(t.errors, >, 0, "d");

    struct pl_pass_params unlinkable = params;
    unlinkable.num_vertex_attribs = 1;
    int glslang = t.glslang, errors = t.errors;
    REQUIRE(!pl_pass_create(gpu, &unlinkable));
    REQUIRE_CMP(t.glslang, ==, glslang + 2, "d");
    REQUIRE_CMP(t.spirv_cross, ==, 4, "d");
    REQUIRE_CMP(t.errors, >, errors, "d");

    pl_log_update(gpu->log, &old);
    pl_tex_destroy(gpu, &target);
    pl_tex_destroy(gpu, &tex);
    pl_buf_destroy(gpu, &ubo);
}

#define EMU_W           4
#define EMU_H           2
#define EMU_COMPS       4
#define EMU_ROW_PAD     16
#define EMU_FLOATS      (EMU_W * EMU_H * EMU_COMPS)
#define ONE_THIRD_HALF  0.333251953125f // 1/3 rounded to the nearest half float

static const char *fetch_vert =
    "#version 450                                       \n"
    "layout(location=0) in vec2 pos;                    \n"
    "void main() {                                      \n"
    "    gl_Position = vec4(pos, 0.0, 1.0);             \n"
    "}";

static const char *fetch_frag =
    "#version 450                                       \n"
    "layout(binding=0) uniform sampler2D tex;           \n"
    "layout(location=0) out vec4 out_color;             \n"
    "void main() {                                      \n"
    "    out_color = texelFetch(tex, ivec2(gl_FragCoord.xy), 0);\n"
    "}";

// Renders the texels of `tex` into a float32 target and downloads them
static void fetch_texels(pl_gpu gpu, pl_tex tex, float *out)
{
    pl_fmt rgba32f = pl_find_named_fmt(gpu, "rgba32f");
    pl_tex target = pl_tex_create(gpu, pl_tex_params(
        .format         = rgba32f,
        .w              = tex->params.w,
        .h              = tex->params.h,
        .renderable     = true,
        .host_readable  = true,
    ));
    REQUIRE(target);

    struct pl_desc desc = { .name = "tex", .type = PL_DESC_SAMPLED_TEX };
    pl_pass pass = pl_pass_create(gpu, &(struct pl_pass_params) {
        .type               = PL_PASS_RASTER,
        .glsl_shader        = fetch_frag,
        .vertex_shader      = fetch_vert,
        .target_format      = rgba32f,
        .vertex_type        = PL_PRIM_TRIANGLE_STRIP,
        .vertex_stride      = sizeof(struct snapshot_vertex),
        .num_vertex_attribs = 1,
        .vertex_attribs     = &(struct pl_vertex_attrib) {
            .name = "pos",
            .fmt  = pl_find_vertex_fmt(gpu, PL_FMT_FLOAT, 2),
        },
        .num_descriptors    = 1,
        .descriptors        = &desc,
    });
    REQUIRE(pass);

    struct snapshot_vertex quad[4];
    set_quad(quad, 0.0f);
    pl_pass_run(gpu, pl_pass_run_params(
        .pass           = pass,
        .target         = target,
        .desc_bindings  = &(struct pl_desc_binding) { .object = tex },
        .vertex_data    = quad,
        .vertex_count   = PL_ARRAY_SIZE(quad),
    ));
    REQUIRE(pl_tex_download(gpu, pl_tex_transfer_params(.tex = target, .ptr = out)));

    pl_pass_destroy(gpu, &pass);
    pl_tex_destroy(gpu, &target);
}

void metal_emulated_format_tests(pl_gpu gpu)
{
    const enum pl_fmt_caps lut_caps = PL_FMT_CAP_SAMPLEABLE | PL_FMT_CAP_LINEAR;
    REQUIRE(pl_find_fmt(gpu, PL_FMT_FLOAT, EMU_COMPS, 16, 32, lut_caps));

    pl_fmt fmt = pl_find_named_fmt(gpu, "rgba16f");
    REQUIRE(fmt);
    REQUIRE(fmt->emulated);
    REQUIRE_CMP(fmt->caps, ==, lut_caps, "x");
    REQUIRE_CMP(fmt->texel_size, ==, EMU_COMPS * sizeof(float), "zu");
    REQUIRE_CMP(fmt->host_bits[0], ==, 32, "d");

    float data[EMU_FLOATS], expected[EMU_FLOATS], out[EMU_FLOATS];
    for (int i = 0; i < EMU_FLOATS; i++)
        data[i] = expected[i] = (i - EMU_FLOATS / 2) * 0.25f;
    data[1] = 1.0f / 3.0f;
    expected[1] = ONE_THIRD_HALF;

    pl_tex initial = pl_tex_create(gpu, pl_tex_params(
        .format         = fmt,
        .w              = EMU_W,
        .h              = EMU_H,
        .sampleable     = true,
        .initial_data   = data,
    ));
    REQUIRE(initial);
    fetch_texels(gpu, initial, out);
    REQUIRE_MEMEQ(out, expected, sizeof(out));

    const size_t row = EMU_W * fmt->texel_size, pitch = row + EMU_ROW_PAD;
    uint8_t padded[EMU_H * (EMU_W * EMU_COMPS * sizeof(float) + EMU_ROW_PAD)];
    for (int y = 0; y < EMU_H; y++)
        memcpy(&padded[y * pitch], (uint8_t *) data + y * row, row);

    pl_tex uploaded = pl_tex_create(gpu, pl_tex_params(
        .format         = fmt,
        .w              = EMU_W,
        .h              = EMU_H,
        .sampleable     = true,
        .host_writable  = true,
    ));
    REQUIRE(uploaded);
    bool done = false;
    REQUIRE(pl_tex_upload(gpu, pl_tex_transfer_params(
        .tex        = uploaded,
        .ptr        = padded,
        .row_pitch  = pitch,
        .callback   = set_flag,
        .priv       = &done,
    )));
    memset(padded, 0, sizeof(padded));
    fetch_texels(gpu, uploaded, out);
    REQUIRE_MEMEQ(out, expected, sizeof(out));
    pl_gpu_finish(gpu);
    REQUIRE(done);

    pl_tex_destroy(gpu, &initial);
    pl_tex_destroy(gpu, &uploaded);
}
