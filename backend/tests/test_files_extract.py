"""PHOENIX Files: extraction, chunking, retrieval and safety rules (pure logic; no server, no model, no network)."""
import io
import zipfile

import pytest

from app.phoenix_files import chunk, extract as X, limits
from app.phoenix_files.retrieve import Index, explicit_refs, pack_budget, query_terms, summary_sample, tokens
from docs_fixtures import make_docx, make_encrypted_pdf, make_pdf, make_scanned_pdf


def ex(name, data):
    return X.extract(X.safe_filename(name), data if isinstance(data, bytes) else data.encode())


def chunks_of(name, data):
    return chunk.pack(ex(name, data))[0]


# ---------------------------------------------------------------- names, types, secrets
@pytest.mark.parametrize("raw,expected", [("../../etc/passwd.txt", "passwd.txt"), ("C:\\Users\\x\\notes.md", "notes.md"),
                                          ("a/b/c.csv", "c.csv"), ("rep\x00ort\n.txt", "report.txt"), ("  .. report.txt ", "report.txt"),
                                          ("what?.txt", "what.txt")])
def test_safe_filename_strips_paths_and_controls(raw, expected):
    assert X.safe_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "..", "/", "\x00"])
def test_safe_filename_rejects_empty(raw):
    with pytest.raises(X.UploadError):
        X.safe_filename(raw)


def test_long_names_are_truncated_keeping_extension():
    n = X.safe_filename("a" * 400 + ".pdf")
    assert len(n) <= limits.MAX_FILENAME and n.endswith(".pdf")


@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "server.pem", "my.key", "passwords.csv", "credentials.json",
                                  "secrets.txt", "app.db", "store.sqlite", "vpn.ovpn", ".npmrc", "wallet.dat"])
def test_credential_files_refused_by_name(name):
    with pytest.raises(X.UploadError) as e:
        ex(name, "x=1")
    assert e.value.code in ("sensitive_file", "unsupported_type") and e.value.status in (403, 415)


@pytest.mark.parametrize("name", [".env", "id_rsa", "server.pem", "app.db", "credentials.json", "passwords.csv", "secrets.txt"])
def test_credential_files_are_403_even_when_extension_is_supported(name):
    with pytest.raises(X.UploadError) as e:
        X.check_name_sensitive(name)
    assert e.value.code == "sensitive_file" and e.value.status == 403


@pytest.mark.parametrize("body", ["-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "key = AKIAABCDEFGHIJKLMNOP",
                                  "token ghp_" + "a" * 36, "sk-ant-" + "x" * 30])
def test_secret_content_refused(body):
    with pytest.raises(X.UploadError) as e:
        ex("notes.txt", "some notes\n" + body)
    assert e.value.code == "contains_secret"


@pytest.mark.parametrize("name,hint", [("a.exe", "Supported"), ("a.doc", ".docx"), ("a.xlsx", ".csv"), ("noext", "Supported")])
def test_unsupported_types_explain_what_to_do(name, hint):
    with pytest.raises(X.UploadError) as e:
        ex(name, "x")
    assert e.value.code == "unsupported_type" and hint in e.value.message


def test_binary_content_in_text_file_refused():
    with pytest.raises(X.UploadError) as e:
        ex("fake.txt", b"abc\x00\x01\x02binary")
    assert e.value.code == "binary_file"


def test_size_limit_per_type(monkeypatch):
    monkeypatch.setitem(limits.MAX_BYTES, "default", 100)
    with pytest.raises(X.UploadError) as e:
        ex("big.txt", "x" * 200)
    assert e.value.code == "too_large" and e.value.status == 413


# ---------------------------------------------------------------- plain text / markdown / code
def test_markdown_sections_and_line_refs():
    md = "# Guide\n\nIntro text about the guide.\n\n## Install\n\nRun the installer carefully.\n\n## Usage\n\nStart the app."
    cs = chunks_of("guide.md", md)
    c = next(c for c in cs if "installer" in c.text)
    assert c.section and "Install" in c.section and "lines" in c.loc and c.line_start is not None


def test_text_file_lines_and_utf8_bom_and_cp1252():
    e = ex("a.txt", b"\xef\xbb\xbfHello world\n\nSecond paragraph here")
    assert e.paras[0].text == "Hello world" and e.paras[1].line == 3
    e2 = ex("b.txt", "caf\xe9 au lait".encode("cp1252"))
    assert "caf\xe9" in e2.paras[0].text and any("Windows-1252" in w for w in e2.warnings)


def test_python_code_sections_named_by_symbol():
    src = "import os\n\n\ndef alpha(x):\n    return x + 1\n\n\nclass Box:\n    def open(self):\n        return True\n"
    cs = chunks_of("m.py", src)
    assert any(c.section == "alpha" for c in cs) and any(c.section and c.section.startswith("Box") for c in cs)
    assert all("lines" in c.loc for c in cs)


