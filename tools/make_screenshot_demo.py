"""Create harmless sample candidate images for public UI screenshots."""

from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / "screenshot_demo" / "Paper Lantern"
page = root / "_candidates" / "page01_lantern.png_512x512"
page.mkdir(parents=True, exist_ok=True)
(root / "lantern.atlas").write_text(
    "lantern.png\nsize: 512,512\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\nlantern\n  rotate: false\n  xy: 0, 0\n  size: 512, 512\n  orig: 512, 512\n  offset: 0, 0\n  index: -1\n",
    encoding="utf-8",
)
lines = ["page_index: 1", "atlas_page: lantern.png", "requested_size: 512x512"]
for rank, (color, accent) in enumerate(
    [("#ed795f", "#f9d885"), ("#e5b448", "#ffe0a8"), ("#789cda", "#d3e4ff")], 1
):
    image = Image.new("RGBA", (512, 512), (35, 40, 63, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 96, 392, 424), fill=color, outline=accent, width=12)
    draw.line((256, 42, 256, 98), fill=accent, width=10)
    draw.arc((163, 130, 350, 365), 90, 270, fill=accent, width=8)
    draw.line((256, 418, 256, 482), fill=accent, width=10)
    image.save(page / f"{rank:04d}_score{2000-rank*100}_lantern_{rank}.png")
    lines.append(f"{rank:04d} score= {2000-rank*100} src=demo_lantern_{rank}.png [synthetic sample]")
(page / "_manifest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(root.parent)
