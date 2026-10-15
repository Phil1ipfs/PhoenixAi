"""Local text extraction for uploaded documents. Nothing here touches the network or an AI model.

Safety rules
- The client-supplied file name is DISPLAY METADATA ONLY: it is reduced to a bare name and is never used to build a path.
  Uploaded bytes are never written under that name (PDFs go to a random temp file for the isolated worker, then are deleted).
- File type is decided from the extension AND verified from the content (magic bytes / zip structure / decodable text).
- Credential-like files (by name) and files containing private keys or well-known token formats are refused.
- Zip bombs (DOCX), oversized files and hostile PDFs are bounded by size/ratio/page limits and a child-process timeout.
- A scanned PDF is reported as `needs_ocr`; PHOENIX never pretends text was extracted.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from ..workspace import is_sensitive
from . import limits

WORKER = Path(__file__).with_name("pdf_worker.py")

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".go",
            ".rs", ".rb", ".php", ".swift", ".kt", ".kts", ".sql", ".sh", ".ps1", ".bat", ".cmd", ".html", ".htm", ".css",
            ".scss", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".xml", ".lua", ".luau", ".dart", ".r", ".gd", ".vue", ".svelte"}
KINDS = {".pdf": "pdf", ".docx": "docx", ".txt": "text", ".log": "text", ".rst": "text", ".md": "markdown", ".markdown": "markdown",
         ".csv": "csv", ".tsv": "csv", ".json": "json", **{e: "code" for e in CODE_EXT}}
SUPPORTED = sorted(KINDS)
EXTRA_SENSITIVE_NAMES = {".git-credentials", "wallet.dat", "known_hosts", "authorized_keys", "shadow", "passwd"}
SENSITIVE_STEMS = {"secret", "secrets", "credential", "credentials", "password", "passwords", "apikey", "api_key", "api-keys", "apikeys"}
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY(?: BLOCK)?-----"), "a private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "a GitHub token"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "an Anthropic API key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"), "an API secret key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "a Google API key"),
]


class UploadError(Exception):
    """A refusal that is safe to show to the user. `code` is stable for the UI/tests."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Para:
    text: str
    page: int | None = None
    section: str | None = None
    line: int | None = None        # source line (text/code/json), paragraph number (docx) or row number (csv)
    line_end: int | None = None


@dataclass
class Extracted:
    kind: str
    paras: list[Para]
    warnings: list[str] = field(default_factory=list)
    status: str = "ready"           # ready | needs_ocr
    detail: str = ""
    meta: dict = field(default_factory=dict)
    prefix: str = ""                # text repeated at the top of every chunk (CSV header)


# ------------------------------------------------------------------ names and types
def safe_filename(raw: str | None) -> str:
    """Bare, printable display name. Directory parts, control characters and reserved characters are removed."""
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if not name:
        raise UploadError(400, "bad_name", "The file needs a name (for example report.pdf).")
    if len(name) > limits.MAX_FILENAME:
        stem, ext = os.path.splitext(name)
        name = stem[: limits.MAX_FILENAME - len(ext)] + ext
    return name


