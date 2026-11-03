# neopixel_rgb_cycle.py - turns the whole 16-LED ring red, then green, then blue.
# Needs no libraries.
#
# Save as code.py on CIRCUITPY.
# Wiring: Data Input -> GP18, Power 5V DC -> 5V, Ground -> GND.

import board
import digitalio
import time
import neopixel_write

RING_PIN = board.GP18

ring = digitalio.DigitalInOut(RING_PIN)
ring.direction = digitalio.Direction.OUTPUT

# each colour is written as (green, red, blue)
RED = [0, 30, 0]
GREEN = [30, 0, 0]
BLUE = [0, 0, 30]

while True:
    for colour in (RED, GREEN, BLUE):
        neopixel_write.neopixel_write(ring, bytearray(colour * 16))
        time.sleep(0.5)