def test_javascript_sections_by_regex():
    js = "const a = 1;\n\nfunction render(props) {\n  return props.x;\n}\n"
    cs = chunks_of("a.js", js)
    assert any(c.section and "function render" in c.section for c in cs)


# ---------------------------------------------------------------- CSV / JSON
CSV = "name,qty,region\nbolt,10,north\nnut,25.5,south\nscrew,7,north\n"


def test_csv_profile_header_and_row_refs():
    e = ex("stock.csv", CSV)
    assert e.meta["rows"] == 3 and e.meta["columns"] == ["name", "qty", "region"]
    cs = chunk.pack(e)[0]
    assert "File profile: 3 data rows, 3 columns" in cs[0].text and "qty (numeric)" in cs[0].text and "max 25.5" in cs[0].text
    assert any("Columns: name | qty | region" in c.text and "bolt | 10 | north" in c.text for c in cs)
    assert any("rows 2" in c.loc for c in cs)          # header is row 1, like a spreadsheet


def test_csv_semicolon_and_tsv():
    assert ex("a.csv", "a;b\n1;2\n3;4\n").meta["columns"] == ["a", "b"]
    assert ex("a.tsv", "a\tb\n1\t2\n").meta["columns"] == ["a", "b"]


def test_json_flatten_paths_and_structure():
    e = ex("d.json", '{"users": [{"name": "Ana", "age": 31}, {"name": "Bo", "age": 22}], "ok": true}')
    texts = [p.text for p in e.paras]
    assert '$.users[0].name = "Ana"' in texts and "$.ok = true" in texts
    assert any("array of 2 items" in t for t in texts)


def test_invalid_json_reports_error():
    with pytest.raises(X.UploadError) as e:
        ex("d.json", "{not json")
    assert e.value.code == "invalid_json"


# ---------------------------------------------------------------- DOCX
def test_docx_headings_tables_sections():
    data = make_docx([("h1", "Annual Report"), ("p", "Revenue grew strongly this year."), ("h2", "Costs"),
                      ("p", "Costs were flat."), ("table", [["Item", "Amount"], ["Rent", "1200"]])])
    e = ex("r.docx", data)
    assert "Annual Report > Costs" in {p.section for p in e.paras}
    cs = chunk.pack(e)[0]
    assert any("Rent | 1200" in c.text for c in cs)
    assert any("Annual Report > Costs" in c.loc and "\u00b6" in c.loc for c in cs)


def test_docx_zip_bomb_and_wrong_types_refused():
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document xmlns:w='x'><w:body/></w:document>" + " " * 5_000_000)
    with pytest.raises(X.UploadError) as e:
        ex("b.docx", bomb.getvalue())
    assert e.value.code == "zip_bomb"
    with pytest.raises(X.UploadError) as e:
        ex("n.docx", b"not a zip at all")
    assert e.value.code == "not_a_docx"
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("hello.txt", "x")
    with pytest.raises(X.UploadError) as e:
        ex("z.docx", plain.getvalue())
    assert e.value.code == "not_a_docx"


def test_docx_entity_declarations_refused():
    z = zipfile.ZipFile(io.BytesIO(make_docx([("p", "hi")])))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as w:
        for n in z.namelist():
            data = z.read(n)
            if n == "word/document.xml":
                data = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>' + data.split(b"?>", 1)[1]
            w.writestr(n, data)
    with pytest.raises(X.UploadError) as e:
        ex("e.docx", out.getvalue())
    assert e.value.code == "unsafe_xml"


def test_docx_without_text_says_so():
    with pytest.raises(X.UploadError) as e:
        ex("empty.docx", make_docx([]))
    assert e.value.code == "no_text"


# ---------------------------------------------------------------- PDF
def test_pdf_pages_text_and_page_refs():
    pdf = make_pdf([f"Page {i} introduction.\nThe topic of page {i} is {'alpha' if i == 2 else 'beta'} testing and more words follow here."
                    for i in range(1, 4)])
    e = ex("doc.pdf", pdf)
    assert e.status == "ready" and e.meta["pages"] == 3
    cs = chunk.pack(e)[0]
    hit = Index([{**c.__dict__, "file_id": "f", "file_name": "doc.pdf"} for c in cs]).search("alpha testing", 1)[0]
    assert hit["page_start"] == 2 and hit["loc"].startswith("p. 2")


def test_scanned_pdf_is_flagged_needs_ocr_not_pretended():
    e = ex("scan.pdf", make_scanned_pdf(4))
    assert e.status == "needs_ocr" and e.paras == [] and "OCR" in e.detail and "4 of 4" in e.detail


