"""
keytest.py — diagnostic: prints exactly what pynput sees for every key event,
both raw and canonicalised, plus the normalised name VoiceFlow's hotkey.py
would use. Run it, hold Ctrl+Alt+Space, release, then press Esc to quit.
"""

from pynput import keyboard
from hotkey import _key_name  # reuse the exact same normalisation VoiceFlow uses

pressed = set()


def on_press(key):
    name = _key_name(listener, key)
    pressed.add(name)
    print(f"PRESS   raw={key!r:20} -> name={name!r:10}  currently down: {sorted(str(p) for p in pressed)}")
    if {"ctrl", "alt", "space"} <= pressed:
        print(">>> COMBO DETECTED — VoiceFlow would start recording here <<<")


def on_release(key):
    name = _key_name(listener, key)
    pressed.discard(name)
    print(f"RELEASE raw={key!r:20} -> name={name!r}")
    if key == keyboard.Key.esc:
        print("Esc pressed — exiting.")
        return False


listener = keyboard.Listener(on_press=on_press, on_release=on_release)
listener.start()
print("Listening… hold Ctrl+Alt+Space, then press Esc to quit.\n")
listener.join()
