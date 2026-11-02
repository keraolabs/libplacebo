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

#include <libplacebo/gpu.h>

enum metal_platform {
    METAL_PLATFORM_MACOS,
    METAL_PLATFORM_IOS,
};

// What a Metal `pl_gpu` translates shaders into: MSL `major.minor` for
// `platform`, with the glslang and SPIRV-Cross builds `compiler` names. All
// of it is part of the cache key of the MSL.
struct metal_msl_target {
    enum metal_platform platform;
    int major;
    int minor;
    const char *compiler;
};

struct metal_msl_target metal_get_msl_target(pl_gpu gpu);

// For testing only: sets the target of the passes created afterwards. Must
// not run concurrently with pass creation, and `compiler` must outlive `gpu`.
void metal_set_msl_target(pl_gpu gpu, struct metal_msl_target target);
