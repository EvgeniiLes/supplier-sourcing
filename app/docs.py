"""Чтение загруженных КП: любой привычный формат превращается в текст (а скан/картинка уходит в Claude как есть)."""
import base64
import io
import re
import zipfile
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from xml.etree import ElementTree

from bs4 import BeautifulSoup

MAX_BYTES = 8 * 1024 * 1024
MAX_UNPACKED = 60 * 1024 * 1024        # защита от zip-бомб в docx/xlsx
MAX_TEXT = 60_000
TEXT_EXT = {".txt", ".csv", ".md", ".tsv"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
ALLOWED = TEXT_EXT | IMAGE_TYPES.keys() | {".pdf", ".docx", ".xlsx", ".eml", ".html", ".htm"}


class DocError(ValueError):
    """Файл нельзя принять: понятное пользователю сообщение."""


@dataclass
class Document:
    text: str                 # извлечённый текст (может быть пустым для скана)
    media: dict | None        # {'type': 'document'|'image', 'media_type': ..., 'data': base64}: отдаём модели как есть
    kind: str                 # pdf / docx / xlsx / eml / text / image / html


def _ext(filename: str) -> str:
    m = re.search(r"\.[A-Za-z0-9]+$", filename or "")
    return m.group(0).lower() if m else ""


def _clean(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_TEXT]


def _check_zip(content: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise DocError("Файл повреждён или это не Office-документ")
    if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
        raise DocError("Документ слишком большой после распаковки")
    return z


def _docx_text(content: bytes) -> str:
    z = _check_zip(content)
    try:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    except (KeyError, ElementTree.ParseError):
        raise DocError("Не удалось прочитать Word-документ")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines = []
    for p in root.iter(f"{ns}p"):
        # ячейки таблицы разделяем табами, чтобы строки прайса не слипались
        parts = []
        for node in p.iter():
            if node.tag == f"{ns}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{ns}tab":
                parts.append("\t")
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _xlsx_text(content: bytes) -> str:
    _check_zip(content)
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise DocError("Не удалось прочитать Excel-файл")
    out = []
    for ws in wb.worksheets[:5]:
        out.append(f"[Лист: {ws.title}]")
        for n, row in enumerate(ws.iter_rows(values_only=True)):
            if n >= 400:
                break
            cells = ["" if v is None else str(v).strip() for v in row]
            if any(cells):
                out.append(" | ".join(cells).rstrip(" |"))
    return "\n".join(out)


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise DocError("PDF защищён паролем")
        return "\n".join((p.extract_text() or "") for p in reader.pages[:40])
    except DocError:
        raise
    except Exception:
        return ""       # скан или нестандартный PDF: модель прочитает сам файл


def pdf_text_layer(content: bytes) -> str:
    """Текст из PDF с текстовым слоем; для скана и защищённого файла пустая строка (без исключений)."""
    try:
        return _clean(_pdf_text(content))
    except DocError:
        return ""


def _html_text(content: bytes) -> str:
    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def decode_bytes(raw: bytes, charset: str | None = None) -> str:
    """Текст из байтов без мусора: заявленная кодировка, затем UTF-8, затем windows-1251 (частая в КП)."""
    for enc in (charset, "utf-8", "cp1251"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _eml_text(content: bytes) -> str:
    msg = BytesParser(policy=policy.default).parsebytes(content)
    head = [f"От: {msg.get('From', '')}", f"Тема: {msg.get('Subject', '')}", f"Дата: {msg.get('Date', '')}"]
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        # байты тела декодируем сами: у писем без Content-Type библиотека считает текст ASCII и портит кириллицу
        payload = body.get_payload(decode=True) or b""
        text = decode_bytes(payload, body.get_content_charset())
        if body.get_content_type() == "text/html":
            text = _html_text(text.encode("utf-8"))
    return "\n".join(head) + "\n\n" + text


def read_document(filename: str, content: bytes) -> Document:
    if not content:
        raise DocError("Файл пустой")
    if len(content) > MAX_BYTES:
        raise DocError("Файл больше 8 МБ")
    ext = _ext(filename)
    if ext not in ALLOWED:
        raise DocError("Формат не поддерживается. Подойдут: PDF, Word (.docx), Excel (.xlsx), письмо (.eml), "
                       "текст, HTML, картинка (png/jpg)")
    b64 = lambda: base64.standard_b64encode(content).decode()   # noqa: E731

    if ext in IMAGE_TYPES:
        return Document("", {"type": "image", "media_type": IMAGE_TYPES[ext], "data": b64()}, "image")
    if ext == ".pdf":
        text = _clean(_pdf_text(content))
        # есть текстовый слой: читаем текстом (дёшево и проверяемо); иначе скан, отдаём PDF модели
        media = None if len(text) > 80 else {"type": "document", "media_type": "application/pdf", "data": b64()}
        return Document(text, media, "pdf")
    if ext == ".docx":
        return Document(_clean(_docx_text(content)), None, "docx")
    if ext == ".xlsx":
        return Document(_clean(_xlsx_text(content)), None, "xlsx")
    if ext == ".eml":
        return Document(_clean(_eml_text(content)), None, "eml")
    if ext in (".html", ".htm"):
        return Document(_clean(_html_text(content)), None, "html")
    return Document(_clean(decode_bytes(content)), None, "text")
