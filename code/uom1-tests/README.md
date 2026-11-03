# UOM-1 hardware tests and calibration

CircuitPython 9 on a Raspberry Pi Pico 2 W. To run a Pico script, copy it to the
CIRCUITPY drive and rename it `code.py`; it starts as soon as you save.

| Folder | File | What it does |
|---|---|---|
| 1_pico_light_test | pico_led_blink.py | Blinks the Pico's own LED: shows the code is running |
| 2_neopixel_test | neopixel_rgb_cycle.py | Whole ring red, green, blue |
| 2_neopixel_test | neopixel_chase.py | Red, green and blue lights running around the ring |
| 3_button_test | button_test.py | Button press prints a message, lights the LED and turns the ring red |
| 4_calibration | calibrate.py | Pico: multi-point calibration, prints `CSV` lines |
| 4_calibration | calibration_data.csv | Our run of 27 October 2026 (3 runs, 0 to 188 g) |
| 4_calibration | make_curve.py | Laptop: fits the line and draws the curve |
| 4_calibration | calibration_curve.png | Result: 2220.7 counts/g, R² = 0.99999, RMS error 0.16 g |

## Wiring

| Part | Part pin | Pico socket |
|---|---|---|
| NeoPixel ring | Data Input, Power 5V DC, Ground | GP18, 5V, GND |
| Button | one leg, diagonal leg | GP17, GND |
| HX711 | VCC, GND, DT, SCK | 3V3, GND, GP5, GP6 |
| Load cell to HX711 | red, black, white, green | E+, E−, A−, A+ |

## Calibration steps

1. Copy `calibrate.py` to the Pico as `code.py`, open the Serial Monitor, and follow the prompts.
2. Copy every line starting with `CSV` into `calibration_data.csv` on the laptop.
3. `pip3 install numpy matplotlib`, then `python3 make_curve.py`.
4. Use the calibration factor it prints in the measuring code.
