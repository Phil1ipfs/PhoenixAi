"""Builders for test documents: a tiny PDF writer, a DOCX writer (zipfile + XML), scanned/encrypted PDFs."""
import io
import zipfile


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str]) -> bytes:
    """One text page per list item; a newline starts a new line. Uses the built-in Helvetica font (extractable text layer)."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for i, text in enumerate(pages):
        page_no, content_no = 4 + 2 * i, 5 + 2 * i
        kids.append(f"{page_no} 0 R")
        lines = text.split("\n")
        stream = "BT /F1 11 Tf 72 740 Td 14 TL " + " T* ".join(f"({_esc(l)}) Tj" for l in lines) + " ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_no} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>".encode())
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode())
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_scanned_pdf(n_pages: int = 3) -> bytes:
    """Pages with no text layer at all (what a scan looks like to a text extractor)."""
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(n_pages):
        w.add_blank_page(612, 792)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def make_encrypted_pdf() -> bytes:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf(["secret page text here, long enough to count"]))))
    w.encrypt("hunter2")
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(items: list, extra_files: dict | None = None) -> bytes:
    """items: ('h1'|'h2'|'p', text) or ('table', [[cell, ...], ...])."""
    body = []
    for kind, val in items:
        if kind == "table":
            rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r) + "</w:tr>" for r in val)
            body.append(f"<w:tbl>{rows}</w:tbl>")
        else:
            style = {"h1": "Heading1", "h2": "Heading2"}.get(kind)
            ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
            body.append(f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{val}</w:t></w:r></w:p>')
    doc = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W_NS}"><w:body>{"".join(body)}</w:body></w:document>'
    styles = (f'<?xml version="1.0"?><w:styles xmlns:w="{W_NS}">'
              '<w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
              '<w:style w:styleId="Heading2"><w:name w:val="heading 2"/></w:style></w:styles>')
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        for k, v in (extra_files or {}).items():
            z.writestr(k, v)
    return b.getvalue()