def classify(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in KINDS:
        return KINDS[ext]
    if ext in {".doc", ".rtf", ".odt", ".pages"}:
        raise UploadError(415, "unsupported_type", f"'{ext}' files are not supported. Save the document as .docx, .pdf or .txt and upload that.")
    if ext in {".xls", ".xlsx", ".ppt", ".pptx"}:
        raise UploadError(415, "unsupported_type", f"'{ext}' files are not supported yet. Export a sheet as .csv, or the document as .pdf, and upload that.")
    raise UploadError(415, "unsupported_type", f"Unsupported file type '{ext or '(none)'}'. Supported: " + ", ".join(SUPPORTED))


def check_name_sensitive(name: str) -> None:
    low = name.lower()
    stem = os.path.splitext(low)[0]
    if is_sensitive(name) or low in EXTRA_SENSITIVE_NAMES or stem in SENSITIVE_STEMS or low.endswith((".ovpn", ".secret")):
        raise UploadError(403, "sensitive_file", "This looks like a credentials/secrets file, so PHOENIX will not read or store it.")


def scan_secrets(text: str) -> None:
    for pat, what in SECRET_PATTERNS:
        if pat.search(text):
            raise UploadError(403, "contains_secret", f"The file appears to contain {what}. PHOENIX refuses to store credentials; "
                                                      "remove it from the file and upload again.")


def max_bytes(kind: str) -> int:
    return limits.MAX_BYTES.get(kind, limits.MAX_BYTES["default"])


def decode_text(data: bytes) -> tuple[str, list[str]]:
    if b"\x00" in data[:8192] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UploadError(415, "binary_file", "This file looks binary, not text.")
    warnings: list[str] = []
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(enc, errors="replace"), warnings
    try:
        return data.decode("utf-8"), warnings
    except UnicodeDecodeError:
        warnings.append("The file is not valid UTF-8; it was read as Windows-1252, so some characters may be wrong.")
        return data.decode("cp1252", errors="replace"), warnings


def _clip(paras: list[Para], warnings: list[str]) -> list[Para]:
    total, out = 0, []
    for p in paras:
        total += len(p.text)
        if total > limits.MAX_TEXT_CHARS:
            warnings.append(f"Only the first {limits.MAX_TEXT_CHARS // 1000:,}K characters were kept; the rest of the file is not searchable.")
            break
        out.append(p)
    return out


# ------------------------------------------------------------------ PDF
def extract_pdf(data: bytes) -> Extracted:
    if not data.startswith(b"%PDF-") and b"%PDF-" not in data[:1024]:
        raise UploadError(415, "not_a_pdf", "This file is named .pdf but its content is not a PDF.")
    with tempfile.TemporaryDirectory(prefix="phoenix-pdf-") as d:
        tmp = Path(d) / "input.pdf"           # fixed name inside a fresh private directory: the client name is never used
        tmp.write_bytes(data)
        try:
            r = subprocess.run([sys.executable, "-I", str(WORKER), str(tmp), str(limits.MAX_PDF_PAGES)],
                               capture_output=True, text=True, timeout=limits.PDF_TIMEOUT_S, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise UploadError(422, "pdf_timeout", f"Reading this PDF took longer than {limits.PDF_TIMEOUT_S} s and was stopped.")
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise UploadError(422, "corrupt_pdf", "The PDF could not be read (the parser crashed). It may be damaged.")
    if "error" in out:
        raise UploadError(422 if out["error"] != "pypdf_missing" else 500, out["error"], out["message"])
    pages = [re.sub(r"[ \t]+", " ", p.replace("\x00", "")).strip() for p in out["pages"]]
    empty = [i + 1 for i, p in enumerate(pages) if len(re.sub(r"\s", "", p)) < limits.MIN_PAGE_CHARS]
    n = len(pages)
    warnings: list[str] = []
    meta = {"pages": out["total_pages"], "pages_read": n, "title": out.get("title", ""), "pages_without_text": empty[:200]}
    if out["total_pages"] > n:
        warnings.append(f"Only the first {n} of {out['total_pages']} pages were read (limit {limits.MAX_PDF_PAGES}).")
    if n == 0:
        raise UploadError(422, "empty_pdf", "The PDF has no pages.")
    if len(empty) / n >= limits.OCR_FRACTION:
        return Extracted("pdf", [], warnings, "needs_ocr",
                         f"This PDF looks scanned: {len(empty)} of {n} pages have no selectable text. PHOENIX cannot read it without "
                         "OCR (text recognition), which is not installed, so nothing was extracted or indexed. Use an OCR tool "
                         "to make a searchable PDF (or export the text) and upload that instead.", meta)
    if empty:
        shown = ", ".join(map(str, empty[:10])) + ("…" if len(empty) > 10 else "")
        warnings.append(f"{len(empty)} page(s) have no extractable text (probably scans or images) and are NOT searchable: {shown}.")
    paras: list[Para] = []
    for i, text in enumerate(pages, 1):
        for block in re.split(r"\n\s*\n", text):
            block = block.strip()
            if block:
                paras.append(Para(block, page=i))
    return Extracted("pdf", _clip(paras, warnings), warnings, meta=meta)


# ------------------------------------------------------------------ DOCX
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx(data: bytes) -> Extracted:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadError(415, "not_a_docx", "This file is named .docx but is not a Word document (old .doc files are not supported).")
    infos = z.infolist()
    if len(infos) > 5000 or sum(i.file_size for i in infos) > limits.DOCX_MAX_UNCOMPRESSED:
        raise UploadError(413, "too_large", "This Word document expands to an unreasonable size and was refused.")
    comp = sum(i.compress_size for i in infos) or 1
    if sum(i.file_size for i in infos) / comp > limits.DOCX_MAX_RATIO:
        raise UploadError(413, "zip_bomb", "This Word document has a suspicious compression ratio and was refused.")
    names = set(z.namelist())
    if "word/document.xml" not in names:
        raise UploadError(415, "not_a_docx", "This is a zip file but not a Word document (no word/document.xml).")

    def load(name: str) -> ET.Element | None:
        if name not in names:
            return None
        raw = z.read(name)
        if b"<!DOCTYPE" in raw[:2048] or b"<!ENTITY" in raw[:20000]:
            raise UploadError(415, "unsafe_xml", "The document contains XML entity declarations and was refused.")
        try:
            return ET.fromstring(raw)
        except ET.ParseError:
            raise UploadError(422, "corrupt_docx", "The Word document's XML is damaged.")

    style_names: dict[str, str] = {}
    styles = load("word/styles.xml")
    if styles is not None:
        for s in styles.iter(f"{W}style"):
            n = s.find(f"{W}name")
            if n is not None:
                style_names[s.get(f"{W}styleId", "")] = (n.get(f"{W}val") or "").lower()
    root = load("word/document.xml")
    body = root.find(f"{W}body") if root is not None else None
    if body is None:
        raise UploadError(422, "corrupt_docx", "The Word document has no body.")

    def text_of(el: ET.Element) -> str:
        out = []
        for n in el.iter():
            if n.tag == f"{W}t" and n.text:
                out.append(n.text)
            elif n.tag == f"{W}tab":
                out.append("\t")
            elif n.tag in (f"{W}br", f"{W}cr"):
                out.append("\n")
        return "".join(out).strip()

    paras: list[Para] = []
    stack: list[tuple[int, str]] = []
    num = 0
    for el in body:
        if el.tag == f"{W}p":
            t = text_of(el)
            if not t:
                continue
            num += 1
            style = el.find(f"{W}pPr/{W}pStyle")
            sid = style.get(f"{W}val", "") if style is not None else ""
            sname = style_names.get(sid, sid.lower())
            m = re.match(r"^(?:heading|titre|überschrift)\s*(\d)$", sname) or re.match(r"^heading(\d)$", sid.lower())
            if m or sname == "title":
                level = int(m.group(1)) if m else 0
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, t[:100]))
            paras.append(Para(t, section=" > ".join(s for _, s in stack) or None, line=num, line_end=num))
        elif el.tag == f"{W}tbl":
            for tr in el.iter(f"{W}tr"):
                cells = [text_of(tc) for tc in tr.findall(f"{W}tc")]
                row = " | ".join(c for c in cells if c)
                if row:
                    num += 1
                    paras.append(Para(row, section=" > ".join(s for _, s in stack) or None, line=num, line_end=num))
    if not paras:
        raise UploadError(422, "no_text", "This Word document contains no text (it may contain only images).")
    warnings: list[str] = []
    if any(n.startswith("word/media/") for n in names):
        warnings.append("The document contains images; text inside images is not read.")
    return Extracted("docx", _clip(paras, warnings), warnings, meta={"paragraphs": num})


