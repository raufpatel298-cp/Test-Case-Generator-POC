from pathlib import Path


def extract_text_from_txt(file_path: str) -> str:
    path = Path(file_path)

    if path.suffix.lower() != ".txt":
        raise ValueError("Only TXT files are supported right now.")

    return path.read_text(encoding="utf-8")