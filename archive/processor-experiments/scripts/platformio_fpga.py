Import("env")
import subprocess

def make_target(name):
    def action(source, target, env_):
        return subprocess.call(["make", name])
    return action

for name in ("doctor", "probe", "lint", "sim", "sim-синтез", "synth", "bitstream", "program", "модели", "проверить-плату", "сравнить", "сравнить-плату", "диагностика", "проверить-диагностику", "проверить-автономность"):
    env.AddCustomTarget("fpga-" + name, None, [make_target(name)], title="FPGA " + name, description="Выполнить make " + name, always_build=True)

