#define _DARWIN_C_SOURCE
#include "grid_cpu.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static void require(int ok, const char *message) {
    if (!ok) { fprintf(stderr, "%s\n", message); exit(1); }
}
static double seconds(void) {
    struct timespec t;
    require(clock_gettime(CLOCK_MONOTONIC_RAW, &t) == 0, "Недоступны часы");
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}
static uint64_t checksum(const int32_t *a, size_t n) {
    uint64_t value = UINT64_C(14695981039346656037);
    for (size_t i = 0; i < n; ++i) value = (value ^ (uint32_t)a[i]) * UINT64_C(1099511628211);
    return value;
}
int main(int argc, char **argv) {
    require(argc == 5, "Нужны: вход, выход, вариант (0/1), замер (0/1)");
    FILE *input = fopen(argv[1], "rb");
    require(input != NULL, "Не открыт вход");
    uint32_t header[5];
    require(fread(header, sizeof(header), 1, input) == 1 && header[0] == 0x47524944 &&
            header[1] > 0 && header[1] <= 4096 && header[2] > 0 && header[2] <= 4096 &&
            header[4] > 0 && header[4] < 0x80000000u, "Неверный заголовок");
    size_t n = (size_t)header[1] * header[2], bytes = n * sizeof(int32_t);
    int32_t *data = malloc(9 * bytes);
    require(data != NULL, "Недостаточно памяти");
    require(fread(data, bytes, 5, input) == 5 && fgetc(input) == EOF, "Неверная длина входа");
    fclose(input);
    GridInput g = {header[1], header[2], (int32_t)header[4], data, data+n, data+2*n, data+3*n, data+4*n};
    GridWork work = {data+5*n, data+6*n, data+7*n, data+8*n};
    GridRunner runner = atoi(argv[3]) ? grid_neon : grid_scalar;
    memcpy(work.current, g.initial, bytes);
    GridResult result = runner(&g, &work, header[3]);
    FILE *output = fopen(argv[2], "wb");
    require(output != NULL, "Не открыт выход");
    uint32_t status[2] = {result.completed, result.fault};
    require(fwrite(status, sizeof(status), 1, output) == 1 &&
            fwrite(result.state, bytes, 1, output) == 1 && fclose(output) == 0, "Ошибка записи");
    uint64_t observed = checksum(result.state, n);
    const uint64_t expected_checksum = observed;
    unsigned counts[9] = {0};
    printf("{\"completed\":%u,\"fault\":%u,\"checksum\":\"%016llx\",\"samples_ns\":[",
           result.completed, result.fault, (unsigned long long)observed);
    if (atoi(argv[4])) {
        require(header[3] > 0 && result.completed == header[3] && result.fault == 0,
                "Нельзя измерять незавершённый расчёт");
        for (unsigned i = 0; i < 8; ++i) {
            memcpy(work.current, g.initial, bytes);
            result = runner(&g, &work, header[3]);
            uint64_t actual = checksum(result.state, n);
            require(result.completed == header[3] && result.fault == 0 && actual == expected_checksum,
                    "Расхождение при прогреве");
            observed ^= actual;
        }
        for (unsigned sample = 0; sample < 9; ++sample) {
            double elapsed = 0;
            unsigned repeats = 0;
            do {
                /* Сброс поля и контрольная сумма находятся вне измеряемого интервала. */
                memcpy(work.current, g.initial, bytes);
                double start = seconds();
                result = runner(&g, &work, header[3]);
                elapsed += seconds() - start;
                require(result.completed == header[3] && result.fault == 0, "Ошибка при замере");
                uint64_t actual = checksum(result.state, n);
                require(actual == expected_checksum, "Расхождение при замере");
                observed ^= actual + repeats;
                ++repeats;
            } while (elapsed < .03);
            counts[sample] = repeats;
            printf("%s%.6f", sample ? "," : "", elapsed * 1e9 / repeats / header[3]);
        }
    }
    printf("],\"repeats_per_sample\":[");
    if (atoi(argv[4]))
        for (unsigned i = 0; i < 9; ++i) printf("%s%u", i ? "," : "", counts[i]);
    printf("],\"observed\":\"%016llx\"}\n", (unsigned long long)observed);
    free(data);
    return 0;
}
