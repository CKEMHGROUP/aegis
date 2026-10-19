"""Creates tools/sample_scan.pdf and .png: a SYNTHETIC scanned page with fake data, for OCR checks.
Run:  python tools/make_sample_scan.py     (never put real documents in the Project or the repo)"""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 1240, 1754   # A4 at 150 dpi
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
title, body = ImageFont.load_default(size=44), ImageFont.load_default(size=30)
d.text((90, 100), "FATURA n.º 2026/145", fill="black", font=title)
lines = ["Cliente: Maria Santos", "NIF: 123456789", "Email: maria.santos@example.pt", "Telefone: +351 912 345 678",
         "IBAN: PT50 0002 0123 1234 5678 9015 4", "Morada: Rua das Flores 12, Lisboa", "",
         "Projeto Atlas: consultoria, março 2026", "Total: 1.250,00 EUR", "",
         "This is a SYNTHETIC test page with fake data.", "Contact: John Smith, john.smith@example.com"]
y = 220
for line in lines:
    d.text((90, y), line, fill="black", font=body)
    y += 60
here = os.path.dirname(os.path.abspath(__file__))
img.save(os.path.join(here, "sample_scan.png"))
img.save(os.path.join(here, "sample_scan.pdf"), resolution=150)
print("wrote tools/sample_scan.png and tools/sample_scan.pdf")
