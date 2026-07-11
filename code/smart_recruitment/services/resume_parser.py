from pathlib import Path


def extract_pdf_text(path: str) -> str:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        return ""

    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages).strip()


def summarize_resume_text(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return {
        "text_length": len(text),
        "headline": lines[0] if lines else "",
        "keywords": [],
    }


def safe_file_name(path: str) -> str:
    return Path(path).name

