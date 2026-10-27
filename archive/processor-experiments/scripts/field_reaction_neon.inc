/* Четыре независимые точки, полные расширяющие произведения Q16.16. */
static inline int32x4_t reaction4(int32x4_t u, int32x4_t diffusion, uint32x4_t *bad) {
    int32x4_t u2 = mul4(u, u, bad);
    int32x4_t u3 = mul4(u2, u, bad);
    int32x4_t au = mul4(vdupq_n_s32(field_a), u, bad);
    int32x4_t bu3 = mul4(vdupq_n_s32(field_b), u3, bad);
    return sub4(add4(diffusion, au, bad), bu3, bad);
}
