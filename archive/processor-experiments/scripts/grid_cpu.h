#ifndef GRID_CPU_H
#define GRID_CPU_H
#include <stddef.h>
#include <stdint.h>

typedef struct {
    size_t width, height;
    int32_t dt;
    const int32_t *initial, *east, *south, *capacity_inverse, *source;
} GridInput;
typedef struct { int32_t *current, *trial, *east, *south; } GridWork;
typedef struct { int32_t *state; uint32_t completed, fault; } GridResult;
typedef GridResult (*GridRunner)(const GridInput *, GridWork *, uint32_t);
GridResult grid_scalar(const GridInput *, GridWork *, uint32_t);
GridResult grid_neon(const GridInput *, GridWork *, uint32_t);
#endif
