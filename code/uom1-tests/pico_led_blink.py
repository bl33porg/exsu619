# pico_led_blink.py - is the Pico running? Blinks the small green LED on the
# Pico board twice a second. Needs no wiring and no libraries.
#
# Save as code.py on CIRCUITPY.
#   LED blinks       -> the Pico and your code are working
#   LED stays dark   -> the code is not running: unplug and replug the USB, check the drive is not full

import board
import digitalio
import time

led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT

print("Pico is running")
while True:
    led.value = not led.value
    time.sleep(0.25)
