/* Обычный расчёт по массивам: потоки восток/юг, затем баланс и новое поле.
 * Helium/MVE: четыре знаковых 32-разрядных значения за вектор.
 * Ошибки накапливаются до конца прохода; принятый снимок меняется целиком. */
#include "grid_cpu.h"
#include <limits.h>


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

#include <arm_mve.h>
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
static inline void check4(int32x4_t high, int32x4_t expected, uint32x4_t *bad) {
    *bad = vorrq_u32(*bad, vpselq_u32(vdupq_n_u32(0), vdupq_n_u32(0x80000000),
                                     vcmpeqq_s32(high, expected)));
}
/* Две половины точного знакового произведения, без насыщения и округления вверх. */
static inline int32x4_t mul4(int32x4_t a, int32x4_t b, uint32x4_t *bad) {
    int32x4_t high = vmulhq_s32(a, b);
    uint32x4_t low = vreinterpretq_u32_s32(vmulq_s32(a, b));
    int32x4_t r = vreinterpretq_s32_u32(vorrq_u32(vshrq_n_u32(low, 16),
                               vshlq_n_u32(vreinterpretq_u32_s32(high), 16)));
    check4(high, vshrq_n_s32(r, 16), bad);
    return r;
}
static inline int32x4_t transition4(int32x4_t state, int32x4_t derivative, int32_t dt,
                                   int power, uint32x4_t *bad) {
    if (power >= 0 && power <= 16) {
        return add4(state, vshlq_s32(derivative, vdupq_n_s32(power - 16)), bad);
    }
    int32x4_t factor = vdupq_n_s32(dt);
    int32x4_t product_high = vmulhq_s32(derivative, factor);
    uint32x4_t product_low = vreinterpretq_u32_s32(vmulq_s32(derivative, factor));
    uint32x4_t increment = vorrq_u32(vshrq_n_u32(product_low, 16),
                            vshlq_n_u32(vreinterpretq_u32_s32(product_high), 16));
    uint32x4_t r = vaddq_u32(increment, vreinterpretq_u32_s32(state));
    /* Перенос в старшее слово сохраняет допустимое широкое приращение. */
    uint32x4_t carry = vpselq_u32(vdupq_n_u32(1), vdupq_n_u32(0), vcmphiq_u32(increment, r));
    int32x4_t high = vaddq_s32(vaddq_s32(vshrq_n_s32(product_high, 16), vshrq_n_s32(state, 31)),
                              vreinterpretq_s32_u32(carry));
    int32x4_t result = vreinterpretq_s32_u32(r);
    check4(high, vshrq_n_s32(result, 31), bad);
    return result;
}

GridResult grid_helium(const GridInput *g, GridWork *work, uint32_t steps) {
    const size_t w = g->width, h = g->height;
    GridResult result = {work->current, 0, 0};
    uint32_t dt = (uint32_t)g->dt;
    const int power = (dt & (dt - 1)) == 0 ? __builtin_ctz(dt) : -1;
    for (; result.completed < steps; ++result.completed) {
        uint32_t bad = 0;
        uint32x4_t bad4 = vdupq_n_u32(0);
        for (size_t y = 0; y < h; ++y) {
            size_t base = y * w, x = 0;
            /* У каждой внутренней связи одно ориентированное округлённое значение. */
            for (; x + 4 < w; x += 4) {
                size_t i = base + x;
                int32x4_t delta = sub4(vld1q_s32(work->current + i + 1),
                                       vld1q_s32(work->current + i), &bad4);
                vst1q_s32(work->east + i, mul4(vld1q_s32(g->east + i), delta, &bad4));
            }
            for (; x + 1 < w; ++x) {
                size_t i = base + x;
                work->east[i] = edge(work->current[i], work->current[i + 1], g->east[i], &bad);
            }
            work->east[base + w - 1] = 0;
            x = 0;
            if (y + 1 < h) {
                for (; x + 4 <= w; x += 4) {
                    size_t i = base + x;
                    int32x4_t delta = sub4(vld1q_s32(work->current + i + w),
                                           vld1q_s32(work->current + i), &bad4);
                    vst1q_s32(work->south + i, mul4(vld1q_s32(g->south + i), delta, &bad4));
                }
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
            bad |= vmaxvq_u32(0, vshrq_n_u32(bad4, 31));
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
