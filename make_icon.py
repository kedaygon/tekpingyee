from PIL import Image, ImageDraw, ImageFont

BG = (15, 42, 56, 255)
FG = (79, 217, 196, 255)
FONT = "C:/Windows/Fonts/malgunbd.ttf"


def draw(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = max(2, size // 5)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=BG)
    fs = int(size * 0.68)
    try:
        f = ImageFont.truetype(FONT, fs)
    except Exception:
        f = ImageFont.load_default()
    box = d.textbbox((0, 0), "핑", font=f)
    x = (size - (box[2] - box[0])) / 2 - box[0]
    y = (size - (box[3] - box[1])) / 2 - box[1]
    d.text((x, y), "핑", font=f, fill=FG)
    return img


sizes = [16, 24, 32, 48, 64, 128, 256]
imgs = [draw(s) for s in sizes]
imgs[-1].save("ping.ico", format="ICO",
              sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
print("ping.ico 생성됨")
