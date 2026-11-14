from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

MEDIA = Path(__file__).resolve().parents[1] / "portfolio" / "media"


def main() -> None:
    large = ImageFont.load_default(size=52)
    regular = ImageFont.load_default(size=22)
    small = ImageFont.load_default(size=16)
    canvas = Image.new("RGB", (1000, 750), "#f4f6f4")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 1000, 293), fill="#155747")
    draw.text((54, 42), "DOCULENS AI  /  INDEPENDENT DEMO", font=small, fill="#c2e9da")
    draw.text((51, 73), "AI Document Q&A", font=large, fill="white")
    draw.text((51, 139), "with RAG & Citations", font=large, fill="white")
    draw.text(
        (55, 219),
        "PDF ingestion   |   Evidence search   |   Verified page links",
        font=regular,
        fill="#e2f1eb",
    )
    with Image.open(MEDIA / "cited-answer.png") as screenshot:
        # Use the actual two-panel workspace area from the desktop capture.
        width, height = screenshot.size
        crop = screenshot.crop(
            (int(width * 0.052), int(height * 0.292), int(width * 0.948), int(height * 0.845))
        )
        fitted = ImageOps.contain(crop, (904, 423), Image.Resampling.LANCZOS)
        canvas.paste(fitted, ((1000 - fitted.width) // 2, 282))
    draw.text(
        (55, 710),
        "Local workspace shown with original synthetic sample PDFs.",
        font=small,
        fill="#405853",
    )
    canvas.save(MEDIA / "cover.png", optimize=True)


if __name__ == "__main__":
    main()
