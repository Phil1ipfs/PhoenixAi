"""Standalone PDF text extractor, run in a child process (`python -I pdf_worker.py <file> <max_pages>`).

Isolation means a malformed or hostile PDF can hang or crash only this child (the parent enforces a timeout), never the
PHOENIX server. It imports nothing from PHOENIX, reads one file and prints one JSON object. No network access.
"""
import json
import sys


def main(path: str, max_pages: int) -> dict:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        return {"error": "pypdf_missing", "message": "The pypdf package is not installed (pip install pypdf)."}
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:  # noqa: BLE001
                ok = 0
            if not ok:
                return {"error": "encrypted", "message": "This PDF is password-protected. Remove the password and upload it again."}
        total = len(reader.pages)
        pages = []
        for i, page in enumerate(reader.pages):
            if i >= max_pages:
                break
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - one broken page must not lose the rest
                pages.append("")
        title = ""
        try:
            title = (reader.metadata.title or "") if reader.metadata else ""
        except Exception:  # noqa: BLE001
            pass
        return {"pages": pages, "total_pages": total, "title": str(title)[:200]}
    except PdfReadError as e:
        return {"error": "corrupt", "message": f"The PDF could not be read ({str(e)[:120]})."}
    except Exception as e:  # noqa: BLE001
        return {"error": "corrupt", "message": f"The PDF could not be read ({type(e).__name__})."}


if __name__ == "__main__":
    sys.stdout.write(json.dumps(main(sys.argv[1], int(sys.argv[2]))))
