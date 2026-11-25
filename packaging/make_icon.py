"""アプリのアイコン(涙のしずく + 波形)を作る。web/icon.png と packaging/icon.ico を書き出す。

    .venv\\Scripts\\python.exe packaging\\make_icon.py
"""

import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 1024


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([40, 40, S - 40, S - 40], radius=200, fill=(30, 33, 40, 255))
    # 涙のしずく(上がとがった形)
    cx, cy, r = S / 2, S * 0.60, S * 0.26
    pts = []
    for i in range(361):
        a = math.radians(i)
        # 円の上半分を頂点へ引き伸ばす
        x = cx + r * math.sin(a)
        y = cy + r * math.cos(a)
        if math.cos(a) < 0:
            k = -math.cos(a)
            y = cy - r * k - (r * 0.95) * k ** 2.2
            x = cx + r * math.sin(a) * (1 - 0.55 * k ** 1.5)
        pts.append((x, y))
    d.polygon(pts, fill=(95, 179, 217, 255))
    # しずくの中の波形(泣き声のふるえ)
    wave = []
    for i in range(201):
        u = i / 200
        x = cx - r * 0.62 + u * r * 1.24
        y = cy + 0.06 * r + math.sin(u * math.pi * 3.2) * r * 0.20 * math.sin(u * math.pi)
        wave.append((x, y))
    d.line(wave, fill=(240, 160, 75, 255), width=int(S * 0.035), joint="curve")
    return img


def main():
    img = draw()
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(ROOT, "web", "icon.png"))
    img.save(os.path.join(ROOT, "packaging", "icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon written")


if __name__ == "__main__":
    main()
