from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "launcher_assets" / "logo.png"
DESTINATION = Path(__file__).with_name("TaikoNautsLauncher.ico")
SIZES = [
    (16, 16),
    (20, 20),
    (24, 24),
    (32, 32),
    (40, 40),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
]


with Image.open(SOURCE) as source:
    source.convert("RGBA").save(DESTINATION, format="ICO", sizes=SIZES)

print(DESTINATION)
