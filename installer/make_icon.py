"""Рисует графику сборки в installer/gen: TunnelVPN.ico для exe и установщика
и картинки мастера установки во всех масштабах экрана.

Сам значок рисует lib/tunnelvpn/icon.py: тот же ico нужен окну, запущенному из
исходников, и форма задана в одном месте. Картинки мастера — те же арка и
полосы, палитра — та же (icon.BG и статусы). --svg переписывает копию
assets/icon.svg — после правки рисунка (расхождение ловит tests/test_icon.py).

    python installer/make_icon.py [папка]       по умолчанию installer/gen
    python installer/make_icon.py --svg
"""

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

from tunnelvpn import icon  # noqa: E402

# Консоль на английской Windows — cp1252, и обычный print с кириллицей падает
# с UnicodeEncodeError. Именно на этом рушилась сборка в CI: скрипт делал своё
# дело, а потом умирал на строчке «собрано».
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


GEN = os.path.join(ROOT, "installer", "gen")

# Области картинок мастера Inno Setup 6.6+ (WizardStyle=modern, шрифт по
# умолчанию) при 100, 125, 150, 175, 200 и 250 % — из справки к
# WizardImageFile и WizardSmallImageFile. Setup берёт ближайшую по размеру.
WIZARD = ((202, 386), (269, 515), (336, 643), (403, 772), (430, 824), (534, 1022))
WIZARD_SMALL = (58, 77, 97, 116, 124, 159)


def wizard_image(w, h):
    """Большая картинка мастера («Добро пожаловать», «Готово»): арка
    туннеля, из неё по полу расходятся полосы цветов значка со свечением.
    Края — ровно icon.BG: картинка сливается с фоном страницы."""
    from PIL import Image, ImageDraw, ImageFilter

    ss = 3                                   # рисуем крупнее и уменьшаем
    W, H = w * ss, h * ss
    u = W / 202                              # единица — точка при 100 %
    cx, base, fan_y = W / 2, H * 0.50, H * 0.355
    r = W * 0.30

    img = Image.new("RGBA", (W, H), icon.rgba(icon.BG))

    # Пол: сетка в перспективе к устью туннеля, гаснет к горизонту.
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(grid)
    for i in range(-9, 10):
        g.line([(cx + i * W * 0.035, base), (cx + i * W * 0.32, H)],
               fill=(255, 255, 255, 16), width=max(1, round(u)))
    y, step = base, H * 0.012
    while y < H:
        g.line([(0, y), (W, y)], fill=(255, 255, 255, 16), width=max(1, round(u)))
        step *= 1.32
        y += step
    fade = Image.linear_gradient("L").resize((W, H))
    fade = fade.point(lambda v: 0 if v < 128 else min(255, (v - 128) * 3))
    grid.putalpha(Image.composite(grid.getchannel("A"), Image.new("L", (W, H), 0), fade))
    img.alpha_composite(grid)

    # Устье туннеля: внутри арки темнее, из глубины — слабый свет.
    mouth = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    m = ImageDraw.Draw(mouth)
    m.rectangle((cx - r, base - r * 0.55, cx + r, base), fill=(6, 6, 8, 255))
    m.ellipse((cx - r, base - r * 0.55 - r, cx + r, base - r * 0.55 + r), fill=(6, 6, 8, 255))
    img.alpha_composite(mouth.filter(ImageFilter.GaussianBlur(4 * u)))
    light = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(light).ellipse((cx - r * 0.3, fan_y - r * 0.3, cx + r * 0.3, fan_y + r * 0.3),
                                  fill=(255, 236, 214, 70))
    img.alpha_composite(light.filter(ImageFilter.GaussianBlur(16 * u)))

    # Полосы: клин от точки под аркой до низа, шире к зрителю.
    ends = ((-0.62, icon.OK), (0.0, icon.ACCENT), (0.62, icon.BUSY))
    beams = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    b = ImageDraw.Draw(beams)
    for dx, color in ends:
        ex = cx + dx * W
        n = math.hypot(ex - cx, H - fan_y)
        px, py = (H - fan_y) / n, -(ex - cx) / n         # нормаль к полосе
        w0, w1 = 1.2 * u, 7 * u
        b.polygon([(cx + px * w0, fan_y + py * w0), (ex + px * w1, H + py * w1),
                   (ex - px * w1, H - py * w1), (cx - px * w0, fan_y - py * w0)],
                  fill=icon.rgba(color))
    glow = beams.filter(ImageFilter.GaussianBlur(9 * u))
    img.alpha_composite(glow)
    img.alpha_composite(glow)
    img.alpha_composite(beams)

    # Арка поверх полос: они выходят из-под неё. Ноги — до пола.
    arch = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    a = ImageDraw.Draw(arch)
    top = base - r * 0.55
    aw = round(5 * u)
    a.arc((cx - r, top - r, cx + r, top + r), 180, 360, fill=icon.rgba(icon.LIGHT), width=aw)
    for x in (cx - r + aw / 2, cx + r - aw / 2):
        a.line([(x, top), (x, base)], fill=icon.rgba(icon.LIGHT), width=aw)
    halo = arch.filter(ImageFilter.GaussianBlur(7 * u))
    halo.putalpha(halo.getchannel("A").point(lambda v: v * 0.45))
    img.alpha_composite(halo)
    img.alpha_composite(arch)

    # Низ и верх уходят в фон, без шва со страницей.
    edge = Image.new("RGBA", (W, H), icon.rgba(icon.BG))
    v = Image.linear_gradient("L").resize((W, H))
    edge.putalpha(v.point(lambda t: max(0, (t - 200) * 4) if t > 200 else
                          max(0, (40 - t) * 6) if t < 40 else 0))
    img.alpha_composite(edge)
    return img.resize((w, h), Image.LANCZOS).convert("RGB")


def wizard_small(size):
    """Малая картинка (шапка внутренних страниц): значок без подложки."""
    return icon.draw(size, plate=False)


def write_all(out):
    """ico и картинки мастера в папку out. Возвращает список файлов."""
    from PIL import Image

    os.makedirs(out, exist_ok=True)
    files = [icon.write_ico(os.path.join(out, "TunnelVPN.ico"))]
    for w, h in WIZARD:
        path = os.path.join(out, f"wizard-{w}x{h}.png")
        # 128 цветов: полноцветные PNG всех масштабов весили бы ~470 КБ,
        # с 256 — ~330 КБ; ориентир — 300 вместе с малыми.
        # С рассеиванием: без него затухание полос идёт ступенями.
        # Pillow рассеивает только по готовой палитре — отсюда два прохода.
        img = wizard_image(w, h)
        pal = img.quantize(128, method=Image.Quantize.MEDIANCUT)
        img.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG).save(path, optimize=True)
        files.append(path)
    for s in WIZARD_SMALL:
        path = os.path.join(out, f"wizard-small-{s}.png")
        wizard_small(s).save(path, optimize=True)
        files.append(path)
    return files


def main():
    if sys.argv[1:] == ["--svg"]:
        out = os.path.join(ROOT, "assets", "icon.svg")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(icon.svg())
        print(f"собрано: {out}")
        return
    out = sys.argv[1] if len(sys.argv) > 1 else GEN
    for path in write_all(out):
        print(f"собрано: {path} ({os.path.getsize(path) // 1024} КБ)")


if __name__ == "__main__":
    main()
