"""Paginated Letter-size PDFs, without remote assets or customer-supplied markup."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@lru_cache(maxsize=1)
def register_fonts():
    folder = Path(reportlab.__file__).parent / "fonts"
    pdfmetrics.registerFont(TTFont("BookingRegular", str(folder / "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("BookingBold", str(folder / "VeraBd.ttf")))


def render_pdf(document):
    register_fonts()
    output = BytesIO()
    styles = {"body": ParagraphStyle("body", fontName="BookingRegular", fontSize=10, leading=15, textColor=colors.HexColor("#202524")),
              "label": ParagraphStyle("label", fontName="BookingRegular", fontSize=9, leading=13, textColor=colors.HexColor("#616967")),
              "heading": ParagraphStyle("heading", fontName="BookingBold", fontSize=23, leading=29),
              "bold": ParagraphStyle("bold", fontName="BookingBold", fontSize=10, leading=15),
              "amount": ParagraphStyle("amount", fontName="BookingRegular", fontSize=10, leading=15, alignment=TA_RIGHT)}
    styles["amount_bold"] = ParagraphStyle("amount_bold", parent=styles["amount"], fontName="BookingBold")
    styles["amount_label"] = ParagraphStyle("amount_label", parent=styles["label"], alignment=TA_RIGHT)
    def p(value, style="body"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])
    def table(rows, widths, header=False):
        result = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
        result.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#e2e5e4"))]))
        return result
    story = [p(document["business"], "bold"), Spacer(1, 12), p(document["title"], "heading"),
             Spacer(1, 10), p("Booking reference: " + document["reference"], "label"),
             p("Issued " + document["generated"], "label"), Spacer(1, 20)]
    details = [[p("CUSTOMER", "label"), p(document["booking_kind"].upper(), "label")],
               [p(document["customer"] + "\n" + document["email"]),
                p(document["booking"] + "\n" + document["when"] + "\n" + document["location"]
                  + ("\n" + document["address"] if document["address"] else "")
                  + f'\n{document["quantity"]} participants | {document["status"]}')]]
    story.extend([table(details, [220, 296]), Spacer(1, 18)])
    rows = [[p(line["label"], "bold" if line.get("strong") else "body"),
             p(line["amount"], "amount_bold" if line.get("strong") else "amount")]
            for line in document["lines"]]
    story.append(KeepTogether([table(rows, [340, 176]), Spacer(1, 12)]))
    for note in (document["tax_note"], document["note"]):
        if note:
            story.extend([p(note, "label"), Spacer(1, 8)])
    if document["history"]:
        heading = p("Payment history", "bold")
        heading.keepWithNext = True
        story.extend([Spacer(1, 16), heading])
        history = [[p(label, "label") for label in ("Date", "Transaction", "Method")]
                   + [p("Amount", "amount_label")]]
        history += [[p(row["date"]), p(row["type"]),
                     p(row["method"] + ("\n" + row["reference"] if row["reference"] else "")), p(row["amount"], "amount")]
                    for row in document["history"]]
        story.append(table(history, [93, 91, 222, 110], header=True))
    def footer(canvas, doc):
        canvas.setFont("BookingRegular", 8)
        canvas.setFillColor(colors.HexColor("#616967"))
        canvas.drawString(48, 26, document["title"] + " | " + document["reference"])
        canvas.drawRightString(564, 26, f"Page {doc.page}")
    pdf = SimpleDocTemplate(output, pagesize=letter, leftMargin=48, rightMargin=48,
        topMargin=42, bottomMargin=48, title=document["title"], author=document["business"])
    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