# ------------------------------------------------------------------ plain text, markdown, code
def _line_paras(text: str, section_for) -> list[Para]:
    """Blank-line separated blocks with their source line numbers."""
    paras: list[Para] = []
    cur: list[str] = []
    start = 0
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            if not cur:
                start = i
            cur.append(line.rstrip())
        elif cur:
            paras.append(Para("\n".join(cur), section=section_for(start), line=start, line_end=i - 1))
            cur = []
    if cur:
        paras.append(Para("\n".join(cur), section=section_for(start), line=start, line_end=start + len(cur) - 1))
    return paras


def extract_text(text: str, kind: str, warnings: list[str]) -> Extracted:
    sections: dict[int, str] = {}
    if kind == "markdown":
        stack: list[tuple[int, str]] = []
        in_fence = False
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
            m = None if in_fence else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
            if m:
                lvl = len(m.group(1))
                while stack and stack[-1][0] >= lvl:
                    stack.pop()
                stack.append((lvl, m.group(2)[:100]))
            sections[i] = " > ".join(s for _, s in stack)
        sec = lambda ln: sections.get(ln) or None  # noqa: E731
    else:
        sec = lambda ln: None  # noqa: E731
    paras = _line_paras(text, sec)
    if not paras:
        raise UploadError(422, "no_text", "The file is empty.")
    return Extracted(kind, _clip(paras, warnings), warnings, meta={"lines": text.count("\n") + 1})


