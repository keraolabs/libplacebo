#include "metal_tests.h"
#include "pl_thread.h"

#include <objc/message.h>
#include <objc/runtime.h>

// Metal feature set tables, Apple5 and later
#define APPLE5                  5
#define NEWEST_APPLE_FAMILY     11
#define LARGE_TEX_DIM_FAMILY    10
#define MAX_TEX_DIM             16384
#define MAX_TEX_DIM_LARGE       32768
#define MAX_TEX_3D_DIM          2048
#define MAX_SET_BYTES           4096

// kerao/test.py reads these lines: `validation MTL_DEBUG_LAYER=<value>` first,
// one `config <name> Apple<family>` for each configuration whose tests all
// passed, then `end <configurations>` last
#define RESULT_PREFIX "kerao-metal-test"

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

static bool is_multiple(size_t x, size_t of)
{
    return x % of == 0;
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
    REQUIRE(lim->callbacks);
    REQUIRE(lim->buf_transfer);
    REQUIRE_CMP(lim->max_mapped_size, ==, max_buf, "zu");
    bool unified = device_bool(mtl->device, "hasUnifiedMemory");
    REQUIRE_CMP(lim->host_cached, ==, unified, "d");
    REQUIRE_CMP(lim->max_mapped_vram, ==, unified ? max_buf : 0, "zu");
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

    REQUIRE(PL_ISPOT(lim->align_tex_xfer_pitch));
    REQUIRE(PL_ISPOT(lim->align_tex_xfer_offset));
    for (int i = 0; i < gpu->num_formats; i++) {
        pl_fmt fmt = gpu->formats[i];
        if (!(fmt->caps & PL_FMT_CAP_SAMPLEABLE))
            continue;
        REQUIRE(is_multiple(lim->align_tex_xfer_pitch, fmt->texel_size));
        REQUIRE(is_multiple(lim->align_tex_xfer_offset, fmt->texel_size));
    }

    REQUIRE_CMP(gpu->import_caps.tex, ==, (pl_handle_caps) PL_HANDLE_IOSURFACE, PRIx64);
    REQUIRE(!gpu->import_caps.buf && !gpu->import_caps.sync);
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

// rgba8 stores each k / 255 as k
static const float clear_color[4] = { 51 / 255.0f, 102 / 255.0f, 153 / 255.0f, 1.0f };
static const uint8_t clear_texel[4] = { 51, 102, 153, 255 };

pl_tex create_rgba8(pl_gpu gpu)
{
    pl_tex tex = pl_tex_create(gpu, pl_tex_params(
        .format         = pl_find_named_fmt(gpu, "rgba8"),
        .w              = TEX_DIM,
        .h              = TEX_DIM,
        .blit_src       = true,
        .blit_dst       = true,
        .host_writable  = true,
        .host_readable  = true,
    ));
    REQUIRE(tex);
    return tex;
}

static void fill_pattern(uint8_t *data, size_t size, int seed)
{
    for (size_t i = 0; i < size; i++)
        data[i] = (i * 7 + seed) % 251;
}

void set_flag(void *priv)
{
    bool *flag = priv;
    *flag = true;
}

void require_rgba8(pl_gpu gpu, pl_tex tex, const uint8_t *expected)
{
    uint8_t data[TEX_BYTES];
    REQUIRE(pl_tex_download(gpu, pl_tex_transfer_params(.tex = tex, .ptr = data)));
    REQUIRE_MEMEQ(data, expected, TEX_BYTES);
}

static void metal_clear_blit_tests(pl_gpu gpu)
{
    pl_tex src = create_rgba8(gpu), dst = create_rgba8(gpu);
    uint8_t pattern[TEX_BYTES], expected[TEX_BYTES];
    fill_pattern(pattern, TEX_BYTES, 1);
    for (int i = 0; i < TEX_DIM * TEX_DIM; i++)
        memcpy(&expected[i * 4], clear_texel, 4);

    pl_tex_clear(gpu, dst, clear_color);
    require_rgba8(gpu, dst, expected);

    const pl_rect3d src_rc = { .x0 = 2, .y0 = 3, .x1 = 10, .y1 = 9, .z1 = 1 };
    const pl_rect3d dst_rc = { .x0 = 5, .y0 = 4, .x1 = 13, .y1 = 10, .z1 = 1 };
    REQUIRE(pl_tex_upload(gpu, pl_tex_transfer_params(.tex = src, .ptr = pattern)));
    pl_tex_blit(gpu, pl_tex_blit_params(
        .src = src, .dst = dst,
        .src_rc = src_rc, .dst_rc = dst_rc,
    ));

    for (int y = dst_rc.y0; y < dst_rc.y1; y++) {
        for (int x = dst_rc.x0; x < dst_rc.x1; x++) {
            int sx = x - dst_rc.x0 + src_rc.x0, sy = y - dst_rc.y0 + src_rc.y0;
            memcpy(&expected[(y * TEX_DIM + x) * 4], &pattern[(sy * TEX_DIM + sx) * 4], 4);
        }
    }
    require_rgba8(gpu, dst, expected);

    pl_tex_destroy(gpu, &src);
    pl_tex_destroy(gpu, &dst);
}

static void metal_buf_transfer_tests(pl_gpu gpu)
{
    const size_t up_offset = 1, down_offset = 3, size = TEX_BYTES + down_offset;
    uint8_t pattern[TEX_BYTES], data[TEX_BYTES];
    fill_pattern(pattern, TEX_BYTES, 2);

    pl_buf up = pl_buf_create(gpu, pl_buf_params(.size = size, .host_mapped = true));
    pl_buf down = pl_buf_create(gpu, pl_buf_params(.size = size, .host_readable = true));
    REQUIRE(up && down);
    memcpy(up->data + up_offset, pattern, TEX_BYTES);

    pl_tex tex = create_rgba8(gpu);
    bool uploaded = false, downloaded = false;
    REQUIRE(pl_tex_upload(gpu, pl_tex_transfer_params(
        .tex = tex, .buf = up, .buf_offset = up_offset,
        .callback = set_flag, .priv = &uploaded,
    )));
    REQUIRE(pl_tex_download(gpu, pl_tex_transfer_params(
        .tex = tex, .buf = down, .buf_offset = down_offset,
        .callback = set_flag, .priv = &downloaded,
    )));

    pl_gpu_finish(gpu);
    REQUIRE(uploaded && downloaded);
    REQUIRE(pl_buf_read(gpu, down, down_offset, data, TEX_BYTES));
    REQUIRE_MEMEQ(data, pattern, TEX_BYTES);

    pl_tex_destroy(gpu, &tex);
    pl_buf_destroy(gpu, &up);
    pl_buf_destroy(gpu, &down);
}

#define BUF_SIZE        4096
#define UPDATE_SIZE     16
#define GATE_OPEN       1
#define POLL_SHORT_NS   1000000         // 1 ms
#define WAIT_LONG_NS    10000000000ULL  // 10 s, above the GPU timeout
#define SETTLE_S        0.02

static id msg_id(id obj, const char *sel)
{
    return ((id (*)(id, SEL)) objc_msgSend)(obj, sel_registerName(sel));
}

static void msg_void(id obj, const char *sel)
{
    ((void (*)(id, SEL)) objc_msgSend)(obj, sel_registerName(sel));
}

static void encode_wait(id cmd, id event)
{
    ((void (*)(id, SEL, id, uint64_t)) objc_msgSend)(cmd,
            sel_registerName("encodeWaitForEvent:value:"), event, GATE_OPEN);
}

void *gate_close(pl_metal mtl)
{
    id pool = msg_id(msg_id((id) objc_getClass("NSAutoreleasePool"), "alloc"), "init");
    id event = msg_id((id) mtl->device, "newSharedEvent");
    id cmd = msg_id((id) mtl->queue, "commandBuffer");
    encode_wait(cmd, event);
    msg_void(cmd, "commit");
    msg_void(pool, "drain");
    return event;
}

void gate_open(void *event)
{
    ((void (*)(id, SEL, uint64_t)) objc_msgSend)((id) event,
            sel_registerName("setSignaledValue:"), GATE_OPEN);
    msg_void((id) event, "release");
}

struct opener {
    void *gate;
    _Atomic bool opened;
};

static PL_THREAD_VOID open_gate_later(void *arg)
{
    struct opener *o = arg;
    pl_thread_sleep(SETTLE_S);
    atomic_store(&o->opened, true);
    gate_open(o->gate);
    PL_THREAD_RETURN();
}

static void metal_buf_in_use_tests(pl_metal mtl)
{
    pl_gpu gpu = mtl->gpu;
    uint8_t pattern[BUF_SIZE], update[UPDATE_SIZE], data[BUF_SIZE];
    fill_pattern(pattern, BUF_SIZE, 3);
    fill_pattern(update, UPDATE_SIZE, 4);

    pl_buf src = pl_buf_create(gpu, pl_buf_params(.size = BUF_SIZE, .initial_data = pattern));
    pl_buf dst = pl_buf_create(gpu, pl_buf_params(
        .size = BUF_SIZE, .host_writable = true, .host_readable = true,
    ));
    REQUIRE(src && dst);
    REQUIRE(!pl_buf_poll(gpu, dst, 0));

    struct opener opener = { .gate = gate_close(mtl) };
    pl_buf_copy(gpu, dst, 0, src, 0, BUF_SIZE);
    REQUIRE(pl_buf_poll(gpu, dst, 0));
    REQUIRE(pl_buf_poll(gpu, dst, POLL_SHORT_NS));

    pl_thread thread;
    REQUIRE(pl_thread_create(&thread, open_gate_later, &opener) == 0);
    pl_buf_write(gpu, dst, 0, update, UPDATE_SIZE);
    REQUIRE(atomic_load(&opener.opened));
    REQUIRE(!pl_buf_poll(gpu, dst, 0));
    pl_thread_join(thread);

    memcpy(pattern, update, UPDATE_SIZE);
    REQUIRE(pl_buf_read(gpu, dst, 0, data, BUF_SIZE));
    REQUIRE_MEMEQ(data, pattern, BUF_SIZE);

    pl_buf_destroy(gpu, &src);
    pl_buf_destroy(gpu, &dst);
}

static void metal_destroy_in_use_tests(pl_metal mtl)
{
    pl_gpu gpu = mtl->gpu;
    uint8_t pattern[TEX_BYTES];
    fill_pattern(pattern, TEX_BYTES, 5);
    pl_tex src = create_rgba8(gpu), dst = create_rgba8(gpu);
    pl_buf marker = pl_buf_create(gpu, pl_buf_params(.size = BUF_SIZE, .host_readable = true));
    REQUIRE(marker);

    void *gate = gate_close(mtl);
    REQUIRE(pl_tex_upload(gpu, pl_tex_transfer_params(.tex = src, .ptr = pattern)));
    pl_tex_blit(gpu, pl_tex_blit_params(.src = src, .dst = dst));
    REQUIRE(pl_tex_download(gpu, pl_tex_transfer_params(.tex = src, .buf = marker)));
    pl_gpu_flush(gpu);

    pl_tex_destroy(gpu, &src);
    REQUIRE(pl_buf_poll(gpu, marker, 0));

    gate_open(gate);
    pl_gpu_finish(gpu);
    REQUIRE(!pl_buf_poll(gpu, marker, 0));
    require_rgba8(gpu, dst, pattern);

    pl_tex_destroy(gpu, &dst);
    pl_buf_destroy(gpu, &marker);
}

enum waiter_state {
    WAITER_STARTING,
    WAITER_WAITING,
    WAITER_DONE,
};

// Blocks in `pl_tex_poll` on `tex`, or in `pl_gpu_finish` if `tex` is NULL
struct waiter {
    pl_gpu gpu;
    pl_tex tex;
    _Atomic int state;
    bool in_use;
};

static PL_THREAD_VOID wait_thread(void *arg)
{
    struct waiter *w = arg;
    atomic_store(&w->state, WAITER_WAITING);
    if (w->tex) {
        w->in_use = pl_tex_poll(w->gpu, w->tex, WAIT_LONG_NS);
    } else {
        pl_gpu_finish(w->gpu);
    }
    atomic_store(&w->state, WAITER_DONE);
    PL_THREAD_RETURN();
}

static void metal_concurrency_tests(pl_metal mtl)
{
    pl_gpu gpu = mtl->gpu;
    uint8_t pattern[TEX_BYTES], cleared[TEX_BYTES];
    fill_pattern(pattern, TEX_BYTES, 6);
    for (int i = 0; i < TEX_DIM * TEX_DIM; i++)
        memcpy(&cleared[i * 4], clear_texel, 4);
    pl_tex presented = create_rgba8(gpu), src = create_rgba8(gpu), dst = create_rgba8(gpu);

    void *gate = gate_close(mtl);
    pl_tex_clear(gpu, presented, clear_color);
    pl_gpu_flush(gpu);

    struct waiter waiters[] = {
        { .gpu = gpu, .tex = presented },
        { .gpu = gpu },
    };
    pl_thread threads[PL_ARRAY_SIZE(waiters)];
    for (int i = 0; i < PL_ARRAY_SIZE(waiters); i++) {
        REQUIRE(pl_thread_create(&threads[i], wait_thread, &waiters[i]) == 0);
        while (atomic_load(&waiters[i].state) == WAITER_STARTING)
            pl_thread_sleep(SETTLE_S);
    }
    pl_thread_sleep(SETTLE_S);

    pl_tex_clear(gpu, dst, clear_color);
    REQUIRE(pl_tex_upload(gpu, pl_tex_transfer_params(.tex = src, .ptr = pattern)));
    pl_tex_blit(gpu, pl_tex_blit_params(.src = src, .dst = dst));
    pl_gpu_flush(gpu);
    for (int i = 0; i < PL_ARRAY_SIZE(waiters); i++)
        REQUIRE_CMP(atomic_load(&waiters[i].state), ==, WAITER_WAITING, "d");

    gate_open(gate);
    for (int i = 0; i < PL_ARRAY_SIZE(waiters); i++)
        pl_thread_join(threads[i]);
    REQUIRE(!waiters[0].in_use);
    require_rgba8(gpu, presented, cleared);
    require_rgba8(gpu, dst, pattern);

    pl_tex_destroy(gpu, &presented);
    pl_tex_destroy(gpu, &src);
    pl_tex_destroy(gpu, &dst);
}

static char stall_queue_key, stall_event_key;

static id stalled_command_buffer(id self, SEL sel)
{
    id cmd = msg_id(objc_getAssociatedObject(self, &stall_queue_key), "commandBuffer");
    encode_wait(cmd, objc_getAssociatedObject(self, &stall_event_key));
    return cmd;
}

static id forward_to_queue(id self, SEL sel, SEL target)
{
    return objc_getAssociatedObject(self, &stall_queue_key);
}

// Returns a retained stand-in for a new command queue of `device`, whose
// command buffers wait for an event that is never signalled, so that the GPU
// times them out and completes them with an error.
static void *stalling_queue(void *device)
{
    const char *name = "PLTestStallingQueue";
    Class cls = objc_getClass(name);
    if (!cls) {
        cls = objc_allocateClassPair(objc_getClass("NSObject"), name, 0);
        class_addMethod(cls, sel_registerName("commandBuffer"),
                        (IMP) stalled_command_buffer, "@@:");
        class_addMethod(cls, sel_registerName("forwardingTargetForSelector:"),
                        (IMP) forward_to_queue, "@@::");
        objc_registerClassPair(cls);
    }

    id queue = msg_id((id) device, "newCommandQueue");
    id event = msg_id((id) device, "newSharedEvent");
    id stalling = msg_id(msg_id((id) cls, "alloc"), "init");
    objc_setAssociatedObject(stalling, &stall_queue_key, queue, OBJC_ASSOCIATION_RETAIN);
    objc_setAssociatedObject(stalling, &stall_event_key, event, OBJC_ASSOCIATION_RETAIN);
    msg_void(queue, "release");
    msg_void(event, "release");
    return stalling;
}

static void count_errors(void *priv, enum pl_log_level level, const char *msg)
{
    _Atomic int *errors = priv;
    if (level == PL_LOG_ERR)
        atomic_fetch_add(errors, 1);
    pl_log_timestamp(stdout, level, msg);
}

static void metal_failure_tests(void *device)
{
    _Atomic int errors = 0;
    pl_log log = pl_log_create(PL_API_VER, pl_log_params(
        .log_cb = count_errors,
        .log_priv = &errors,
        .log_level = PL_LOG_DEBUG,
    ));

    void *queue = stalling_queue(device);
    pl_metal mtl = pl_metal_create(log, pl_metal_params(.device = device, .queue = queue));
    msg_void((id) queue, "release");
    REQUIRE(mtl);
    pl_gpu gpu = mtl->gpu;

    pl_tex tex = create_rgba8(gpu);
    pl_tex_clear(gpu, tex, clear_color);
    REQUIRE(!pl_gpu_is_failed(gpu));
    REQUIRE_CMP(atomic_load(&errors), ==, 0, "d");

    pl_gpu_finish(gpu);
    REQUIRE(pl_gpu_is_failed(gpu));
    REQUIRE_CMP(atomic_load(&errors), >=, 1, "d");
    REQUIRE(!pl_tex_poll(gpu, tex, 0));

    uint8_t data[TEX_BYTES] = {0};
    REQUIRE(!pl_tex_upload(gpu, pl_tex_transfer_params(.tex = tex, .ptr = data)));
    REQUIRE(!pl_tex_download(gpu, pl_tex_transfer_params(.tex = tex, .ptr = data)));
    REQUIRE(!pl_tex_create(gpu, &tex->params));
    REQUIRE(!pl_buf_create(gpu, pl_buf_params(.size = BUF_SIZE, .host_readable = true)));
    pl_tex_clear(gpu, tex, clear_color);
    pl_gpu_flush(gpu);
    pl_gpu_finish(gpu);
    REQUIRE(pl_gpu_is_failed(gpu));

    pl_tex_destroy(gpu, &tex);
    pl_metal_destroy(&mtl);
    pl_log_destroy(&log);
}

static void metal_teardown(pl_metal *mtl)
{
    REQUIRE(!pl_gpu_is_failed((*mtl)->gpu));
    pl_metal_destroy(mtl);
    REQUIRE(!*mtl);
}

int metal_tests(void)
{
    const char *validation = getenv("MTL_DEBUG_LAYER");
    REQUIRE(validation && strcmp(validation, "1") == 0);

    pl_log log = pl_test_logger();
    printf(RESULT_PREFIX " validation MTL_DEBUG_LAYER=%s\n", validation);

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

    const struct {
        const char *name;
        pl_metal mtl;
        int apple_family;
    } configs[] = {
        { "native", native, native_exp.apple_family },
        { "Apple5", apple5, APPLE5 },
    };

    const char *rounding_names[] = {
        [HALF_TOWARD_ZERO]  = "toward zero",
        [HALF_NEAREST_EVEN] = "to nearest even",
    };
    enum half_rounding rounding = HALF_TOWARD_ZERO;
    for (int i = 0; i < PL_ARRAY_SIZE(configs); i++) {
        pl_metal mtl = configs[i].mtl;
        pl_gpu gpu = mtl->gpu;
        metal_translation_tests(gpu);
        metal_snapshot_tests(mtl);
        metal_emulated_format_tests(gpu);
        gpu_shader_tests(gpu);
        metal_clear_blit_tests(gpu);
        metal_buf_transfer_tests(gpu);
        metal_buf_in_use_tests(mtl);
        metal_destroy_in_use_tests(mtl);
        metal_concurrency_tests(mtl);
        enum half_rounding config_rounding = metal_iosurface_tests(gpu);
        REQUIRE(i == 0 || config_rounding == rounding);
        rounding = config_rounding;
        printf("RGhA render targets round %s\n", rounding_names[rounding]);
        REQUIRE(!pl_gpu_is_failed(gpu));
        printf(RESULT_PREFIX " config %s Apple%d\n", configs[i].name,
               configs[i].apple_family);
    }

    metal_failure_tests(native->device);

    metal_teardown(&apple5);
    metal_teardown(&native);
    pl_log_destroy(&log);
    printf(RESULT_PREFIX " end %d\n", (int) PL_ARRAY_SIZE(configs));
    return 0;
}
