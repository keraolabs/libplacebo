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

#import <Metal/Metal.h>

#include "../gpu.h"
#include "../glsl/utils.h"
#include "msl_target.h"

// Buffer argument table layout, the same in both stages
#define VERTEX_BUFFER_INDEX 0
#define PUSHC_BUFFER_INDEX  1
#define FIRST_UBO_INDEX     2

// Raster pass shader stages, indexed by `enum glsl_shader_stage`
#define METAL_STAGES 2

enum metal_stage_bits {
    STAGE_VERTEX   = 1 << GLSL_SHADER_VERTEX,
    STAGE_FRAGMENT = 1 << GLSL_SHADER_FRAGMENT,
    STAGE_ALL      = STAGE_VERTEX | STAGE_FRAGMENT,
};

// MSL argument table entries of a descriptor, and the stages using it
struct metal_binding {
    int index;   // buffer or texture index
    int sampler; // sampler index of a sampled texture
    enum metal_stage_bits stages;
};

// The compiled shaders of a raster pass, with the stages using its push
// constants and the argument table entries of its descriptors
struct metal_shaders {
    id<MTLFunction> functions[METAL_STAGES];
    struct metal_binding *bindings; // one per `pl_pass_params.descriptors` entry
    enum metal_stage_bits pushc_stages;
};

// Creates the shader compiler, or logs an error and returns false.
// `metal_msl_uninit` is safe after a failure.
bool metal_msl_init(pl_gpu gpu);
void metal_msl_uninit(pl_gpu gpu);

// Compiles the shaders of `params` from MSL taken from the `pl_gpu`'s cache,
// or translated from their GLSL and then cached. `bindings` is allocated on
// `alloc`. Logs an error and returns false on failure.
bool metal_compile_shaders(pl_gpu gpu, const struct pl_pass_params *params,
                           void *alloc, struct metal_shaders *out);
