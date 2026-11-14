import json
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1] / "sample_data"


def main() -> None:
    for slug, document in json.loads((ROOT / "corpus.json").read_text()).items():
        canvas = Canvas(str(ROOT / f"{slug}.pdf"), pagesize=letter, invariant=True)
        canvas.setTitle(document["title"] + " — synthetic sample")
        for number, page in enumerate(document["pages"], 1):
            canvas.setFillColor(HexColor("#155747"))
            canvas.rect(0, 597, 612, 195, fill=1, stroke=0)
            canvas.setFillColor(HexColor("#ffffff"))
            canvas.setFont("Helvetica-Bold", 11)
            canvas.drawString(55, 743, "DOCULENS AI  /  SYNTHETIC SAMPLE")
            canvas.setFont("Helvetica-Bold", 32)
            canvas.drawString(55, 685, document["title"])
            canvas.setFont("Helvetica", 14)
            canvas.drawString(55, 650, page["heading"])
            y = 540
            for fact in page["facts"]:
                canvas.setFillColor(HexColor("#155747"))
                canvas.circle(59, y + 4, 3, fill=1, stroke=0)
                canvas.setFillColor(HexColor("#182327"))
                canvas.setFont("Helvetica", 13)
                for line in simpleSplit(fact, "Helvetica", 13, 470):
                    canvas.drawString(73, y, line)
                    y -= 20
                y -= 23
            canvas.setFillColor(HexColor("#536369"))
            canvas.setFont("Helvetica", 10)
            canvas.drawString(
                55, 52, "Original synthetic material for demonstration. No real policies."
            )
            canvas.drawRightString(557, 52, f"{number} / {len(document['pages'])}")
            canvas.showPage()
        canvas.save()


if __name__ == "__main__":
    main()
