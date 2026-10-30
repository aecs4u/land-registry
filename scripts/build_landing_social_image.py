"""Build the 1200×630 raster preview used by the public landing page."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "land_registry" / "static" / "land-registry-social.png"
WIDTH, HEIGHT = 1200, 630


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf")
        if bold
        else ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/freefont/FreeSans.ttf")
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = image.load()
    start = (14, 38, 63)
    end = (26, 84, 144)
    for y in range(HEIGHT):
        blend = y / (HEIGHT - 1)
        color = tuple(round(start[channel] * (1 - blend) + end[channel] * blend) for channel in range(3))
        for x in range(WIDTH):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image, "RGBA")
    # A quiet cadastral grid makes the subject legible without implying that
    # the preview is a survey or an authoritative parcel boundary.
    blocks = [
        [(690, 92), (855, 74), (872, 204), (716, 218)],
        [(875, 72), (1077, 95), (1055, 222), (890, 205)],
        [(712, 232), (870, 218), (861, 373), (696, 354)],
        [(888, 222), (1052, 239), (1071, 379), (871, 365)],
        [(686, 369), (857, 385), (838, 551), (664, 520)],
        [(874, 382), (1078, 398), (1094, 540), (853, 555)],
    ]
    for polygon in blocks:
        draw.polygon(polygon, fill=(255, 255, 255, 12), outline=(220, 239, 250, 115), width=3)
    draw.polygon([(901, 246), (1034, 258), (1047, 366), (884, 354)], fill=(40, 167, 69, 218), outline=(219, 255, 225, 255), width=5)
    draw.line([(740, 104), (767, 514)], fill=(220, 239, 250, 70), width=2)
    draw.line([(1002, 110), (986, 526)], fill=(220, 239, 250, 70), width=2)
    draw.ellipse((1088, 117, 1104, 133), fill=(255, 255, 255, 220))
    draw.ellipse((1088, 117, 1104, 133), outline=(26, 84, 144, 255), width=3)

    # Match the cadastral mark used by the site's existing wordmark.
    draw.rounded_rectangle((72, 76, 164, 168), radius=18, fill=(26, 84, 144, 255), outline=(255, 255, 255, 65), width=2)
    for x1, y1, x2, y2, color in (
        (87, 91, 119, 122, (255, 255, 255, 255)),
        (124, 91, 150, 122, (255, 255, 255, 255)),
        (87, 130, 123, 153, (255, 255, 255, 255)),
        (128, 130, 150, 153, (255, 255, 255, 255)),
        (91, 95, 115, 118, (40, 167, 69, 255)),
        (128, 95, 146, 118, (40, 167, 69, 255)),
        (91, 134, 119, 149, (40, 167, 69, 255)),
        (132, 134, 146, 149, (40, 167, 69, 255)),
    ):
        draw.rectangle((x1, y1, x2, y2), fill=color)

    draw.text((190, 92), "Land Registry Viewer", font=_font(39, bold=True), fill=(255, 255, 255, 255))
    draw.text((76, 236), "Italian cadastral map", font=_font(46, bold=True), fill=(255, 255, 255, 255))
    draw.text((78, 307), "Find parcels, explore public data,", font=_font(27), fill=(218, 233, 246, 255))
    draw.text((78, 349), "understand the place around them.", font=_font(27), fill=(218, 233, 246, 255))
    draw.rounded_rectangle((78, 439, 401, 494), radius=27, fill=(255, 255, 255, 24), outline=(255, 255, 255, 85), width=2)
    draw.text((101, 454), "MAP  ·  DATA  ·  SOURCES", font=_font(19, bold=True), fill=(255, 255, 255, 255))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, format="PNG", optimize=True)
    print(f"Wrote {OUTPUT} ({WIDTH}×{HEIGHT})")


if __name__ == "__main__":
    main()
