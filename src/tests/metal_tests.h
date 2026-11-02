#pragma once

#include "gpu_tests.h"

#include <libplacebo/metal.h>

#define TEX_DIM     16
#define TEX_BYTES   (TEX_DIM * TEX_DIM * 4)

// Returns a TEX_DIM x TEX_DIM rgba8 texture that can be uploaded to,
// downloaded from, blitted and rendered to
pl_tex create_rgba8(pl_gpu gpu);
void require_rgba8(pl_gpu gpu, pl_tex tex, const uint8_t *expected);

// Transfer callback setting the bool at `priv`
void set_flag(void *priv);

// Holds back the GPU work of every command buffer committed to the queue of
// `mtl` after this call until `gate_open`, which must follow within the GPU
// timeout of a few seconds.
void *gate_close(pl_metal mtl);
void gate_open(void *gate);

// Runs every Metal test on the system default device, printing the result
// lines kerao/test.py reads. Returns 0 when all passed; any failure exits the
// process. Needs MTL_DEBUG_LAYER=1 in the environment.
int metal_tests(void);

void metal_translation_tests(pl_gpu gpu);
void metal_snapshot_tests(pl_metal mtl);
void metal_emulated_format_tests(pl_gpu gpu);

enum half_rounding {
    HALF_TOWARD_ZERO,
    HALF_NEAREST_EVEN,
};

// Returns how the device rounds floats rendered to half float targets
enum half_rounding metal_iosurface_tests(pl_gpu gpu);
