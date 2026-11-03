# 02_ring_chase.py - NeoPixel ring test: red, green and blue lights run around
# the 16-LED ring. Needs no libraries.
#
# Save as code.py on CIRCUITPY. Wiring: Data Input -> GP18, Power 5V DC -> 5V, Ground -> GND.

import board
import digitalio
import time
import neopixel_write

RING_PIN = board.GP18
N = 16
SPEED = 0.1          # seconds per step

ring = digitalio.DigitalInOut(RING_PIN)
ring.direction = digitalio.Direction.OUTPUT

# each colour is written as (green, red, blue)
RED = [0, 30, 0]
GREEN = [30, 0, 0]
BLUE = [0, 0, 30]
OFF = [0, 0, 0]

while True:
    for i in range(N):
        leds = []
        for n in range(N):
            if n == i:
                leds += RED
            elif n == (i + 1) % N:
                leds += GREEN
            elif n == (i + 2) % N:
                leds += BLUE
            else:
                leds += OFF
        neopixel_write.neopixel_write(ring, bytearray(leds))
        time.sleep(SPEED)
