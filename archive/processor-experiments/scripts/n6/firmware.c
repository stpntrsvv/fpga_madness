/* NUCLEO-N657X0-Q: временный опыт из SRAM, CMSIS и прямые регистры, без HAL. */
#include "stm32n6xx.h"
#include "grid_cpu.h"
#include <string.h>

#ifndef N6_CPU_MHZ
#define N6_CPU_MHZ 800
#endif
#if N6_CPU_MHZ != 800 && N6_CPU_MHZ != 600
#error Допустимы только проверяемые режимы 600 и 800 МГц
#endif
#ifndef N6_DTCM
#define N6_DTCM 1
#endif
#if N6_DTCM
#define WORK_MEMORY __attribute__((section(".dtcm"), aligned(32)))
#else
#define WORK_MEMORY __attribute__((aligned(32)))
#endif
#define CAPACITY 4096U
#define SERIES 9U
extern GridResult grid_helium(const GridInput *, GridWork *, uint32_t);
extern uint32_t _sbss, _ebss, _sdtcm, _edtcm, vectors;
/* Разделение команд и данных фиксировано в ABI; адреса берёт хост из ELF. */
volatile struct {
    uint32_t magic, abi, state, command;
    uint32_t width, height, dt, steps, variant, timing;
    uint32_t completed, fault, result_address, cpu_hz, axi_hz, ccr, cpuid, dwt_ctrl;
    uint32_t error_line, input_address, capacity;
    uint32_t clock_registers[12];
    uint64_t cycles[SERIES];
    uint32_t iterations[SERIES];
} n6_mailbox;
static int32_t initial[CAPACITY] __attribute__((aligned(32)));
static int32_t maps[4][CAPACITY] WORK_MEMORY;
uint32_t n6_input_addresses[5];
static int32_t current[CAPACITY] WORK_MEMORY;
static int32_t trial[CAPACITY] WORK_MEMORY;
static int32_t east[CAPACITY] WORK_MEMORY;
static int32_t south[CAPACITY] WORK_MEMORY;

