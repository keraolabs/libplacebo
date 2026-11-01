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

#include "formats.h"

static MTLTextureType texture_type(const struct pl_tex_params *params)
{
    switch (pl_tex_params_dimension(*params)) {
    case 1: return MTLTextureType1D;
    case 2: return MTLTextureType2D;
    case 3: return MTLTextureType3D;
    }

    pl_unreachable();
}

static MTLTextureUsage texture_usage(const struct pl_tex_params *params)
{
    MTLTextureUsage usage = 0;
    if (params->sampleable)
        usage |= MTLTextureUsageShaderRead;
    if (params->renderable)
        usage |= MTLTextureUsageRenderTarget;
    return usage ? usage : MTLTextureUsageShaderRead;
}

static MTLOrigin rect_origin(pl_rect3d rc)
{
    return MTLOriginMake(rc.x0, rc.y0, rc.z0);
}

static MTLSize rect_size(pl_rect3d rc)
{
    return MTLSizeMake(pl_rect_w(rc), pl_rect_h(rc), pl_rect_d(rc));
}

// Copies between the texture region of `params` and `buf` at `offset`, with
// the pitches of `params`
static void encode_tex_copy(struct metal_cmd *cmd, const struct pl_tex_transfer_params *params,
                            id<MTLBuffer> buf, size_t offset, bool upload)
{
    pl_tex tex = params->tex;
    struct pl_tex_mtl *tex_mtl = PL_PRIV(tex);
    int dims = pl_tex_params_dimension(tex->params);
    size_t row_pitch = dims > 1 ? params->row_pitch
                                : pl_rect_w(params->rc) * tex->params.format->texel_size;
    size_t image_pitch = dims > 2 ? params->depth_pitch : 0;

    id<MTLBlitCommandEncoder> enc = [cmd->buf blitCommandEncoder];
    if (upload) {
        [enc copyFromBuffer:buf
               sourceOffset:offset
          sourceBytesPerRow:row_pitch
        sourceBytesPerImage:image_pitch
                 sourceSize:rect_size(params->rc)
                  toTexture:tex_mtl->tex
           destinationSlice:0
           destinationLevel:0
          destinationOrigin:rect_origin(params->rc)];
    } else {
        [enc copyFromTexture:tex_mtl->tex
                 sourceSlice:0
                 sourceLevel:0
                sourceOrigin:rect_origin(params->rc)
                  sourceSize:rect_size(params->rc)
                    toBuffer:buf
           destinationOffset:offset
      destinationBytesPerRow:row_pitch
    destinationBytesPerImage:image_pitch];
    }
    [enc endEncoding];
}

static bool upload_initial_data(pl_gpu gpu, pl_tex tex, const void *data)
{
    struct pl_tex_mtl *tex_mtl = PL_PRIV(tex);
    struct pl_tex_transfer_params params = {
        .tex = tex,
        .rc = {
            .x1 = tex->params.w,
            .y1 = PL_MAX(tex->params.h, 1),
            .z1 = PL_MAX(tex->params.d, 1),
        },
        .row_pitch = tex->params.w * tex->params.format->texel_size,
    };
    params.depth_pitch = pl_rect_h(params.rc) * params.row_pitch;

    size_t size = pl_tex_transfer_size(&params);
    id<MTLBuffer> staging = metal_new_buffer(gpu, data, size, MTLResourceStorageModeShared);
    if (!staging)
        return false;

    struct metal_cmd *cmd = metal_cmd_begin(gpu);
    if (!cmd)
        return false;

    encode_tex_copy(cmd, &params, staging, 0, true);
    metal_cmd_use(cmd, &tex_mtl->last_use);
    metal_cmd_end(gpu, cmd);
    return true;
}

void metal_tex_destroy(pl_gpu gpu, pl_tex tex)
{
    @autoreleasepool {
        struct pl_tex_mtl *tex_mtl = PL_PRIV(tex);
        tex_mtl->tex = nil;
        pl_free((void *) tex);
    }
}

pl_tex metal_tex_create(pl_gpu gpu, const struct pl_tex_params *params)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return NULL;

        const struct metal_format *mfmt = metal_format(params->format);
        if (mfmt->pixel == MTLPixelFormatInvalid) {
            PL_ERR(gpu, "Metal: format '%s' is not a texture format",
                   params->format->name);
            return NULL;
        }

        struct pl_tex_t *tex = pl_zalloc_obj(NULL, tex, struct pl_tex_mtl);
        tex->params = *params;
        tex->params.initial_data = NULL;
        tex->params.sampleable |= params->blit_src;
        tex->params.renderable |= params->blit_dst;
        tex->sampler_type = PL_SAMPLER_NORMAL;
        struct pl_tex_mtl *tex_mtl = PL_PRIV(tex);

        MTLTextureDescriptor *desc = [[MTLTextureDescriptor alloc] init];
        desc.textureType = texture_type(params);
        desc.pixelFormat = mfmt->pixel;
        desc.width = params->w;
        desc.height = PL_MAX(params->h, 1);
        desc.depth = PL_MAX(params->d, 1);
        desc.storageMode = MTLStorageModePrivate;
        desc.usage = texture_usage(&tex->params);

        struct pl_mtl *p = PL_PRIV(gpu);
        tex_mtl->tex = [p->device newTextureWithDescriptor:desc];
        if (!tex_mtl->tex) {
            PL_ERR(gpu, "Metal: failed creating a %dx%dx%d '%s' texture",
                   params->w, params->h, params->d, params->format->name);
            goto error;
        }

        if (params->initial_data && !upload_initial_data(gpu, tex, params->initial_data))
            goto error;

        return tex;

