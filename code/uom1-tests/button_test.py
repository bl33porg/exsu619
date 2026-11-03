# button_test.py - press the button: "button pressed" prints in the Serial Monitor,
# the Pico's LED lights and the ring turns red. Released: ring yellow, LED off.
# Needs no libraries.
#
# Save as code.py on CIRCUITPY.
# Wiring: one button leg -> GP17, the diagonal leg -> GND.
#         Ring (optional): Data Input -> GP18, Power 5V DC -> 5V, Ground -> GND.
#
# Nothing prints? Turn the button 90 degrees: some buttons have legs that are always connected.

import board
import digitalio
import time
import neopixel_write

BUTTON_PIN = board.GP17
RING_PIN = board.GP18

button = digitalio.DigitalInOut(BUTTON_PIN)
button.direction = digitalio.Direction.INPUT
button.pull = digitalio.Pull.UP          # reads True when released, False when pressed

led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT
ring = digitalio.DigitalInOut(RING_PIN)
ring.direction = digitalio.Direction.OUTPUT


def ring_colour(r, g, b):
    neopixel_write.neopixel_write(ring, bytearray([g, r, b] * 16))   # ring expects G, R, B


was_pressed = False
ring_colour(40, 40, 0)                   # yellow = waiting
print("Press the button")
while True:
    pressed = not button.value
    if pressed and not was_pressed:
        print("button pressed")
        led.value = True
        ring_colour(40, 0, 0)            # red
    elif was_pressed and not pressed:
        print("button released")
        led.value = False
        ring_colour(40, 40, 0)
    was_pressed = pressed
    time.sleep(0.02)                     # simple debounce