DEF_LINE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:public\s+|private\s+|protected\s+|static\s+)*"
                      r"(?:function\*?|class|def|fn|func|interface|struct|enum|impl|trait|namespace|object|module)\s+[\w$.<>:]+")


def extract_code(text: str, name: str, warnings: list[str]) -> Extracted:
    import ast
    lines = text.splitlines()
    owner: dict[int, str] = {}
    if name.lower().endswith(".py"):
        try:
            tree = ast.parse(text)

            def mark(node, prefix=""):
                for n in node.body:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        label = prefix + n.name
                        for ln in range(min([n.lineno] + [d.lineno for d in n.decorator_list]), n.end_lineno + 1):
                            owner[ln] = label
                        if isinstance(n, ast.ClassDef):
                            mark(n, label + ".")
            mark(tree)
        except SyntaxError:
            warnings.append("The Python file has a syntax error; sections are approximate.")
    if not owner:
        last = None
        for i, line in enumerate(lines, 1):
            m = DEF_LINE.match(line)
            if m:
                last = m.group(0).strip()[:80]
            if last:
                owner[i] = last
    paras = _line_paras(text, lambda ln: owner.get(ln))
    if not paras:
        raise UploadError(422, "no_text", "The file is empty.")
    return Extracted("code", _clip(paras, warnings), warnings, meta={"lines": len(lines)})


# ------------------------------------------------------------------ CSV
def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "")) if re.fullmatch(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?|[-+]?\.\d+", s.strip()) else None
    except ValueError:
        return None


def extract_csv(text: str, name: str, warnings: list[str]) -> Extracted:
    sample = text[:4096]
    delim = "\t" if name.lower().endswith(".tsv") else None
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    csv.field_size_limit(1_000_000)
    try:
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim)]
    except csv.Error as e:
        raise UploadError(422, "bad_csv", f"The CSV could not be parsed ({e}).")
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 1:
        raise UploadError(422, "no_text", "The CSV is empty.")
    header, data = [h.strip() or f"col{i + 1}" for i, h in enumerate(rows[0])], rows[1:]
    if len(data) > 100_000:
        data = data[:100_000]
        warnings.append("Only the first 100,000 rows were read.")
    profile = [f"File profile: {len(data):,} data rows, {len(header)} columns."]
    for ci, h in enumerate(header[:40]):
        vals = [r[ci].strip() for r in data if ci < len(r) and r[ci].strip()]
        nums = [n for n in (_num(v) for v in vals) if n is not None]
        if vals and len(nums) >= 0.9 * len(vals):
            profile.append(f"- {h} (numeric): {len(vals):,} filled, min {min(nums):g}, max {max(nums):g}, mean {sum(nums) / len(nums):.4g}")
        else:
            distinct: dict[str, int] = {}
            for v in vals:
                distinct[v] = distinct.get(v, 0) + 1
            top = ", ".join(f"{k[:30]} ({c})" for k, c in sorted(distinct.items(), key=lambda t: (-t[1], t[0]))[:5])
            profile.append(f"- {h} (text): {len(vals):,} filled, {len(distinct):,} distinct" + (f"; most common: {top}" if top else ""))
    paras = [Para("\n".join(profile), section="Profile", line=1, line_end=1)]
    for i, r in enumerate(data, 2):                     # row 1 is the header, so the first data row is row 2 (as in a spreadsheet)
        paras.append(Para(" | ".join(c.strip() for c in r), section="Rows", line=i, line_end=i))
    return Extracted("csv", _clip(paras, warnings), warnings, meta={"rows": len(data), "columns": header[:60]},
                     prefix="Columns: " + " | ".join(header[:40]))


