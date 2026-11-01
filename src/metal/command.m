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
#include "../pl_clock.h"

#define NS_PER_SEC 1e9

bool metal_gpu_usable(pl_gpu gpu)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    if (!atomic_load(&p->failed))
        return true;

    PL_ERR(gpu, "Metal: a command buffer failed, the pl_gpu is unusable");
    return false;
}

struct metal_cmd *metal_cmd_begin(pl_gpu gpu)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    pl_mutex_lock(&p->lock);
    if (p->cur)
        return p->cur;
    pl_mutex_unlock(&p->lock);

    id<MTLCommandBuffer> buf = [p->queue commandBuffer];
    if (!buf) {
        PL_ERR(gpu, "Metal: failed creating a command buffer");
        return NULL;
    }

    pl_mutex_lock(&p->lock);
    if (!p->cur) {
        struct metal_cmd *cmd = pl_zalloc_ptr(NULL, cmd);
        cmd->buf = buf;
        cmd->seq = p->next_seq++;
        p->cur = cmd;
    }

    return p->cur;
}

void metal_cmd_use(struct metal_cmd *cmd, _Atomic uint64_t *last_use)
{
    atomic_store(last_use, cmd->seq);
}

void metal_cmd_callback(struct metal_cmd *cmd, void (*run)(void *priv), void *priv)
{
    PL_ARRAY_APPEND(cmd, cmd->callbacks, (struct metal_callback) {
        .run = run,
        .priv = priv,
    });
}

static void cmd_completed(pl_gpu gpu, struct metal_cmd *cmd, id<MTLCommandBuffer> buf)
{
    @autoreleasepool {
        struct pl_mtl *p = PL_PRIV(gpu);
        if (buf.status == MTLCommandBufferStatusError || buf.error) {
            atomic_store(&p->failed, true);
            PL_ERR(gpu, "Metal: command buffer %"PRIu64" failed: %s", cmd->seq,
                   buf.error ? buf.error.localizedDescription.UTF8String : "no error");
        }

        pl_mutex_lock(&p->done_lock);
        cmd->done = true;
        int num = 0;
        while (num < p->submitted.num && p->submitted.elem[num]->done)
            num++;
        if (num) {
            for (int i = 0; i < num; i++)
                PL_ARRAY_APPEND(NULL, p->retired, p->submitted.elem[i]);
            atomic_store(&p->completed_seq, p->submitted.elem[num - 1]->seq);
            PL_ARRAY_REMOVE_RANGE(p->submitted, 0, num);
            pl_cond_broadcast(&p->done_cond);
        }
        pl_mutex_unlock(&p->done_lock);
    }
}

// Requires `lock`
static void commit(pl_gpu gpu)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    struct metal_cmd *cmd = p->cur;
    if (!cmd)
        return;

    p->cur = NULL;
    [cmd->buf addCompletedHandler:^(id<MTLCommandBuffer> buf) {
        cmd_completed(gpu, cmd, buf);
    }];

    pl_mutex_lock(&p->done_lock);
    PL_ARRAY_APPEND(NULL, p->submitted, cmd);
    pl_mutex_unlock(&p->done_lock);

    [cmd->buf commit];
    p->submitted_seq = cmd->seq;
}

static void commit_through(pl_gpu gpu, uint64_t seq)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    pl_mutex_lock(&p->lock);
    if (seq > p->submitted_seq)
        commit(gpu);
    pl_mutex_unlock(&p->lock);
}

static bool wait_completed(struct pl_mtl *p, uint64_t seq, uint64_t timeout)
{
    pl_clock_t start = pl_clock_now();
    bool done;

    pl_mutex_lock(&p->done_lock);
    while (!(done = atomic_load(&p->completed_seq) >= seq)) {
        uint64_t remaining = timeout;
        if (timeout != UINT64_MAX) {
            double elapsed = pl_clock_diff(pl_clock_now(), start) * NS_PER_SEC;
            if (elapsed >= timeout)
                break;
            remaining = timeout - (uint64_t) elapsed;
        }
        pl_cond_timedwait(&p->done_cond, &p->done_lock, remaining);
    }
    pl_mutex_unlock(&p->done_lock);

    return done;
}

static struct metal_cmd *take_retired(struct pl_mtl *p)
{
    struct metal_cmd *cmd = NULL;
    pl_mutex_lock(&p->done_lock);
    if (p->retired.num) {
        cmd = p->retired.elem[0];
        PL_ARRAY_REMOVE_AT(p->retired, 0);
    }
    pl_mutex_unlock(&p->done_lock);
    return cmd;
}

static void release_cmd(struct metal_cmd *cmd)
{
    for (int i = 0; i < cmd->callbacks.num; i++)
        cmd->callbacks.elem[i].run(cmd->callbacks.elem[i].priv);

    cmd->buf = nil;
    cmd->arena.chunks = nil;
    pl_free(cmd);
}

// Runs the callbacks of retired command buffers in `seq` order. Without
// `block`, returns at once if another thread is retiring.
static void retire(pl_gpu gpu, bool block)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    if (block) {
        pl_mutex_lock(&p->callback_lock);
    } else if (pthread_mutex_trylock(&p->callback_lock) != 0) {
        return;
    }

    if (!p->retiring) {
        p->retiring = true;
        struct metal_cmd *cmd;
        while ((cmd = take_retired(p)))
            release_cmd(cmd);
        p->retiring = false;
    }

    pl_mutex_unlock(&p->callback_lock);
}

void metal_cmd_end(pl_gpu gpu, struct metal_cmd *cmd)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    if (++cmd->num_encodes >= METAL_MAX_CMD_ENCODES)
        commit(gpu);
    pl_mutex_unlock(&p->lock);
    retire(gpu, false);
}

bool metal_poll(pl_gpu gpu, _Atomic uint64_t *last_use, uint64_t timeout)
{
    struct pl_mtl *p = PL_PRIV(gpu);
    uint64_t seq = atomic_load(last_use);
    bool busy = seq > atomic_load(&p->completed_seq);
    if (busy) {
        commit_through(gpu, seq);
        busy = !wait_completed(p, seq, timeout);
    }

    retire(gpu, false);
    return busy;
}

void metal_gpu_flush(pl_gpu gpu)
{
    @autoreleasepool {
        struct pl_mtl *p = PL_PRIV(gpu);
        pl_mutex_lock(&p->lock);
        commit(gpu);
        pl_mutex_unlock(&p->lock);
        retire(gpu, false);
    }
}

void metal_gpu_finish(pl_gpu gpu)
{
    @autoreleasepool {
        struct pl_mtl *p = PL_PRIV(gpu);
        pl_mutex_lock(&p->lock);
        commit(gpu);
        uint64_t seq = p->submitted_seq;
        pl_mutex_unlock(&p->lock);

        wait_completed(p, seq, UINT64_MAX);
        retire(gpu, true);
    }
}
