import json
from pathlib import Path
from typing import Any


# ==================================================
# Knowledge Library Configuration
# ==================================================

KNOWLEDGE_ROOT = Path(__file__).resolve().parent

KNOWLEDGE_DIRECTORIES = (
    "testing",
    "security",
    "performance",
    "api",
    "accessibility",
    "domains",
)


# ==================================================
# Load One Knowledge File
# ==================================================

def load_knowledge_file(
    file_path: Path,
) -> dict[str, Any] | None:
    """
    Load one JSON knowledge file.

    Invalid files are ignored and reported through the
    returned error metadata rather than crashing the
    application.
    """

    try:

        with file_path.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(
                file
            )

    except (
        OSError,
        json.JSONDecodeError,
    ):

        return None

    if not isinstance(
        data,
        dict,
    ):
        return None

    return data


# ==================================================
# Load Knowledge Library
# ==================================================

def load_knowledge_library() -> dict[str, Any]:
    """
    Load all QA knowledge JSON files.

    This function only reads files.

    It does NOT:
        - generate test cases
        - modify requirements
        - modify templates
        - modify the database
        - modify the existing generator
    """

    knowledge_items: list[dict[str, Any]] = []
    loaded_files: list[str] = []
    skipped_files: list[str] = []

    for directory_name in KNOWLEDGE_DIRECTORIES:

        directory = (
            KNOWLEDGE_ROOT
            / directory_name
        )

        if not directory.exists():
            continue

        for file_path in sorted(
            directory.glob(
                "*.json"
            )
        ):

            data = load_knowledge_file(
                file_path
            )

            if data is None:

                skipped_files.append(
                    str(
                        file_path.relative_to(
                            KNOWLEDGE_ROOT
                        )
                    )
                )

                continue

            data = dict(
                data
            )

            data[
                "_source_file"
            ] = str(
                file_path.relative_to(
                    KNOWLEDGE_ROOT
                )
            )

            knowledge_items.append(
                data
            )

            loaded_files.append(
                str(
                    file_path.relative_to(
                        KNOWLEDGE_ROOT
                    )
                )
            )

    return {
        "knowledge_items": knowledge_items,
        "loaded_files": loaded_files,
        "skipped_files": skipped_files,
        "knowledge_count": len(
            knowledge_items
        ),
    }


# ==================================================
# Build AI Knowledge Context
# ==================================================

def build_knowledge_context(
    library: dict[str, Any],
) -> str:
    """
    Convert the loaded knowledge library into a compact
    JSON context that can later be supplied to the AI.

    This function does not decide which scenarios apply.
    Applicability decisions remain a separate step.
    """

    if not isinstance(
        library,
        dict,
    ):
        return "{}"

    items = library.get(
        "knowledge_items",
        [],
    )

    if not isinstance(
        items,
        list,
    ):
        return "{}"

    context_items = []

    for item in items:

        if not isinstance(
            item,
            dict,
        ):
            continue

        context_items.append(
            item
        )

    return json.dumps(
        context_items,
        ensure_ascii=False,
        indent=2,
    )


# ==================================================
# Simple Library Summary
# ==================================================

def get_knowledge_summary() -> dict[str, Any]:
    """
    Return a lightweight summary useful for diagnostics.
    """

    library = load_knowledge_library()

    summaries = []

    for item in library.get(
        "knowledge_items",
        [],
    ):

        if not isinstance(
            item,
            dict,
        ):
            continue

        summaries.append(
            {
                "name": item.get(
                    "name",
                    "Unknown",
                ),
                "knowledge_type": item.get(
                    "knowledge_type",
                    "Unknown",
                ),
                "source_file": item.get(
                    "_source_file",
                    "",
                ),
            }
        )

    return {
        "knowledge_count": library.get(
            "knowledge_count",
            0,
        ),
        "loaded_files": library.get(
            "loaded_files",
            [],
        ),
        "skipped_files": library.get(
            "skipped_files",
            [],
        ),
        "items": summaries,
    }