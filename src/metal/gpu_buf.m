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

#include "gpu.h"

id<MTLBuffer> metal_new_buffer(pl_gpu gpu, const void *data, size_t size,
                               MTLResourceOptions options)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    id<MTLBuffer> buf = data
        ? [p->device newBufferWithBytes:data length:size options:options]
        : [p->device newBufferWithLength:size options:options];
    if (!buf)
        PL_ERR(gpu, "Metal: failed allocating a buffer of %zu bytes", size);
    return buf;
}

void metal_encode_buf_copy(struct metal_cmd *cmd, id<MTLBuffer> dst, size_t dst_offset,
                           id<MTLBuffer> src, size_t src_offset, size_t size)
{
    id<MTLBlitCommandEncoder> enc = [cmd->buf blitCommandEncoder];
    [enc copyFromBuffer:src
           sourceOffset:src_offset
               toBuffer:dst
      destinationOffset:dst_offset
                   size:size];
    [enc endEncoding];
}

static bool upload_initial_data(pl_gpu gpu, pl_buf buf, const void *data)
{
    struct pl_buf_mtl *buf_mtl = PL_PRIV(buf);
    size_t size = buf->params.size;
    id<MTLBuffer> staging = metal_new_buffer(gpu, data, size, MTLResourceStorageModeShared);
    if (!staging)
        return false;

    struct metal_cmd *cmd = metal_cmd_begin(gpu);
    if (!cmd)
        return false;

    metal_encode_buf_copy(cmd, buf_mtl->buf, 0, staging, 0, size);
    metal_cmd_use(cmd, &buf_mtl->last_use);
    metal_cmd_end(gpu, cmd);
    return true;
}

void metal_buf_destroy(pl_gpu gpu, pl_buf buf)
{
    @autoreleasepool {
        struct pl_buf_mtl *buf_mtl = PL_PRIV(buf);
        buf_mtl->buf = nil;
        pl_free((void *) buf);
    }
}

pl_buf metal_buf_create(pl_gpu gpu, const struct pl_buf_params *params)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return NULL;

        struct pl_buf_t *buf = pl_zalloc_obj(NULL, buf, struct pl_buf_mtl);
        buf->params = *params;
        buf->params.initial_data = NULL;
        struct pl_buf_mtl *buf_mtl = PL_PRIV(buf);

        bool host = params->host_mapped || params->host_writable ||
                    params->host_readable || params->uniform;
        MTLResourceOptions options = host ? MTLResourceStorageModeShared
                                          : MTLResourceStorageModePrivate;
        const void *data = host ? params->initial_data : NULL;
        buf_mtl->buf = metal_new_buffer(gpu, data, params->size, options);
        if (!buf_mtl->buf)
            goto error;

        if (params->host_mapped)
            buf->data = buf_mtl->buf.contents;
        if (!host && params->initial_data &&
            !upload_initial_data(gpu, buf, params->initial_data))
            goto error;

        return buf;

error:
        metal_buf_destroy(gpu, buf);
        return NULL;
    }
}

void metal_buf_write(pl_gpu gpu, pl_buf buf, size_t offset, const void *data,
                     size_t size)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return;

        struct pl_buf_mtl *buf_mtl = PL_PRIV(buf);
        while (metal_poll(gpu, &buf_mtl->last_use, UINT64_MAX))
            ;
        memcpy((uint8_t *) buf_mtl->buf.contents + offset, data, size);
    }
}

bool metal_buf_read(pl_gpu gpu, pl_buf buf, size_t offset, void *dest, size_t size)
{
    @autoreleasepool {
        struct pl_buf_mtl *buf_mtl = PL_PRIV(buf);
        while (metal_poll(gpu, &buf_mtl->last_use, UINT64_MAX))
            ;
        if (!metal_gpu_usable(gpu))
            return false;

        memcpy(dest, (const uint8_t *) buf_mtl->buf.contents + offset, size);
        return true;
    }
}

void metal_buf_copy(pl_gpu gpu, pl_buf dst, size_t dst_offset, pl_buf src,
                    size_t src_offset, size_t size)
{
    @autoreleasepool {
        if (!metal_gpu_usable(gpu))
            return;

        struct pl_buf_mtl *dst_mtl = PL_PRIV(dst), *src_mtl = PL_PRIV(src);
        struct metal_cmd *cmd = metal_cmd_begin(gpu);
        if (!cmd)
            return;

        metal_encode_buf_copy(cmd, dst_mtl->buf, dst_offset, src_mtl->buf,
                              src_offset, size);
        metal_cmd_use(cmd, &dst_mtl->last_use);
        metal_cmd_use(cmd, &src_mtl->last_use);
        metal_cmd_end(gpu, cmd);
    }
}

bool metal_buf_poll(pl_gpu gpu, pl_buf buf, uint64_t timeout)
{
    @autoreleasepool {
        struct pl_buf_mtl *buf_mtl = PL_PRIV(buf);
        return metal_poll(gpu, &buf_mtl->last_use, timeout);
    }
}
