# calibrate.py - multi-point load cell calibration on the Pico.
#
# Save as code.py on CIRCUITPY, open the Serial Monitor, and follow the prompts:
# put each mass on, wait until it hangs still, press Enter. The masses go up,
# then back down (to check hysteresis), and the whole run repeats 3 times.
#
# Copy every line starting with "CSV" into calibration_data.csv, then run
# make_curve.py on the laptop to get the calibration factor and the curve.
# Wiring (HX711): VCC -> 3V3, GND -> GND, DT -> GP5, SCK -> GP6.

import board
import digitalio
import time

DT_PIN = board.GP5
SCK_PIN = board.GP6
MASSES = [0, 1, 3, 8, 18, 38, 88, 188]   # grams; change to match your mass set
REPEATS = 3
SAMPLES = 40                              # readings averaged per point (about 4 s)

dt = digitalio.DigitalInOut(DT_PIN)
dt.direction = digitalio.Direction.INPUT
sck = digitalio.DigitalInOut(SCK_PIN)
sck.direction = digitalio.Direction.OUTPUT
sck.value = False


def read_raw(timeout=1.0):
    start = time.monotonic()
    while dt.value:
        if time.monotonic() - start > timeout:
            return None
    value = 0
    for _ in range(24):
        sck.value = True
        value = (value << 1) | dt.value
        sck.value = False
    sck.value = True
    sck.value = False
    if value & 0x800000:
        value -= 0x1000000
    return value


def measure(n=SAMPLES):
    vals = []
    while len(vals) < n:
        r = read_raw()
        if r is None:
            print("!! HX711 not answering: check the wires")
            continue
        vals.append(r)
    mean = sum(vals) / n
    sd = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
    return mean, sd


print("Hang the EMPTY urimeter on the scale.")
print("CSV,repeat,direction,mass_g,raw_mean,raw_sd")
points = []
for rep in range(1, REPEATS + 1):
    order = [("up", m) for m in MASSES] + [("down", m) for m in reversed(MASSES[:-1])]
    for direction, mass in order:
        input("Put {} g on, wait until still, then press Enter: ".format(mass))
        mean, sd = measure()
        print("CSV,{},{},{},{:.1f},{:.1f}".format(rep, direction, mass, mean, sd))
        if sd > 500:
            print("   (noisy: something moved - consider redoing this run)")
        points.append((mass, mean))

# least-squares straight line: raw = factor * mass + zero
n = len(points)
mx = sum(p[0] for p in points) / n
my = sum(p[1] for p in points) / n
sxx = sum((p[0] - mx) ** 2 for p in points)
sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
factor = sxy / sxx
zero = my - factor * mx
res = [(p[1] - zero) / factor - p[0] for p in points]
rms = (sum(r * r for r in res) / n) ** 0.5
print("RESULT counts_per_g = {:.3f}".format(factor))
print("RESULT zero_counts  = {:.1f}".format(zero))
print("RESULT rms_residual_g = {:.3f}".format(rms))
print("RESULT worst_residual_g = {:.3f}".format(max(res, key=abs)))
print("Done. Copy every line starting with CSV into calibration_data.csv on the laptop.")
while True:
    time.sleep(1)
