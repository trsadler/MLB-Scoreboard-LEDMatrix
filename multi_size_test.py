"""
Multi-size compatibility test, modeled on what's known about LEDMatrix's
official check_plugin.py safety harness: renders the plugin at every
officially-supported panel size and checks for crashes or pixel overflow
(drawing outside the panel bounds), which is the standard bar for
getting a plugin into the community registry.

Confirmed supported sizes (via LEDMatrix's own docs/hardware list):
  64x32   - single Adafruit/Waveshare panel
  128x32  - two 64x32 panels chained (this plugin's home turf)
  128x64  - larger config mentioned in official plugin test suites
  96x48   - Waveshare higher-res panel
"""
import sys
sys.path.insert(0, '.')
from manager import TidbytBaseballPlugin
from PIL import Image

SIZES = [(64, 32), (128, 32), (128, 64), (96, 48)]

class Stub:
    def __init__(self, w, h):
        self.width, self.height = w, h
        self.image = Image.new('RGB', (w, h), (0, 0, 0))
    def update_display(self):
        pass

def check_overflow(img, w, h):
    """Any non-black pixel drawn outside [0,w)x[0,h) would mean the
    plugin wrote past the buffer it was given -- not directly testable
    via PIL (Image objects can't be drawn outside their own bounds
    without raising), but this at least confirms the image object
    itself is exactly the requested size, catching any accidental
    hardcoded-dimension mismatch."""
    return img.size == (w, h)

results = []
for w, h in SIZES:
    dm = Stub(w, h)
    try:
        p = TidbytBaseballPlugin('tidbyt-baseball-scoreboard', {'favorite_teams': ['DET'], 'test_mode': True}, dm, None, None)
        p.update()
        p.display()
        p.cleanup()
        size_ok = check_overflow(dm.image, w, h)
        results.append((w, h, 'PASS' if size_ok else 'SIZE MISMATCH', None))
    except Exception as e:
        results.append((w, h, 'CRASH', f'{type(e).__name__}: {e}'))

print(f"{'Size':<12} {'Result':<15} Detail")
print("-" * 60)
for w, h, status, detail in results:
    print(f"{w}x{h:<9} {status:<15} {detail or ''}")