static void failure(uint32_t line) {
    if (SCB->CCR & SCB_CCR_DC_Msk) SCB_DisableDCache();
    n6_mailbox.error_line = line;
    n6_mailbox.state = 0xdead;
    for (;;) __NOP();
}
void exception_handler(void) { failure(0xffff); }
#define REQUIRE(c) do { if (!(c)) failure(__LINE__); } while (0)
static void await(volatile uint32_t *reg, uint32_t mask, uint32_t expected) {
    uint32_t started = DWT->CYCCNT;
    while ((*reg & mask) != expected) {
        if ((uint32_t)(DWT->CYCCNT - started) >= 16000000U) {
            n6_mailbox.completed = (uint32_t)reg;
            n6_mailbox.fault = mask;
            n6_mailbox.result_address = *reg;
            failure(__LINE__);
        }
    }
}
static void clock_config(void) {
    /* Внешний источник VCORE предусмотрен схемой NUCLEO и шаблоном ST.
     * Повторная запись неизменного SDEN=0 завершает выбор питания после BootROM. */
    REQUIRE((PWR->CR1 & PWR_CR1_SDEN) == 0);
    PWR->CR1 &= ~PWR_CR1_SDEN;
    RCC->CSR = RCC_CSR_HSIONS;
    await(&RCC->SR, RCC_SR_HSIRDY, RCC_SR_HSIRDY);
    /* Переход на HSI перед изменением PLL; периферия в опыте не используется. */
    RCC->CFGR1 &= ~(RCC_CFGR1_CPUSW | RCC_CFGR1_SYSSW);
    await(&RCC->CFGR1, RCC_CFGR1_CPUSWS | RCC_CFGR1_SYSSWS, 0);
    RCC->HSICFGR &= ~RCC_HSICFGR_HSIDIV;
    /* UM3417 §7.4.3: PB12/PWR_LP управляет внешним регулятором:
     * 0 -> 0.81 В, 1 -> 0.89 В. PWR.VOS сам внешний источник не меняет. */
    RCC->AHB4ENSR = RCC_AHB4ENSR_GPIOBENS;
    (void)RCC->AHB4ENR;
#if N6_CPU_MHZ == 800
    GPIOB->BSRR = 1U << 12;
#else
    GPIOB->BSRR = 1U << 28;
#endif
    GPIOB->OTYPER &= ~(1U << 12);
    GPIOB->PUPDR &= ~(3U << 24);
    GPIOB->MODER = (GPIOB->MODER & ~(3U << 24)) | (1U << 24);
#if N6_CPU_MHZ == 800
    PWR->VOSCR |= PWR_VOSCR_VOS;
#else
    PWR->VOSCR &= ~PWR_VOSCR_VOS;
#endif
    await(&PWR->VOSCR, PWR_VOSCR_VOSRDY, PWR_VOSCR_VOSRDY);
    RCC->CCR = RCC_CCR_PLL1ONC;
    await(&RCC->SR, RCC_SR_PLL1RDY, 0);
    RCC->PLL1CFGR3 |= RCC_PLL1CFGR3_PLL1MODSSDIS;
    RCC->PLL1CFGR1 = (RCC->PLL1CFGR1 & ~(RCC_PLL1CFGR1_PLL1BYP |
        RCC_PLL1CFGR1_PLL1SEL | RCC_PLL1CFGR1_PLL1DIVM | RCC_PLL1CFGR1_PLL1DIVN)) |
        (4U << RCC_PLL1CFGR1_PLL1DIVM_Pos) |
        ((N6_CPU_MHZ / 8U) << RCC_PLL1CFGR1_PLL1DIVN_Pos);
    RCC->PLL1CFGR2 &= ~RCC_PLL1CFGR2_PLL1DIVNFRAC;
    RCC->PLL1CFGR3 = (RCC->PLL1CFGR3 & ~(RCC_PLL1CFGR3_PLL1PDIV1 |
        RCC_PLL1CFGR3_PLL1PDIV2 | RCC_PLL1CFGR3_PLL1MODDSEN)) |
        (1U << RCC_PLL1CFGR3_PLL1PDIV1_Pos) | (1U << RCC_PLL1CFGR3_PLL1PDIV2_Pos) |
        RCC_PLL1CFGR3_PLL1MODSSRST | RCC_PLL1CFGR3_PLL1PDIVEN;
    RCC->CSR = RCC_CSR_PLL1ONS;
    await(&RCC->SR, RCC_SR_PLL1RDY, RCC_SR_PLL1RDY);
    /* HSI/4 × 100 = 1600 МГц, CPU /2, AXI /4, AHB /2.
     * Для 600 МГц: HSI/4 × 75 = 1200, CPU /2, AXI /3. */
    RCC->IC1CFGR = 1U << RCC_IC1CFGR_IC1INT_Pos;
    RCC->IC2CFGR = (N6_CPU_MHZ == 800 ? 3U : 2U) << RCC_IC2CFGR_IC2INT_Pos;
    RCC->IC6CFGR = 3U << RCC_IC6CFGR_IC6INT_Pos;
    RCC->IC11CFGR = (N6_CPU_MHZ == 800 ? 3U : 2U) << RCC_IC11CFGR_IC11INT_Pos;
    RCC->DIVENSR = RCC_DIVENSR_IC1ENS | RCC_DIVENSR_IC2ENS | RCC_DIVENSR_IC6ENS | RCC_DIVENSR_IC11ENS;
    RCC->CFGR2 = (RCC->CFGR2 & ~(RCC_CFGR2_HPRE | RCC_CFGR2_PPRE1 |
        RCC_CFGR2_PPRE2 | RCC_CFGR2_PPRE4 | RCC_CFGR2_PPRE5)) | RCC_CFGR2_HPRE_0;
    RCC->CFGR1 = (RCC->CFGR1 & ~(RCC_CFGR1_CPUSW | RCC_CFGR1_SYSSW)) |
        RCC_CFGR1_CPUSW | RCC_CFGR1_SYSSW;
    await(&RCC->CFGR1, RCC_CFGR1_CPUSWS | RCC_CFGR1_SYSSWS, RCC_CFGR1_CPUSWS | RCC_CFGR1_SYSSWS);
    __DSB(); __ISB();
    n6_mailbox.cpu_hz = N6_CPU_MHZ * 1000000U;
    n6_mailbox.axi_hz = 400000000U;
    uint32_t snapshot[] = {RCC->CR, RCC->SR, RCC->CFGR1, RCC->CFGR2, RCC->HSICFGR,
        RCC->PLL1CFGR1, RCC->PLL1CFGR2, RCC->PLL1CFGR3, RCC->IC1CFGR, RCC->IC2CFGR,
        PWR->CR1, PWR->VOSCR};
    for (unsigned i = 0; i < 12; ++i) n6_mailbox.clock_registers[i] = snapshot[i];
}
static void calculate(void) {
    size_t w = n6_mailbox.width, h = n6_mailbox.height;
    REQUIRE(w && h && w <= CAPACITY && h <= CAPACITY / w);
    REQUIRE(n6_mailbox.dt > 0 && n6_mailbox.dt <= INT32_MAX);
    REQUIRE(n6_mailbox.variant <= 1);
    uint32_t steps = n6_mailbox.steps;
    REQUIRE(steps <= 64);
    GridInput g = {w, h, (int32_t)n6_mailbox.dt, initial, maps[0], maps[1], maps[2], maps[3]};
    GridRunner runner = n6_mailbox.variant ? grid_helium : grid_scalar;
    unsigned timing = n6_mailbox.timing;
    GridResult result = {0};
    SCB_EnableDCache();
    n6_mailbox.ccr = SCB->CCR;
    /* Копирование исходного поля и прогрев исключены из измеряемого интервала. */
    for (unsigned s = 0; s < (timing ? 8U + SERIES : 1U); ++s) {
        uint64_t total = 0;
        uint32_t iterations = 0;
        do {
            memcpy(current, g.initial, w * h * sizeof(int32_t));
            GridWork work = {current, trial, east, south};
            __DSB(); __ISB();
            uint32_t start = DWT->CYCCNT;
            result = runner(&g, &work, steps);
            __DSB(); __ISB();
            uint32_t elapsed = DWT->CYCCNT - start;
            total += elapsed;
            ++iterations;
            /* Ошибочные случаи не размножаются в серии измерения. */
            if (!timing || s < 8 || result.fault) break;
        } while (total < (uint64_t)n6_mailbox.cpu_hz * 30U / 1000U);
        if (!timing || s >= 8) {
            unsigned index = timing ? s - 8 : 0;
            n6_mailbox.cycles[index] = total;
            n6_mailbox.iterations[index] = iterations;
        }
    }
    n6_mailbox.completed = result.completed;
    n6_mailbox.fault = result.fault;
    n6_mailbox.result_address = (uint32_t)result.state;
    /* Между командами D-cache выключен: отладчик видит согласованную SRAM. */
    SCB_DisableDCache();
}
void n6_main(void) {
    __disable_irq();
    SCB->CPACR |= (3UL << 20) | (3UL << 22);
    __DSB(); __ISB();
    SCB->VTOR = (uint32_t)&vectors;
    if (SCB->CCR & SCB_CCR_DC_Msk) SCB_DisableDCache();
    for (uint32_t *p = &_sbss; p < &_ebss; ++p) *p = 0;
    SysTick->CTRL = 0;
    MPU->CTRL = 0;
    __DSB(); __ISB();
    n6_mailbox.magic = 0x4e364731;
    n6_mailbox.abi = 2;
    n6_mailbox.state = 2;
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    uint32_t before = DWT->CYCCNT;
    for (volatile unsigned i = 0; i < 100; ++i) __NOP();
    REQUIRE(DWT->CYCCNT != before);
    clock_config();
#if N6_DTCM
    REQUIRE((MEMSYSCTL->DTCMCR & MEMSYSCTL_DTCMCR_EN_Msk) != 0);
    REQUIRE(((MEMSYSCTL->DTCMCR & MEMSYSCTL_DTCMCR_SZ_Msk) >> MEMSYSCTL_DTCMCR_SZ_Pos) >= 8);
#endif
    for (uint32_t *p = &_sdtcm; p < &_edtcm; ++p) *p = 0;
    /* Явная нормальная WB/WA память: включение CCR само не задаёт атрибуты SRAM. */
    for (unsigned r = 0; r < ((MPU->TYPE >> 8) & 255U); ++r) {
        MPU->RNR = r; MPU->RBAR = 0; MPU->RLAR = 0;
    }
    ARM_MPU_SetMemAttr(0, ARM_MPU_ATTR(15, 15));
    ARM_MPU_SetRegion(0, ARM_MPU_RBAR(0x34180000U, ARM_MPU_SH_NON, 0, 0, 0),
                        ARM_MPU_RLAR(0x341fffffU, 0));
    ARM_MPU_Enable(MPU_CTRL_PRIVDEFENA_Msk);
    SCB_EnableICache();
    n6_mailbox.cpuid = SCB->CPUID;
    n6_mailbox.dwt_ctrl = DWT->CTRL;
    n6_input_addresses[0] = (uint32_t)initial;
    for (unsigned i = 0; i < 4; ++i) n6_input_addresses[i+1] = (uint32_t)maps[i];
    n6_mailbox.input_address = (uint32_t)n6_input_addresses;
    n6_mailbox.capacity = CAPACITY;
    n6_mailbox.state = 1;
    for (;;) {
        if (n6_mailbox.command == 1) {
            n6_mailbox.state = 2;
            calculate();
            n6_mailbox.command = 0;
            __DSB();
            n6_mailbox.state = 3;
        }
    }
}
