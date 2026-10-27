"""Проверки чисел, ограничений доставки и согласованного принятия большой сетки."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dpu import ONE, MAXIMUM
from grid_model import Fabric, Grid, example, reference, simulate


class GridModelTest(unittest.TestCase):
    def compare(self, grid, fabric, steps=1):
        expected = grid.state
        completed, fault = 0, 0
        for _ in range(steps):
            expected, fault = reference(replace(grid, state=expected))
            if fault:
                break
            completed += 1
        actual = simulate(grid, fabric, steps)
        self.assertEqual((actual['state'], actual['completed'], actual['fault']),
                         (expected, completed, fault))
        if not fault:
            for frame in actual['frames']:
                self.assertEqual(frame['stats']['state_reads'], len(grid.state))
                self.assertEqual(frame['stats']['parameter_reads'], 4 * len(grid.state))
                self.assertEqual(frame['stats']['trial_writes'], len(grid.state))
                self.assertEqual(frame['stats']['line_writes'], len(grid.state))
                self.assertLessEqual(frame['stats']['line_reads'], 3 * len(grid.state))
                self.assertEqual(frame['stats'].get('flux_reads', 0), grid.width * (grid.height - 1))
                self.assertEqual(frame['stats'].get('flux_writes', 0), grid.width * (grid.height - 1))
        return actual

    def test_known_pair_and_negative_rounding(self):
        grid = Grid(2, 1, (ONE, 0), (ONE // 8, 0), (0, 0), (ONE, ONE), (0, 0), ONE // 2)
        for lanes in (1, 2, 4):
            result = self.compare(grid, Fabric(lanes=lanes, banks=lanes))
            self.assertEqual(result['state'], (ONE - ONE // 16, ONE // 16))
        # -1/4 округляется вниз до -1; противоположный вклад использует тот же поток.
        tiny = replace(grid, state=(1, 0), east=(ONE // 4, 0), dt=ONE)
        self.assertEqual(self.compare(tiny, Fabric())['state'], (0, 1))

    def test_rectangles_ports_latencies_and_small_queues(self):
        count = 0
        for width, height in ((1, 1), (1, 7), (9, 1), (2, 3), (3, 7), (11, 5)):
            grid = replace(example(width, height), dt=8191)
            for lanes in (1, 2, 4):
                for multipliers in (2, 4):
                    for slots, banks, memory_latency in ((1, 1, 3), (3, lanes, 1)):
                        with self.subTest(size=(width, height), lanes=lanes, multipliers=multipliers,
                                          slots=slots, banks=banks):
                            self.compare(grid, Fabric(lanes=lanes, banks=banks, slots=slots,
                                                     multipliers=multipliers, memory_latency=memory_latency), 3)
                            count += 1
        self.assertEqual(count, 72)

    def test_backpressure_does_not_lose_points(self):
        grid = example(13, 9)
        fabric = Fabric(lanes=4, banks=4, slots=2)
        normal = self.compare(grid, fabric, 2)
        paused = self.compare(grid, replace(fabric, write_pause_period=16, write_pause_length=15), 2)
        self.assertGreater(paused['cycles'], normal['cycles'])
        self.assertGreater(paused['frames'][0]['stats']['window_slot_stalls'], 0)
        self.assertGreater(paused['frames'][0]['stats']['output_stalls'], 0)

    def test_real_resource_bottlenecks(self):
        grid = example(32, 16)
        baseline = self.compare(grid, Fabric(shift_dt=False))
        narrow = self.compare(grid, Fabric(banks=1, shift_dt=False))
        shared = self.compare(grid, Fabric(multipliers=2, shift_dt=False))
        self.assertGreater(narrow['cycles'], baseline['cycles'])
        self.assertGreater(shared['cycles'], baseline['cycles'])
        self.assertGreater(narrow['frames'][0]['stats']['parameter_port_denials'], 0)
        self.assertGreater(shared['frames'][0]['stats']['multiply_denials'], 0)
        # Два потока на внутренние связи и по два произведения перехода на точку.
        expected = 4 * len(grid.state) - grid.width - grid.height
        self.assertEqual(baseline['frames'][0]['stats']['multiply_issued'], expected)

    def test_shift_dt_releases_multiplier_without_changing_numbers(self):
        grid = example(32, 16)
        fabric = Fabric(multipliers=3, slots=32)
        shifted = self.compare(grid, fabric)
        generic = self.compare(grid, replace(fabric, shift_dt=False))
        self.assertLess(shifted['cycles'], generic['cycles'])
        self.assertEqual(shifted['frames'][0]['stats']['shift_issued'], len(grid.state))
        self.assertEqual(generic['frames'][0]['stats']['multiply_issued'] -
                         shifted['frames'][0]['stats']['multiply_issued'], len(grid.state))
        non_power = replace(grid, dt=8191)
        self.assertEqual(self.compare(non_power, fabric)['cycles'],
                         self.compare(non_power, replace(fabric, shift_dt=False))['cycles'])
        for dt in (1, 1 << 16, 1 << 17, 1 << 30):
            self.compare(replace(grid, dt=dt), fabric)

    def test_error_after_partial_writes_is_atomic(self):
        n = 64
        grid = Grid(8, 8, (0,) * (n - 1) + (ONE,), (0,) * n, (0,) * n,
                    (ONE,) * n, (0,) * (n - 1) + (MAXIMUM,), ONE)
        result = self.compare(grid, Fabric(lanes=2, banks=2))
        self.assertEqual(result['completed'], 0)
        self.assertEqual(result['fault'], 3)
        self.assertEqual(result['frames'][0]['fault_location'], (n - 1, 'next'))
        self.assertGreater(result['frames'][0]['stats']['trial_writes'], 0)
        self.assertEqual(result['state'], grid.state)
        self.compare(replace(grid, source=(0,) * n), Fabric(lanes=2, banks=2), 2)

    def test_keeps_previous_accepted_steps(self):
        grid = Grid(1, 1, (MAXIMUM - 2 * ONE,), (0,), (0,), (ONE,), (ONE,), ONE)
        result = self.compare(grid, Fabric(), 4)
        self.assertEqual((result['state'], result['completed'], result['fault']), ((MAXIMUM,), 2, 3))
        self.assertEqual(simulate(grid, Fabric(), 0)['state'], grid.state)

    def test_intermediate_overflow_and_wide_increment(self):
        grid = Grid(2, 1, (-(1 << 31), MAXIMUM), (0, 0), (0, 0), (ONE, ONE), (0, 0), ONE)
        self.assertEqual(self.compare(grid, Fabric())['fault'], 3)
        # Большое приращение допустимо, когда конечная сумма снова входит в Q16.16.
        wide = Grid(1, 1, (-(1 << 31),), (0,), (0,), (ONE,), (MAXIMUM,), 2 * ONE)
        self.assertEqual(self.compare(wide, Fabric())['state'], (MAXIMUM - 1,))


if __name__ == '__main__':
    unittest.main(verbosity=2)