def test_partially_scanned_pdf_warns_with_page_numbers():
    text = "A real text page with enough characters to be counted as extractable content for the test."
    e = ex("mix.pdf", make_pdf([text, text, text, ""]))
    assert e.status == "ready" and any("have no extractable text" in w and "4" in w for w in e.warnings)
    assert e.meta["pages_without_text"] == [4]


def test_encrypted_corrupt_and_fake_pdfs():
    with pytest.raises(X.UploadError) as e:
        ex("enc.pdf", make_encrypted_pdf())
    assert e.value.code == "encrypted"
    with pytest.raises(X.UploadError) as e:
        ex("fake.pdf", b"just some text pretending")
    assert e.value.code == "not_a_pdf"
    with pytest.raises(X.UploadError) as e:
        ex("bad.pdf", b"%PDF-1.4\n garbage garbage garbage")
    assert e.value.code in ("corrupt", "corrupt_pdf", "empty_pdf")


def test_pdf_page_limit_warns(monkeypatch):
    monkeypatch.setattr(limits, "MAX_PDF_PAGES", 2)
    e = ex("long.pdf", make_pdf([f"This is page {i} with plenty of readable text content in it." for i in range(5)]))
    assert e.meta["pages_read"] == 2 and any("first 2 of 5" in w for w in e.warnings)


# ---------------------------------------------------------------- chunking
LONG = "\n\n".join(f"Paragraph {i}. " + "The quick brown fox jumps over the lazy dog. " * 6 for i in range(30))


def test_chunks_are_bounded_overlapping_and_deterministic():
    e = ex("long.txt", LONG)
    a, _ = chunk.pack(e)
    b, _ = chunk.pack(e)
    assert [c.text for c in a] == [c.text for c in b] and len(a) > 5
    assert all(len(c.text) <= limits.CHUNK_TARGET * 1.6 for c in a)
    assert any(a[i].text.split("\n\n")[-1] in a[i + 1].text for i in range(len(a) - 1))   # overlap carried forward


def test_chunk_cap(monkeypatch):
    monkeypatch.setattr(limits, "MAX_CHUNKS", 3)
    cs, w = chunk.pack(ex("long.txt", LONG))
    assert len(cs) == 3 and w and "first 3" in w[0]


# ---------------------------------------------------------------- retrieval
def idx_for(text, name="n.txt"):
    return Index([{**c.__dict__, "file_id": "f1", "file_name": name} for c in chunks_of(name, text)])


def test_tokens_split_identifiers_and_stem():
    assert tokens("build_messages") == ["build", "message"]
    assert tokens("getUserName") == ["get", "user", "name"]
    assert "price" in query_terms("What are the prices?")


def test_bm25_ranks_relevant_chunk_first_and_is_deterministic():
    doc = "\n\n".join(["Cats are small domestic animals that purr.", "The invoice total is 4200 dollars due in March.",
                       "Mountains are formed by tectonic plate collisions.", "Invoices must be paid within thirty days."])
    cs = [{"file_id": "f", "file_name": "n.txt", "idx": i, "text": t, "section": None, "page_start": None, "page_end": None,
           "line_start": i, "line_end": i, "loc": f"lines {i}"} for i, t in enumerate(doc.split("\n\n"))]
    ix = Index(cs)
    r1, r2 = ix.search("when is the invoice due?", 3), ix.search("when is the invoice due?", 3)
    assert r1 == r2 and "invoice" in r1[0]["text"].lower()


def test_page_and_phrase_refs_boost():
    pdf = make_pdf([f"Section text number {i} about gardening and soil and plants and more words here." for i in range(1, 6)])
    cs = chunk.pack(ex("g.pdf", pdf))[0]
    ix = Index([{**c.__dict__, "file_id": "f", "file_name": "g.pdf"} for c in cs])
    assert ix.search("what is on page 4?", 1)[0]["page_start"] == 4
    assert explicit_refs('see "soil and plants" on pages 2-3')["pages"] == {2, 3}


def test_no_match_returns_nothing():
    assert idx_for("apples and oranges").search("quantum chromodynamics lagrangian") == []


def test_budget_and_summary_sampling():
    ix = idx_for(LONG)
    chosen = pack_budget(ix.search("quick brown fox", 12), 2000)
    assert sum(len(h["text"]) for h in chosen) <= 2000 + 1400 and [h["idx"] for h in chosen] == sorted(h["idx"] for h in chosen)
    s = summary_sample(ix.chunks, 2400)
    assert s[0]["idx"] == 0 and len(s) < len(ix.chunks) and sum(len(c["text"]) for c in s) <= 2400 + 900