# ------------------------------------------------------------------ JSON
def _flatten(obj, path: str, out: list[tuple[str, str]], limit: int, depth: int = 0) -> None:
    if len(out) >= limit:
        return
    if isinstance(obj, dict) and depth < 40:
        if not obj:
            out.append((path, "{}"))
        for k, v in obj.items():
            _flatten(v, f"{path}.{k}" if re.fullmatch(r"\w+", str(k)) else f'{path}["{k}"]', out, limit, depth + 1)
    elif isinstance(obj, list) and depth < 40:
        if not obj:
            out.append((path, "[]"))
        for i, v in enumerate(obj):
            _flatten(v, f"{path}[{i}]", out, limit, depth + 1)
    else:
        out.append((path, json.dumps(obj, ensure_ascii=False)[:300]))


def _describe(obj) -> str:
    if isinstance(obj, dict):
        return f"object with {len(obj)} keys: " + ", ".join(list(map(str, obj))[:25])
    if isinstance(obj, list):
        keys: dict[str, int] = {}
        for it in obj[:200]:
            if isinstance(it, dict):
                for k in it:
                    keys[str(k)] = keys.get(str(k), 0) + 1
        return f"array of {len(obj):,} items" + (f"; item keys: {', '.join(list(keys)[:25])}" if keys else "")
    return type(obj).__name__


def extract_json(text: str, warnings: list[str]) -> Extracted:
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise UploadError(422, "invalid_json", f"This is not valid JSON ({str(e)[:120]}).")
    out: list[tuple[str, str]] = []
    _flatten(obj, "$", out, 200_000)
    if len(out) >= 200_000:
        warnings.append("Only the first 200,000 values were read.")
    top = _describe(obj)
    paras = [Para(f"JSON structure: {top}.", section="Structure", line=1, line_end=1)]
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:40]:
            paras.append(Para(f"Top-level key `{k}`: {_describe(v)}", section="Structure", line=1, line_end=1))
    for i, (p, v) in enumerate(out, 1):
        parts = re.match(r"^(\$(?:\.\w+|\[\d+\]|\[\"[^\"]*\"\]){0,2})", p)
        paras.append(Para(f"{p} = {v}", section=parts.group(1) if parts else "$", line=i, line_end=i))
    return Extracted("json", _clip(paras, warnings), warnings, meta={"values": len(out), "structure": top})


# ------------------------------------------------------------------ entry point
def extract(name: str, data: bytes) -> Extracted:
    """Validate and extract. `name` must already be passed through safe_filename."""
    kind = classify(name)
    check_name_sensitive(name)
    if not data:
        raise UploadError(400, "empty_file", "The file is empty.")
    if len(data) > max_bytes(kind):
        raise UploadError(413, "too_large", f"The file is {len(data) / 2**20:.1f} MB; the limit for this type is {max_bytes(kind) // 2**20} MB.")
    if kind == "pdf":
        ex = extract_pdf(data)
    elif kind == "docx":
        ex = extract_docx(data)
    else:
        text, warnings = decode_text(data)
        ex = (extract_csv(text, name, warnings) if kind == "csv" else extract_json(text, warnings) if kind == "json"
              else extract_code(text, name, warnings) if kind == "code" else extract_text(text, kind, warnings))
    for p in ex.paras:
        scan_secrets(p.text)
    return ex
