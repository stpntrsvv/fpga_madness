/* Обычный расчёт по массивам: потоки восток/юг, затем баланс и новое поле.
 * Две сборки одного алгоритма: скалярная C и четыре точки за команду NEON.
 * Ошибки накапливаются до конца прохода; принятый снимок меняется целиком. */
#include "grid_cpu.h"
#include <limits.h>

#ifndef GRID_RUN
#define GRID_RUN grid_scalar
#endif
#ifndef GRID_NEON
#define GRID_NEON 0
#endif

static inline int64_t floor16(int64_t x) {
    return x / 65536 - (x < 0 && x % 65536 != 0);
}
static inline int32_t narrow(int64_t x, uint32_t *bad) {
    *bad |= (x < INT32_MIN || x > INT32_MAX);
    return (int32_t)(uint32_t)x;
}
static inline int32_t edge(int32_t a, int32_t b, int32_t g, uint32_t *bad) {
    int32_t delta = narrow((int64_t)b - a, bad);
    return narrow(floor16((int64_t)g * delta), bad);
}
static inline int32_t update(const GridInput *g, const GridWork *work, size_t i,
                             int32_t west, int32_t north, uint32_t *bad) {
    int32_t balance = narrow((int64_t)work->east[i] + work->south[i], bad);
    balance = narrow((int64_t)balance - west, bad);
    balance = narrow((int64_t)balance - north, bad);
    balance = narrow((int64_t)balance + g->source[i], bad);
    int32_t derivative = narrow(floor16((int64_t)g->capacity_inverse[i] * balance), bad);
    return narrow((int64_t)work->current[i] + floor16((int64_t)g->dt * derivative), bad);
}

#if GRID_NEON
#include <arm_neon.h>
static inline int32x4_t add4(int32x4_t a, int32x4_t b, uint32x4_t *bad) {
    int32x4_t r = vaddq_s32(a, b);
    *bad = vorrq_u32(*bad, vreinterpretq_u32_s32(vandq_s32(veorq_s32(a, r), veorq_s32(b, r))));
    return r;
}
static inline int32x4_t sub4(int32x4_t a, int32x4_t b, uint32x4_t *bad) {
    int32x4_t r = vsubq_s32(a, b);
    *bad = vorrq_u32(*bad, vreinterpretq_u32_s32(vandq_s32(veorq_s32(a, b), veorq_s32(a, r))));
    return r;
}
static inline int32x4_t narrow4(int64x2_t lo, int64x2_t hi, uint32x4_t *bad) {
    int32x4_t r = vcombine_s32(vmovn_s64(lo), vmovn_s64(hi));
    uint32x4_t fits = vcombine_u32(vmovn_u64(vceqq_s64(lo, vmovl_s32(vget_low_s32(r)))),
                                   vmovn_u64(vceqq_s64(hi, vmovl_high_s32(r))));
    *bad = vorrq_u32(*bad, vmvnq_u32(fits));
    return r;
}
static inline int32x4_t mul4(int32x4_t a, int32x4_t b, uint32x4_t *bad) {
    return narrow4(vshrq_n_s64(vmull_s32(vget_low_s32(a), vget_low_s32(b)), 16),
                   vshrq_n_s64(vmull_high_s32(a, b), 16), bad);
}
static inline int32x4_t transition4(int32x4_t state, int32x4_t derivative, int32_t dt,
                                   int power, uint32x4_t *bad) {
    int64x2_t lo, hi;
    if (power >= 0) {
        int64x2_t shift = vdupq_n_s64(power - 16);
        lo = vshlq_s64(vmovl_s32(vget_low_s32(derivative)), shift);
        hi = vshlq_s64(vmovl_high_s32(derivative), shift);
    } else {
        lo = vshrq_n_s64(vmull_n_s32(vget_low_s32(derivative), dt), 16);
        hi = vshrq_n_s64(vmull_n_s32(vget_high_s32(derivative), dt), 16);
    }
    return narrow4(vaddw_s32(lo, vget_low_s32(state)), vaddw_high_s32(hi, state), bad);
}
#endif

GridResult GRID_RUN(const GridInput *g, GridWork *work, uint32_t steps) {
    const size_t w = g->width, h = g->height;
    GridResult result = {work->current, 0, 0};
#if GRID_NEON
    uint32_t dt = (uint32_t)g->dt;
    const int power = (dt & (dt - 1)) == 0 ? __builtin_ctz(dt) : -1;
#endif
    for (; result.completed < steps; ++result.completed) {
        uint32_t bad = 0;
#if GRID_NEON
        uint32x4_t bad4 = vdupq_n_u32(0);
#endif
        for (size_t y = 0; y < h; ++y) {
            size_t base = y * w, x = 0;
            /* У каждой внутренней связи одно ориентированное округлённое значение. */
#if GRID_NEON
            for (; x + 4 < w; x += 4) {
                size_t i = base + x;
                int32x4_t delta = sub4(vld1q_s32(work->current + i + 1),
                                       vld1q_s32(work->current + i), &bad4);
                vst1q_s32(work->east + i, mul4(vld1q_s32(g->east + i), delta, &bad4));
            }
#endif
            for (; x + 1 < w; ++x) {
                size_t i = base + x;
                work->east[i] = edge(work->current[i], work->current[i + 1], g->east[i], &bad);
            }
            work->east[base + w - 1] = 0;
            x = 0;
            if (y + 1 < h) {
#if GRID_NEON
                for (; x + 4 <= w; x += 4) {
                    size_t i = base + x;
                    int32x4_t delta = sub4(vld1q_s32(work->current + i + w),
                                           vld1q_s32(work->current + i), &bad4);
                    vst1q_s32(work->south + i, mul4(vld1q_s32(g->south + i), delta, &bad4));
                }
#endif
                for (; x < w; ++x) {
                    size_t i = base + x;
                    work->south[i] = edge(work->current[i], work->current[i + w], g->south[i], &bad);
                }
            } else for (; x < w; ++x) work->south[base + x] = 0;
        }
        for (size_t y = 0; y < h; ++y) {
            size_t base = y * w;
            work->trial[base] = update(g, work, base, 0, y ? work->south[base - w] : 0, &bad);
            size_t x = 1;
#if GRID_NEON
            for (; x + 4 <= w; x += 4) {
                size_t i = base + x;
                int32x4_t balance = add4(vld1q_s32(work->east + i), vld1q_s32(work->south + i), &bad4);
                balance = sub4(balance, vld1q_s32(work->east + i - 1), &bad4);
                balance = sub4(balance, y ? vld1q_s32(work->south + i - w) : vdupq_n_s32(0), &bad4);
                balance = add4(balance, vld1q_s32(g->source + i), &bad4);
                int32x4_t derivative = mul4(vld1q_s32(g->capacity_inverse + i), balance, &bad4);
                vst1q_s32(work->trial + i, transition4(vld1q_s32(work->current + i), derivative,
                                                      g->dt, power, &bad4));
            }
            bad |= vmaxvq_u32(vshrq_n_u32(bad4, 31));
#endif
            for (; x < w; ++x) {
                size_t i = base + x;
                work->trial[i] = update(g, work, i, work->east[i - 1], y ? work->south[i - w] : 0, &bad);
            }
        }
        if (bad) { result.fault = 3; break; }
        int32_t *previous = work->current;
        work->current = work->trial;
        work->trial = previous;
    }
    result.state = work->current;
    return result;
}