error:
        metal_tex_destroy(gpu, tex);
        return NULL;
    }
}

void metal_tex_clear_ex(pl_gpu gpu, pl_tex tex, const union pl_clear_color color)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return;

        struct pl_tex_mtl *tex_mtl = PL_PRIV(tex);
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = tex_mtl->tex;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor =
            MTLClearColorMake(color.f[0], color.f[1], color.f[2], color.f[3]);

        struct metal_cmd *cmd = metal_cmd_begin(gpu);
        if (!cmd)
            return;

        [[cmd->buf renderCommandEncoderWithDescriptor:pass] endEncoding];
        metal_cmd_use(cmd, &tex_mtl->last_use);
        metal_cmd_end(gpu, cmd);
    }
}

void metal_tex_blit(pl_gpu gpu, const struct pl_tex_blit_params *params)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return;

        pl_rect3d src_rc = params->src_rc, dst_rc = params->dst_rc;
        bool copy = params->src->params.format == params->dst->params.format &&
                    pl_rect_w(src_rc) == pl_rect_w(dst_rc) &&
                    pl_rect_h(src_rc) == pl_rect_h(dst_rc) &&
                    pl_rect_d(src_rc) == pl_rect_d(dst_rc);
        if (!copy) {
            pl_tex_blit_raster(gpu, params);
            return;
        }

        pl_rect3d_normalize(&src_rc);
        pl_rect3d_normalize(&dst_rc);
        struct pl_tex_mtl *src_mtl = PL_PRIV(params->src);
        struct pl_tex_mtl *dst_mtl = PL_PRIV(params->dst);
        struct metal_cmd *cmd = metal_cmd_begin(gpu);
        if (!cmd)
            return;

        id<MTLBlitCommandEncoder> enc = [cmd->buf blitCommandEncoder];
        [enc copyFromTexture:src_mtl->tex
                 sourceSlice:0
                 sourceLevel:0
                sourceOrigin:rect_origin(src_rc)
                  sourceSize:rect_size(src_rc)
                   toTexture:dst_mtl->tex
            destinationSlice:0
            destinationLevel:0
           destinationOrigin:rect_origin(dst_rc)];
        [enc endEncoding];
        metal_cmd_use(cmd, &src_mtl->last_use);
        metal_cmd_use(cmd, &dst_mtl->last_use);
        metal_cmd_end(gpu, cmd);
    }
}

static bool check_row_pitch(pl_gpu gpu, const struct pl_tex_transfer_params *params)
{
    int dims = pl_tex_params_dimension(params->tex->params);
    if (dims == 1)
        return true;

    uint32_t max_width = dims == 2 ? gpu->limits.max_tex_2d_dim : gpu->limits.max_tex_3d_dim;
    size_t max_pitch = (size_t) max_width * params->tex->params.format->texel_size;
    if (params->row_pitch <= max_pitch)
        return true;

    PL_ERR(gpu, "Metal: texture transfer row pitch %zu exceeds the maximum of %zu",
           params->row_pitch, max_pitch);
    return false;
}

static bool transfer(pl_gpu gpu, const struct pl_tex_transfer_params *params, bool upload)
{
    if (!check_row_pitch(gpu, params))
        return false;

    struct pl_tex_mtl *tex_mtl = PL_PRIV(params->tex);
    struct pl_buf_mtl *buf_mtl = PL_PRIV(params->buf);
    size_t size = pl_tex_transfer_size(params), offset = params->buf_offset;
    id<MTLBuffer> staging = nil;
    if (offset % params->tex->params.format->texel_size) {
        staging = metal_new_buffer(gpu, NULL, size, MTLResourceStorageModePrivate);
        if (!staging)
            return false;
    }

    struct metal_cmd *cmd = metal_cmd_begin(gpu);
    if (!cmd)
        return false;

    if (staging && upload)
        metal_encode_buf_copy(cmd, staging, 0, buf_mtl->buf, offset, size);
    encode_tex_copy(cmd, params, staging ? staging : buf_mtl->buf, staging ? 0 : offset,
                    upload);
    if (staging && !upload)
        metal_encode_buf_copy(cmd, buf_mtl->buf, offset, staging, 0, size);

    metal_cmd_use(cmd, &tex_mtl->last_use);
    metal_cmd_use(cmd, &buf_mtl->last_use);
    if (params->callback)
        metal_cmd_callback(cmd, params->callback, params->priv);
    metal_cmd_end(gpu, cmd);
    return true;
}

bool metal_tex_upload(pl_gpu gpu, const struct pl_tex_transfer_params *params)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return false;
        if (!params->buf)
            return pl_tex_upload_pbo(gpu, params);
        return transfer(gpu, params, true);
    }
}

bool metal_tex_download(pl_gpu gpu, const struct pl_tex_transfer_params *params)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return false;
        if (!params->buf)
            return pl_tex_download_pbo(gpu, params);
        return transfer(gpu, params, false);
    }
}

bool metal_tex_poll(pl_gpu gpu, pl_tex tex, uint64_t timeout)
{
    @autoreleasepool {
        struct pl_tex_mtl *tex_mtl = PL_PRIV(tex);
        return metal_poll(gpu, &tex_mtl->last_use, timeout);
    }
}
