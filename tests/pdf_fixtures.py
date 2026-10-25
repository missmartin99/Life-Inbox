"""Tiny PDF bytes for automated tests (no committed binaries)."""


SAMPLE_TEXT = "Life Inbox PDF fixture text for extraction tests."


def make_text_pdf(
    text: str = SAMPLE_TEXT,
    *,
    title: str | None = "Sample Fixture PDF",
) -> bytes:
    """Build a minimal valid PDF with extractable text."""
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    title_line = ""
    if title:
        safe_title = title.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        title_line = f" /Title ({safe_title})"
    return f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R{title_line} >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
  /Resources << /Font << /F1 5 0 R >> >>
  /Contents 4 0 R >> endobj
4 0 obj << /Length 68 >> stream
BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000078 00000 n 
0000000135 00000 n 
0000000286 00000 n 
0000000406 00000 n 
trailer << /Size 6 /Root 1 0 R >>
startxref
485
%%EOF
""".encode("latin-1")


def make_blank_pdf() -> bytes:
    return b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj
4 0 obj << /Length 0 >> stream
endstream endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000212 00000 n 
trailer << /Size 5 /Root 1 0 R >>
startxref
292
%%EOF
"""
