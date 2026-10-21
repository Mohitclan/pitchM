"""
Pitchlight — "my documents": the facts a person uploads once (pricing, FAQ, product sheets, case studies)
so live answers use THEIR facts instead of invented ones.

Plain keyword search (no embeddings, no extra service): documents are split into short passages, and a
question is matched to the passages that share its meaningful words, weighted by how rare each word is.
"""
from __future__ import annotations

import math
import re
from collections import Counter

MAX_DOC_CHARS = 60_000         # one document
MAX_TOTAL_CHARS = 300_000      # everything one person has uploaded
MAX_DOCS = 25
CHUNK_CHARS = 650
CHUNK_OVERLAP = 90

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
STOPWORDS = frozenset("""
a an the and or but if then so to of in on at for with from by as is are was were be been being it its this that
these those i we you they he she our your their my me us them will would can could should do does did have has had
not no yes just very really about into than also more most some any all each how what when where which who whom why
there here up out over under again further once only own same too s t d ll m o re ve y
much many please tell definitely anyone something
""".split())


def normalize_token(tok: str) -> str:
    """Rough stem so 'prices / priced / pricing' and 'integrate / integrations' meet."""
    for suffix in ("ations", "ation", "ions", "ion", "ing", "ed", "es", "s"):
        if len(tok) > len(suffix) + 3 and tok.endswith(suffix):
            tok = tok[: -len(suffix)]
            if len(tok) > 3 and tok[-1] == tok[-2] and suffix in ("ing", "ed"):
                tok = tok[:-1]                                 # running -> run, stopped -> stop
            break
    if len(tok) > 4 and tok.endswith("e"):
        tok = tok[:-1]
    return tok[:6]


def content_tokens(text: str) -> list[str]:
    return [normalize_token(t) for t in _WORD_RE.findall((text or "").lower()) if t not in STOPWORDS and len(t) > 1]


def clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split into passages at paragraph/sentence boundaries, ~size characters, with a little overlap."""
    text = clean_text(text)
    if not text:
        return []
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            pieces.append(para)
        else:
            pieces.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", para) if s.strip())
    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 1 > size:
            chunks.append(cur)
            cur = (cur[-overlap:] + " " + piece) if overlap and len(cur) > overlap else piece
        else:
            cur = f"{cur}\n{piece}".strip() if cur else piece
    if cur:
        chunks.append(cur)
    return [c[: size * 2] for c in chunks]


def _office_text(data: bytes, kind: str) -> str:
    """Text from a .pptx (one block per slide, in order) or .docx, read straight from the zip's XML."""
    import html
    import io
    import zipfile
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("That file could not be read.") from exc
    with zf:
        if sum(i.file_size for i in zf.infolist()) > 80_000_000:
            raise ValueError("That file is too large to read.")
        if kind == "pptx":
            slides = sorted((n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                            key=lambda n: int(re.search(r"(\d+)\.xml", n).group(1)))
            parts = []
            for i, n in enumerate(slides, 1):
                xml = zf.read(n).decode("utf-8", "ignore")
                paras = [" ".join(html.unescape(t) for t in re.findall(r"<a:t>([^<]*)</a:t>", para)).strip()
                         for para in re.findall(r"<a:p>.*?</a:p>|<a:p [^>]*>.*?</a:p>", xml, flags=re.S)]
                body = "\n".join(p for p in paras if p)
                if body:
                    parts.append(f"Slide {i}\n{body}")
            text = "\n\n".join(parts)
        else:
            if "word/document.xml" not in zf.namelist():
                raise ValueError("That file could not be read.")
            xml = zf.read("word/document.xml").decode("utf-8", "ignore")
            paras = [" ".join(html.unescape(t) for t in re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para)).strip()
                     for para in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)]
            text = "\n".join(p for p in paras if p)
    if len(text.strip()) < 20:
        raise ValueError("No text found in that file (is it all images?). Paste the text instead.")
    return clean_text(text)


