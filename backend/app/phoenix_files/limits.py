"""Hard limits chosen for a 12 GB laptop running a 3B model: parsing must stay cheap and the model never sees a whole document."""
MAX_BYTES = {"pdf": 15 * 2**20, "docx": 10 * 2**20, "default": 5 * 2**20}
MAX_PDF_PAGES = 300
MAX_TEXT_CHARS = 1_500_000        # extracted text kept per file; the rest is dropped and the file is flagged truncated
MAX_CHUNKS = 3000
MAX_FILES = 200                   # files in the library
MAX_ATTACHED = 8                  # files attached to one conversation
MAX_FILENAME = 120
CHUNK_TARGET = 800                # characters per chunk (about 200 tokens)
CHUNK_OVERLAP = 120
PDF_TIMEOUT_S = 90
MIN_PAGE_CHARS = 25               # fewer non-space characters than this => the page has no usable text layer
OCR_FRACTION = 0.8                # if at least this share of pages has no text layer the PDF is treated as scanned
DOCX_MAX_UNCOMPRESSED = 60 * 2**20
DOCX_MAX_RATIO = 100              # zip-bomb guard: uncompressed / compressed
