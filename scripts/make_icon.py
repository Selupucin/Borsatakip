"""Borsa Bot için Windows ICO ikonu üretir.

Yükselen mum grafiği + gradient arka plan. PIL/Pillow ile.

Çıktı: app/resources/icon.ico (256x256, 128, 64, 48, 32, 16 size'ları)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "app" / "resources"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = OUT_DIR / "icon.ico"

SIZE = 512  # ana çözünürlük; ICO içine downscale ile gömülür


def lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def make_master(size: int = SIZE) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ---- Yuvarlak köşeli kare arka plan (rounded) ----
    bg_top = (15, 25, 50)        # koyu lacivert
    bg_bot = (10, 60, 90)        # koyu mavi-cyan
    radius = int(size * 0.22)
    bg_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg_img)
    # Gradient çiz
    for y in range(size):
        t = y / (size - 1)
        c = lerp_color(bg_top, bg_bot, t)
        bg_draw.line([(0, y), (size, y)], fill=c + (255,))
    # Mask: rounded rectangle
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    bg_img.putalpha(mask)
    img.paste(bg_img, (0, 0), bg_img)

    # ---- Yükselen çizgi grafik ----
    line_pts = [
        (0.10, 0.78),
        (0.22, 0.65),
        (0.34, 0.72),
        (0.46, 0.55),
        (0.58, 0.62),
        (0.70, 0.40),
        (0.82, 0.30),
        (0.92, 0.22),
    ]
    pts = [(int(x * size), int(y * size)) for x, y in line_pts]

    # Glow (blur) — çizginin altına
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.line(pts, fill=(0, 220, 130, 150), width=int(size * 0.04))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=size * 0.018))
    img.alpha_composite(glow)

    # Ana çizgi
    draw.line(pts, fill=(46, 204, 113, 255), width=int(size * 0.025), joint="curve")

    # Noktalar
    dot_r = int(size * 0.025)
    for x, y in pts:
        draw.ellipse(
            (x - dot_r, y - dot_r, x + dot_r, y + dot_r),
            fill=(46, 204, 113, 255),
            outline=(255, 255, 255, 255),
            width=max(1, int(size * 0.006)),
        )

    # ---- Sağ üst köşe "B" harfi (Borsa Bot) ----
    try:
        from PIL import ImageFont

        font_size = int(size * 0.20)
        # Sistemde varsayılan bold font
        font = None
        for name in ("arialbd.ttf", "arial.ttf", "segoeui.ttf"):
            try:
                font = ImageFont.truetype(name, font_size)
                break
            except OSError:
                continue
        if font is not None:
            text = "B"
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            # Sol alt köşe
            margin = int(size * 0.08)
            x_pos = margin
            y_pos = size - margin - th - bbox[1]
            # Glow effect
            shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow)
            sd.text((x_pos, y_pos), text, font=font, fill=(0, 0, 0, 180))
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=size * 0.012))
            img.alpha_composite(shadow)
            draw.text((x_pos, y_pos), text, font=font, fill=(255, 255, 255, 240))
    except Exception:
        pass

    return img


def main() -> None:
    master = make_master(SIZE)
    # ICO için tipik boyutlar
    sizes = [(256, 256), (128, 128), (96, 96), (64, 64), (48, 48), (32, 32), (16, 16)]
    icons = [master.resize(s, Image.Resampling.LANCZOS) for s in sizes]
    # PNG da kaydedelim (önizleme için)
    master.resize((256, 256), Image.Resampling.LANCZOS).save(OUT.with_suffix(".png"))
    icons[0].save(
        OUT,
        format="ICO",
        sizes=sizes,
        append_images=icons[1:],
    )
    print(f"Icon written: {OUT}")
    print(f"PNG preview:  {OUT.with_suffix('.png')}")


if __name__ == "__main__":
    main()
