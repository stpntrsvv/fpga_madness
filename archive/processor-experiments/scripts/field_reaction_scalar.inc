/* Добавление локальной реакции к обычному решателю по массивам. */
static inline int32_t reaction(int32_t u, int32_t diffusion, uint32_t *bad) {
    int32_t u2 = narrow(floor16((int64_t)u * u), bad);
    int32_t u3 = narrow(floor16((int64_t)u2 * u), bad);
    int32_t au = narrow(floor16((int64_t)field_a * u), bad);
    int32_t bu3 = narrow(floor16((int64_t)field_b * u3), bad);
    return narrow((int64_t)narrow((int64_t)diffusion + au, bad) - bu3, bad);
}
