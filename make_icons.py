"""Generate PWA icons (run once; needs pillow)."""
from PIL import Image, ImageDraw
def icon(size, pad=0.0):
    im = Image.new("RGB", (size, size), "#0b0d10"); d = ImageDraw.Draw(im)
    m = size * (0.16 + pad); w = max(4, int(size * 0.09))
    d.ellipse([m, m, size - m, size - m], outline="#242a33", width=w)
    d.arc([m, m, size - m, size - m], start=-90, end=200, fill="#16ec06", width=w)
    r = size * 0.07; c = size / 2
    d.ellipse([c - r, c - r, c + r, c + r], fill="#0093e7")
    return im
for s in (180, 192, 512): icon(s).save(f"static/icons/icon-{s}.png")
icon(512, pad=0.08).save("static/icons/icon-512-maskable.png")