def extract_text(filename: str, data: bytes) -> str:
    """Text out of an uploaded file: .pdf / .pptx / .docx / .txt / .md / .csv. Raises ValueError for anything else."""
    name = (filename or "").lower()
    if name.endswith(".pptx"):
        return _office_text(data, "pptx")
    if name.endswith(".docx"):
        return _office_text(data, "docx")
    if name.endswith(".pdf"):
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:                                  # pragma: no cover
            raise ValueError("PDF reading is not available on this server.") from exc
        try:
            with fitz.open(stream=data, filetype="pdf") as doc:
                text = "\n\n".join(page.get_text() for page in doc)
        except Exception as exc:
            raise ValueError("That PDF could not be read.") from exc
        if len(text.strip()) < 20:
            raise ValueError("No text found in that PDF (is it a scanned image?). Paste the text instead.")
        return clean_text(text)
    if name.endswith((".txt", ".md", ".markdown", ".csv", ".text")):
        for enc in ("utf-8", "utf-16", "latin-1"):
            try:
                return clean_text(data.decode(enc))
            except (UnicodeDecodeError, UnicodeError):
                continue
    raise ValueError("Use a .pdf, .pptx, .docx, .txt or .md file, or paste the text.")


class Library:
    """In-memory search over one person's documents (built when a session starts)."""

    def __init__(self, docs: list[dict]):
        self.passages: list[dict] = []
        for doc in docs:
            for chunk in chunk_text(doc.get("text", "")):
                self.passages.append({"doc_id": doc.get("doc_id"), "title": doc.get("title") or "Document", "text": chunk,
                                      "tokens": Counter(content_tokens(chunk))})
        df: Counter = Counter()
        for p in self.passages:
            df.update(set(p["tokens"]))
        n = max(1, len(self.passages))
        self._df = dict(df)
        self._idf = {t: math.log(1 + n / (1 + c)) + 0.3 for t, c in df.items()}

    def __bool__(self) -> bool:
        return bool(self.passages)

    MIN_COVERAGE = 0.3            # share of the question's (weighted) words a passage must contain

    def search(self, query: str, k: int = 3) -> list[dict]:
        """
        Best passages for a question. A passage must contain enough of the question's meaningful words
        (`cov`, 0-1; words found in no document count as missing), words found in most passages are ignored,
        and with two or more question words at least two must match.
        """
        q = set(content_tokens(query))
        n = len(self.passages)
        if not q or not n:
            return []
        missing_w = max(self._idf.values(), default=1.0)
        total_w = sum(self._idf.get(t, missing_w) for t in q)
        scored = []
        for p in self.passages:
            matched = [t for t in q if t in p["tokens"]]
            hits = [t for t in matched if n < 4 or self._df.get(t, 0) / n <= 0.5]      # words found in most passages say little
            if not hits or (len(q) >= 3 and len(matched) < 2):
                continue
            cov = sum(self._idf.get(t, 0.0) for t in matched) / total_w
            if cov < self.MIN_COVERAGE:
                continue
            score = sum(self._idf.get(t, 0.0) for t in hits) / math.sqrt(1 + len(p["tokens"]) / 40)
            scored.append((score, cov, p))
        if not scored:
            return []
        scored.sort(key=lambda sp: sp[0], reverse=True)
        top = scored[0][0]
        out = []
        for score, cov, p in scored[:k]:
            if score >= 0.45 * top and score >= 0.25:
                out.append({"title": p["title"], "text": p["text"], "score": round(score, 2), "cov": round(cov, 2), "doc_id": p["doc_id"]})
        return out


def facts_block(hits: list[dict], *, max_chars: int = 1800) -> str:
    """Prompt text for the passages that matched, with their document names."""
    if not hits:
        return ""
    lines, used = [], 0
    for h in hits:
        piece = f"[{h['title']}] {h['text']}"
        if used + len(piece) > max_chars:
            break
        lines.append(piece)
        used += len(piece)
    return "FROM THE PRESENTER'S OWN DOCUMENTS (the only source for numbers, prices, features, customer names):\n" + "\n".join(lines)


def source_titles(hits: list[dict]) -> list[str]:
    seen: list[str] = []
    for h in hits:
        if h["title"] not in seen:
            seen.append(h["title"])
    return seen[:3]
