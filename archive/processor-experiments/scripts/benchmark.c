/* Измерение одной траектории. Вычислительные функции собираются отдельно,
 * без LTO, чтобы компилятор не вынес одинаковые запуски из цикла измерения. */
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include "models.h"

static volatile uint64_t observed;

static double now(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &t)) {
        perror("Не удалось прочитать часы");
        exit(1);
    }
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static double batch(Runner run, const Input *input, uint32_t steps, uint64_t count) {
    uint64_t checksum = 0;
    double start = now();
    for (uint64_t i = 0; i < count; ++i) {
        Result r = run(input, steps);
        checksum += (uint32_t)r.x[0] + (uint64_t)(uint32_t)r.x[1]
                  + (uint32_t)r.x[2] + (uint32_t)r.x[3] + r.completed + r.fault;
    }
    double elapsed = now() - start;
    observed = checksum;
    return elapsed;
}

int main(int argc, char **argv) {
    if (argc != 17) {
        fprintf(stderr, "Нужны номер модели, число шагов, dt, 4 состояния, 8 параметров и режим проверки\n");
        return 2;
    }
    unsigned model = (unsigned)strtoul(argv[1], NULL, 10);
    uint32_t steps = (uint32_t)strtoul(argv[2], NULL, 10);
    if (model >= MODEL_COUNT) return 2;
    Input input = {.dt = (int32_t)strtoll(argv[3], NULL, 10)};
    for (unsigned i = 0; i < 4; ++i) input.x[i] = (int32_t)strtoll(argv[4 + i], NULL, 10);
    for (unsigned i = 0; i < 8; ++i) input.p[i] = (int32_t)strtoll(argv[8 + i], NULL, 10);
    /* Последний аргумент выбирает проверку без измерения. */
    Result r = runners[model](&input, steps);
    printf("RESULT %u %u %" PRId32 " %" PRId32 " %" PRId32 " %" PRId32 "\n",
           r.completed, r.fault, r.x[0], r.x[1], r.x[2], r.x[3]);
    if (atoi(argv[16])) return 0;
    batch(runners[model], &input, steps, 64);
    uint64_t count = 1;
    while (batch(runners[model], &input, steps, count) < 0.03) {
        if (count >= (UINT64_C(1) << 32)) return 3;
        count *= 2;
    }
    printf("REPEATS %" PRIu64 "\n", count);
    for (unsigned i = 0; i < 9; ++i) {
        double elapsed = batch(runners[model], &input, steps, count);
        printf("SAMPLE %.9f %.3f\n", elapsed, elapsed * 1e9 / count);
    }
    printf("CHECKSUM %" PRIu64 "\n", observed);
    return 0;
}
