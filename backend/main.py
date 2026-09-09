import base64
import html
import io
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ai.provider import generate_text, get_ai_model, get_ai_provider
from ai.test_case_generator import generate_test_cases
from templates.analyzer import analyze_template

load_dotenv()

app = FastAPI(title="AI Test Case Generator API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "https://ai-testing-tools.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_TEMPLATE = {
    "filename": "standard-qa",
    "format": "standard",
    "columns": [
        "TC ID", "Requirement ID", "Test Scenario", "Preconditions",
        "Test Steps", "Expected Result", "Priority", "Test Type",
    ],
}

class GenerateRequest(BaseModel):
    requirement: str = Field(..., min_length=1)
    template: dict[str, Any] = Field(default_factory=dict)
    # Optional context from additional requirements, acceptance criteria,
    # business rules, UI/UX evidence and API specifications.
    source_context: str = ""

class TextRequest(BaseModel):
    prompt: str = Field(..., min_length=1)

class DevOpsConfig(BaseModel):
    organization: str = Field(..., min_length=1)
    project: str = Field(..., min_length=1)
    personal_access_token: str = Field(..., min_length=1)

class DevOpsCreateRequest(BaseModel):
    config: DevOpsConfig
    test_cases: list[dict[str, Any]] = Field(default_factory=list)
    module_name: str = ""
    user_story_id: str = ""
    # Optional Azure Test Plans destination. When both are supplied, the
    # published Test Case work items are also added to that suite.
    test_plan_id: str = ""
    test_suite_id: str = ""


def normalize_template(template: dict[str, Any] | None) -> dict[str, Any]:
    template = template if isinstance(template, dict) else {}
    columns = template.get("columns", [])
    if not isinstance(columns, list):
        columns = []
    columns = [str(x).strip() for x in columns if x is not None and str(x).strip()]
    if not columns:
        return {**DEFAULT_TEMPLATE, "columns": list(DEFAULT_TEMPLATE["columns"]), "column_count": len(DEFAULT_TEMPLATE["columns"])}
    return {**template, "columns": columns, "column_count": len(columns)}


def field_value(case: dict[str, Any], names: tuple[str, ...]) -> str:
    for key, value in case.items():
        if str(key).strip().lower() in {n.lower() for n in names} and value not in (None, ""):
            return str(value)
    return ""


def _clean_step_text(value: Any) -> str:
    """Convert one generated step/result value into plain display text."""
    if value is None:
        return ""

    if isinstance(value, dict):
        value = (
            value.get("text")
            or value.get("action")
            or value.get("step")
            or value.get("description")
            or value.get("expected")
            or value.get("expected_result")
            or value.get("expectedResult")
            or ""
        )

    if isinstance(value, (list, tuple)):
        parts = [_clean_step_text(item) for item in value]
        return " | ".join(part for part in parts if part)

    return str(value).strip()


def _split_values(value: Any) -> list[str]:
    """Convert generated step/expected data into clean individual strings."""
    if value is None:
        return []

    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            text = _clean_step_text(item)
            if not text:
                continue

            # A single generated list item can itself contain numbered lines.
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if len(lines) > 1:
                for line in lines:
                    line = re.sub(
                        r"^(?:step\s*)?\d+\s*[\.\):\-]\s*",
                        "",
                        line,
                        flags=re.I,
                    ).strip()
                    if line:
                        result.append(line)
            else:
                result.append(text)

        return result

    text = _clean_step_text(value)
    if not text:
        return []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        cleaned: list[str] = []
        for line in lines:
            line = re.sub(
                r"^(?:step\s*)?\d+\s*[\.\):\-]\s*",
                "",
                line,
                flags=re.I,
            ).strip()
            if line:
                cleaned.append(line)
        return cleaned

    return [text]


def _step_pairs(steps: Any, expected: Any) -> list[tuple[str, str]]:
    """
    Convert generated test-case data into Azure DevOps Action/Expected Result
    rows without dropping extra steps or expected results.
    """
    separate_expected = _split_values(expected)
    pairs: list[tuple[str, str]] = []

    # Preferred generator shape:
    # [{"action": "...", "expected": "..."}, ...]
    if isinstance(steps, list) and any(isinstance(item, dict) for item in steps):
        for item in steps:
            if not isinstance(item, dict):
                continue

            action = _clean_step_text(
                item.get("action")
                or item.get("step")
                or item.get("description")
                or item.get("text")
                or ""
            )
            exp = _clean_step_text(
                item.get("expected")
                or item.get("expected_result")
                or item.get("expectedResult")
                or item.get("result")
                or ""
            )

            if action or exp:
                pairs.append((action, exp))

        # Fill missing expectations from a separate expected-result column.
        for i in range(len(pairs)):
            if not pairs[i][1] and i < len(separate_expected):
                pairs[i] = (pairs[i][0], separate_expected[i])

        # Preserve every additional expected result.
        if len(separate_expected) > len(pairs):
            for exp in separate_expected[len(pairs):]:
                pairs.append(("", exp))

        return pairs

    step_values = _split_values(steps)
    expected_values = separate_expected

    for i, action in enumerate(step_values):
        exp = expected_values[i] if i < len(expected_values) else ""

        # Never invent an expectation. If exactly one expected result exists
        # for multiple actions, attach it only to the final action.
        if (
            not exp
            and len(expected_values) == 1
            and len(step_values) > 1
            and i == len(step_values) - 1
        ):
            exp = expected_values[0]

        pairs.append((action, exp))

    # Preserve additional expected results rather than silently dropping them.
    if len(expected_values) > len(step_values):
        for exp in expected_values[len(step_values):]:
            pairs.append(("", exp))

    return pairs


def _ado_parameterized_text(value: str) -> str:
    """
    Encode one Action/Expected Result value in the native Azure DevOps
    parameterizedString format.

    IMPORTANT:
    Microsoft.VSTS.TCM.Steps expects the text directly inside
    <parameterizedString>. Do not wrap generated text in <P>...</P>.
    Azure DevOps may accept the work-item update while rendering a
    P-wrapped value as an empty row in the manual Steps editor.
    """
    text = str(value or "").strip()
    if not text:
        return ""

    escaped = html.escape(text, quote=False)
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n")
    escaped = escaped.replace("\n", "<br/>")
    return escaped


def steps_to_html(steps: Any, expected: Any = None) -> str:
    """
    Build the native Azure DevOps Test Case Steps XML.

    Azure DevOps manual Test Cases store Action/Expected Result rows in
    Microsoft.VSTS.TCM.Steps. Each row uses two parameterizedString nodes.
    A ValidateStep is used when an expected result is present.
    """
    pairs = _step_pairs(steps, expected)

    rows: list[str] = []
    for i, (action, exp) in enumerate(pairs, 1):
        action_text = _ado_parameterized_text(action)
        expected_text = _ado_parameterized_text(exp)
        step_type = "ValidateStep" if str(exp or "").strip() else "ActionStep"

        rows.append(
            f'<step id="{i}" type="{step_type}">'
            f'<parameterizedString isformatted="true">{action_text}</parameterizedString>'
            f'<parameterizedString isformatted="true">{expected_text}</parameterizedString>'
            f'<description/>'
            f'</step>'
        )

    if not rows:
        return '<steps id="0" last="0"></steps>'

    return f'<steps id="0" last="{len(rows)}">{"".join(rows)}</steps>'


@app.get("/")
def root():
    return {"message": "AI Test Case Generator API", "status": "running", "provider": get_ai_provider(), "model": get_ai_model()}

@app.get("/api/health")
def health():
    return {"success": True, "provider": get_ai_provider(), "model": get_ai_model()}

@app.get("/api/ai/config")
def ai_config():
    return {"success": True, "provider": get_ai_provider(), "model": get_ai_model()}

@app.post("/api/ai/generate-text")
async def generate_text_endpoint(request: TextRequest):
    try:
        return {"success": True, "response": await generate_text(request.prompt.strip()), "provider": get_ai_provider(), "model": get_ai_model()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.post("/api/ai/generate")
async def generate(request: GenerateRequest):
    requirement = request.requirement.strip()
    if not requirement:
        return {"success": False, "error": "Requirement is required."}
    template = normalize_template(request.template)

    # Keep the source hierarchy explicit at the backend boundary so the
    # generator receives the same QA rules even if another client calls the API.
    generation_input = f"""SOURCE AUTHORITY AND COVERAGE RULES

1. Explicit requirements, acceptance criteria and business rules supplied by the user are authoritative.
2. Supporting requirements/design/API material may clarify or expand coverage only where it is relevant to the supplied feature.
3. The QA knowledge library is guidance for selecting applicable testing dimensions. It must never introduce an unrelated requirement.
4. Never silently invent permissions, performance targets, security policies, workflows, validations, limits or expected outcomes that are not supported by the supplied sources.
5. When information is missing or ambiguous, flag it for QA input/ambiguity review rather than inventing a requirement.
6. Analyze applicable features, roles, workflows, validations, business rules, error scenarios, permissions, UI/UX behavior, API behavior, performance-sensitive operations and security risks.
7. Generate deep positive, negative, boundary, validation and workflow/state coverage where supported.
8. Every test case must be traceable to a supplied requirement, behavior, rule, acceptance criterion, UI/API behavior, or clearly derived behavior.
9. Use the supplied template as the source of truth for output columns, terminology and format.
10. Generate Priority during test-case creation when the selected template contains a Priority field.

SUPPLIED PRIMARY SOURCE MATERIAL

{requirement}

SUPPLIED SUPPORTING / DESIGN / API SOURCE MATERIAL

{request.source_context.strip() if request.source_context.strip() else "No additional supporting source was supplied."}
"""

    try:
        result = await generate_test_cases(generation_input, template)
        if not isinstance(result, dict):
            result = {}
        return {
            "success": True,
            "mode": "review",
            "saved": False,
            "provider": get_ai_provider(),
            "model": get_ai_model(),
            "template": template,
            "assumptions": result.get("assumptions", []),
            "coverage_analysis": result.get("coverage_analysis", {}),
            "test_cases": result.get("test_cases", []),
            "source_analysis": {
                "primary_requirement": True,
                "additional_supporting_inputs": bool(request.source_context.strip()),
                "supporting_source_context_included": bool(request.source_context.strip()),
                "knowledge_library": True,
                "rules": [
                    "Explicit supplied requirements are authoritative.",
                    "Knowledge-library guidance is applicability-bound and cannot create unrelated requirements.",
                    "Ambiguous or missing information is surfaced for QA input rather than silently invented.",
                ],
            },
        }
    except Exception as exc:
        print("Generation error:", repr(exc))
        return {"success": False, "error": str(exc)}

@app.post("/api/templates/analyze")
async def analyze_template_endpoint(file: UploadFile = File(...)):
    if not file.filename:
        return {"success": False, "error": "No template file selected."}
    try:
        content = await file.read()
        template = normalize_template(analyze_template(file.filename, content))
        return {"success": True, "template": template}
    except Exception as exc:
        return {"success": False, "error": f"Template analysis failed: {exc}"}

@app.post("/api/requirements/extract")
async def extract_requirement(file: UploadFile = File(...)):
    if not file.filename:
        return {"success": False, "error": "No requirement file selected."}
    filename = file.filename.lower()
    content = await file.read()
    if not content:
        return {"success": False, "error": "The uploaded file is empty."}
    try:
        text = _extract_document_text(file.filename, content).strip()
        if not text:
            return {
                "success": False,
                "error": (
                    "Supported requirement files: TXT, MD, CSV, XLSX, DOCX or PDF. "
                    "No readable text was found."
                ),
            }
        return {"success": True, "filename": file.filename, "text": text}
    except Exception as exc:
        return {"success": False, "error": f"Requirement extraction failed: {exc}"}

def _extract_document_text(filename: str, content: bytes) -> str:
    """Extract readable text from supported requirement/supporting files."""
    lower = filename.lower()

    if lower.endswith((".txt", ".md", ".markdown", ".csv")):
        return content.decode("utf-8-sig")

    if lower.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        lines: list[str] = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                values = [str(v).strip() for v in row if v not in (None, "")]
                if values:
                    lines.append(" | ".join(values))
        return "\n".join(lines)

    if lower.endswith(".docx"):
        from docx import Document

        doc = Document(io.BytesIO(content))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

        # Include table content because business rules/acceptance criteria are
        # frequently stored in Word tables.
        for table in doc.tables:
            for row in table.rows:
                values = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if values:
                    paragraphs.append(" | ".join(values))

        return "\n".join(paragraphs)

    if lower.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    return ""


async def _analyze_image_with_gemini(filename: str, content: bytes) -> str:
    """Describe a supplied UI/UX screenshot so it can participate in QA analysis."""
    provider = get_ai_provider()

    if provider != "gemini":
        return (
            f"UI/UX screenshot supplied: {filename}. "
            "Visual analysis is unavailable for the currently selected AI provider."
        )

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = get_ai_model()

    if not api_key:
        return (
            f"UI/UX screenshot supplied: {filename}. "
            "GEMINI_API_KEY is not configured, so visual analysis could not be performed."
        )

    try:
        from google import genai
        from google.genai import types

        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        mime_type = mime_map.get(
            os.path.splitext(filename.lower())[1],
            "image/png",
        )

        client = genai.Client(api_key=api_key)
        prompt = f"""
Analyze this UI/UX screenshot only as evidence for QA test generation.

Filename: {filename}

Extract only observable, test-relevant information:
- visible screens/pages and major sections
- labels, buttons, links, fields, dropdowns, checkboxes and controls
- visible validation/error/help states
- navigation/workflow clues
- visible roles/permissions clues
- UI states and state transitions
- important layout/responsive behavior that is explicitly observable
- any text that appears to define expected behavior

Do not invent hidden functionality, backend rules, permissions, performance targets,
security requirements or business rules that cannot be supported by the screenshot.
Clearly say when something is not observable.
Return concise structured QA evidence.
"""
        response = await client.aio.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=content, mime_type=mime_type),
                prompt,
            ],
        )
        return str(response.text or "").strip()
    except Exception as exc:
        return (
            f"UI/UX screenshot supplied: {filename}. "
            f"Visual analysis failed safely: {exc}"
        )


@app.post("/api/requirements/extract-many")
async def extract_supporting_inputs(
    files: list[UploadFile] = File(...),
):
    """
    Extract multiple optional supporting QA inputs in one request.

    Documents are converted to text. UI/UX images are analyzed with Gemini
    when Gemini is the active provider. Nothing is persisted.
    """
    if not files:
        return {"success": False, "error": "No supporting files selected."}

    allowed_document_extensions = {
        ".txt", ".md", ".markdown", ".csv", ".xlsx", ".docx", ".pdf"
    }
    allowed_image_extensions = {".png", ".jpg", ".jpeg", ".webp"}

    extracted: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for file in files:
        filename = (file.filename or "").strip()
        if not filename:
            errors.append({"filename": "", "error": "A selected file has no filename."})
            continue

        content = await file.read()
        if not content:
            errors.append({"filename": filename, "error": "The uploaded file is empty."})
            continue

        extension = os.path.splitext(filename.lower())[1]

        try:
            if extension in allowed_document_extensions:
                text = _extract_document_text(filename, content).strip()
                if not text:
                    errors.append({
                        "filename": filename,
                        "error": "No readable text was found in the uploaded file.",
                    })
                    continue

                extracted.append({
                    "filename": filename,
                    "type": "document",
                    "text": text,
                })

            elif extension in allowed_image_extensions:
                text = await _analyze_image_with_gemini(filename, content)
                extracted.append({
                    "filename": filename,
                    "type": "ui_ux_screenshot",
                    "text": text,
                })

            else:
                errors.append({
                    "filename": filename,
                    "error": (
                        "Supported files: TXT, MD, CSV, XLSX, DOCX, PDF, "
                        "PNG, JPG or WEBP."
                    ),
                })
        except Exception as exc:
            errors.append({
                "filename": filename,
                "error": f"Extraction failed: {exc}",
            })

    if not extracted:
        return {
            "success": False,
            "error": "No supporting input could be read.",
            "files": [],
            "errors": errors,
        }

    return {
        "success": True,
        "files": extracted,
        "errors": errors,
    }


def ado_url(config: DevOpsConfig, path: str) -> str:
    org = config.organization.strip().strip("/")
    project = config.project.strip().strip("/")

    if path.startswith("_apis/projects"):
        return f"https://dev.azure.com/{org}/{path}"

    return f"https://dev.azure.com/{org}/{project}/{path}"


def _ado_auth_headers(config: DevOpsConfig, content_type: str = "application/json") -> dict[str, str]:
    token = base64.b64encode(
        f":{config.personal_access_token}".encode("utf-8")
    ).decode("ascii")

    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": content_type,
    }


async def ado_request(
    config: DevOpsConfig,
    method: str,
    url: str,
    *,
    content_type: str = "application/json",
    **kwargs,
):
    """
    Central Azure DevOps REST helper.

    GET/WIQL calls use JSON. Work-item create/update calls use JSON Patch.
    Keeping the content type correct avoids subtle API behavior differences.
    """
    headers = kwargs.pop("headers", {})
    merged_headers = _ado_auth_headers(config, content_type)
    merged_headers.update(headers)

    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        response = await client.request(
            method,
            url,
            headers=merged_headers,
            **kwargs,
        )

    if response.status_code >= 400:
        detail = response.text[:1000]
        raise RuntimeError(
            f"Azure DevOps returned {response.status_code}: {detail}"
        )

    if not response.content:
        return {}

    try:
        return response.json()
    except Exception:
        return {}


async def _get_work_item(
    config: DevOpsConfig,
    work_item_id: str,
    *,
    expand: str = "Relations",
) -> dict[str, Any]:
    """Read a source work item so publisher fields/links follow the project."""
    safe_id = str(work_item_id).strip()
    url = ado_url(
        config,
        f"_apis/wit/workitems/{safe_id}?$expand={expand}&api-version=7.1",
    )
    return await ado_request(config, "GET", url)


def _source_relation_url(config: DevOpsConfig, work_item_id: str) -> str:
    safe_id = str(work_item_id).strip()
    return ado_url(
        config,
        f"_apis/wit/workitems/{safe_id}?api-version=7.1",
    )


def _valid_priority(case: dict[str, Any]) -> str:
    raw = field_value(
        case,
        ("priority", "test priority", "priority level"),
    ).strip()

    # Azure DevOps manual Test Case Priority is normally 1-4.
    # Do not invent a priority if the generated case/template did not supply one.
    match = re.search(r"\b([1-4])\b", raw)
    return match.group(1) if match else ""


def _identity_patch_value(value: Any) -> str:
    """Convert Azure identity objects to a PATCH-safe identity string."""
    if isinstance(value, dict):
        return str(
            value.get("uniqueName")
            or value.get("mail")
            or value.get("displayName")
            or ""
        ).strip()
    return str(value or "").strip()


def _case_description(case: dict[str, Any], title: str = "") -> str:
    """
    Build the Azure DevOps Test Case Description using only the requested
    business-facing fields:

    - Issue Title / Test Case title
    - Preconditions
    - Priority
    - Test Type
    - User Story / Requirement ID, when provided

    Steps and Expected Results are intentionally NOT included here because
    they are stored in the native Microsoft.VSTS.TCM.Steps field.
    """
    parts: list[str] = []

    # Prefer the actual issue/test scenario title fields.
    issue_title = field_value(
        case,
        (
            "issue title",
            "issue_title",
            "test scenario",
            "test scenario title",
            "scenario title",
            "scenario",
            "test case title",
            "test case name",
            "test objective",
            "business check",
            "title",
        ),
    ).strip()

    if not issue_title:
        issue_title = str(title or "").strip()

    preconditions = field_value(
        case,
        (
            "preconditions",
            "pre-condition",
            "pre conditions",
        ),
    ).strip()

    priority = field_value(
        case,
        (
            "priority",
            "test priority",
            "priority level",
        ),
    ).strip()

    test_type = field_value(
        case,
        (
            "test type",
            "type",
        ),
    ).strip()

    user_story_id = field_value(
        case,
        (
            "user story id",
            "user_story_id",
            "user story",
            "requirement id",
            "requirement_id",
            "requirement",
            "work item",
            "work item id",
        ),
    ).strip()

    if issue_title:
        parts.append(
            f"<p><strong>Issue Title:</strong> {html.escape(issue_title)}</p>"
        )

    if preconditions:
        parts.append(
            f"<p><strong>Preconditions:</strong> {html.escape(preconditions)}</p>"
        )

    if priority:
        parts.append(
            f"<p><strong>Priority:</strong> {html.escape(priority)}</p>"
        )

    if test_type:
        parts.append(
            f"<p><strong>Test Type:</strong> {html.escape(test_type)}</p>"
        )

    # Only show User Story ID when it was actually supplied.
    if user_story_id:
        parts.append(
            f"<p><strong>User Story ID:</strong> {html.escape(user_story_id)}</p>"
        )

    # Keep a concise, meaningful provenance statement in the Description.
    parts.append(
        "<p><strong>Source:</strong> Generated by the AI Test Case Generator and reviewed by the QA tester.</p>"
    )

    return "".join(parts)

def _case_tags(case: dict[str, Any], module: str = "") -> str:
    """Build Azure DevOps semicolon-separated tags from supplied metadata."""
    tags: list[str] = ["AI-Generated"]

    test_type = field_value(case, ("test type", "type")).strip()
    if test_type:
        tags.append(test_type)

    if module:
        tags.append(module)

    cleaned: list[str] = []
    for tag in tags:
        tag = re.sub(r"[;\r\n]+", " ", str(tag)).strip()
        if tag and tag not in cleaned:
            cleaned.append(tag)

    return "; ".join(cleaned)


def _build_test_case_patch(
    case: dict[str, Any],
    title: str,
    step_xml: str,
    source_fields: dict[str, Any],
    source_url: str = "",
    module: str = "",
) -> list[dict[str, Any]]:
    """
    Build a native Azure DevOps Test Case work-item patch.

    The standard Test Case fields are populated directly. The source
    requirement is linked using the native "Tests" direction from the
    Test Case to the requirement, whose reverse is "Tested By" on the
    requirement/User Story.
    """
    patch: list[dict[str, Any]] = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {
            "op": "add",
            "path": "/fields/Microsoft.VSTS.TCM.Steps",
            "value": step_xml,
        },
        {
            "op": "add",
            "path": "/fields/Microsoft.VSTS.TCM.AutomationStatus",
            "value": "Not Automated",
        },
        {
            "op": "add",
            "path": "/fields/System.Description",
            "value": _case_description(case, title),
        },
        {
            "op": "add",
            "path": "/fields/System.Tags",
            "value": _case_tags(case, module),
        },
    ]

    area_path = source_fields.get("System.AreaPath")
    if area_path:
        patch.append(
            {"op": "add", "path": "/fields/System.AreaPath", "value": area_path}
        )

    iteration_path = source_fields.get("System.IterationPath")
    if iteration_path:
        patch.append(
            {
                "op": "add",
                "path": "/fields/System.IterationPath",
                "value": iteration_path,
            }
        )

    assigned_to = _identity_patch_value(source_fields.get("System.AssignedTo"))
    if assigned_to:
        patch.append(
            {
                "op": "add",
                "path": "/fields/System.AssignedTo",
                "value": assigned_to,
            }
        )

    priority = _valid_priority(case)
    if priority:
        patch.append(
            {
                "op": "add",
                "path": "/fields/Microsoft.VSTS.Common.Priority",
                "value": int(priority),
            }
        )

    if source_url:
        patch.append(
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "Microsoft.VSTS.Common.TestedBy-Reverse",
                    "url": source_url,
                    "attributes": {
                        "comment": "AI-generated test case linked to source requirement."
                    },
                },
            }
        )

    return patch


async def _find_existing_test_cases(
    config: DevOpsConfig,
    title: str,
) -> list[dict[str, Any]]:
    """Return exact-title Test Case candidates for duplicate/repair handling."""
    escaped_title = title.replace("'", "''")
    wiql = {
        "query": (
            "SELECT [System.Id] FROM WorkItems "
            "WHERE [System.TeamProject] = @project "
            "AND [System.WorkItemType] = 'Test Case' "
            f"AND [System.Title] = '{escaped_title}'"
        )
    }

    wiql_url = ado_url(config, "_apis/wit/wiql?api-version=7.1")
    result = await ado_request(
        config,
        "POST",
        wiql_url,
        content_type="application/json",
        json=wiql,
    )

    candidates: list[dict[str, Any]] = []
    for ref in result.get("workItems", []) or []:
        candidate_id = str(ref.get("id", "")).strip()
        if not candidate_id:
            continue
        try:
            candidates.append(
                await _get_work_item(
                    config,
                    candidate_id,
                    expand="Relations",
                )
            )
        except Exception:
            continue

    return candidates


def _relation_targets_work_item(
    work_item: dict[str, Any],
    target_id: str,
) -> bool:
    """Check either Tested By direction so old records remain detectable."""
    target_id = str(target_id).strip()
    for relation in work_item.get("relations", []) or []:
        relation_url = str(relation.get("url", "")).rstrip("/")
        relation_type = str(relation.get("rel", "")).strip()
        if (
            relation_url.endswith(f"/{target_id}")
            and relation_type
            in {
                "Microsoft.VSTS.Common.TestedBy-Forward",
                "Microsoft.VSTS.Common.TestedBy-Reverse",
            }
        ):
            return True
    return False


async def _find_existing_test_case(
    config: DevOpsConfig,
    title: str,
    source_work_item_id: str = "",
) -> dict[str, Any] | None:
    """
    Find an existing exact-title Test Case.

    When a source requirement is supplied, prefer a candidate already linked
    to that requirement. For legacy shells created by an earlier publisher,
    return an empty/unfinished candidate so it can be repaired instead of
    creating another duplicate.
    """
    candidates = await _find_existing_test_cases(config, title)

    if not candidates:
        return None

    source_id = str(source_work_item_id).strip()

    if not source_id:
        return candidates[0]

    for candidate in candidates:
        if _relation_targets_work_item(candidate, source_id):
            return candidate

    for candidate in candidates:
        fields = candidate.get("fields", {}) or {}
        steps = str(fields.get("Microsoft.VSTS.TCM.Steps", "") or "").strip()
        if not steps or 'last="0"' in steps:
            return candidate

    return None


def _add_source_relation_if_missing(
    patch: list[dict[str, Any]],
    work_item: dict[str, Any],
    source_url: str,
) -> None:
    """Add the correct Test Case -> requirement relation when absent."""
    if not source_url:
        return

    source_id = source_url.rstrip("/").split("/")[-1]
    if _relation_targets_work_item(work_item, source_id):
        return

    patch.append(
        {
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "Microsoft.VSTS.Common.TestedBy-Reverse",
                "url": source_url,
                "attributes": {
                    "comment": "AI-generated test case linked to source requirement."
                },
            },
        }
    )


@app.post("/api/devops/test-connection")
async def devops_test_connection(config: DevOpsConfig):
    try:
        # Organization-level endpoint. The project is intentionally not part
        # of this URL.
        url = ado_url(
            config,
            "_apis/projects?api-version=7.1",
        )
        data = await ado_request(
            config,
            "GET",
            url,
        )

        return {
            "success": True,
            "message": "Azure DevOps connection successful.",
            "project_count": len(data.get("value", [])),
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }



def _stored_step_count(step_xml: Any) -> int:
    """Count native Azure DevOps step nodes stored in the Steps field."""
    if not step_xml:
        return 0
    return len(re.findall(r"<step\b", str(step_xml), flags=re.I))


def _has_native_steps(step_xml: Any) -> bool:
    """Require at least one real native <step> node."""
    return _stored_step_count(step_xml) > 0


async def _verify_test_case(
    config: DevOpsConfig,
    work_item_id: str,
    source_id: str = "",
) -> dict[str, Any]:
    """
    Re-read the Test Case after create/update.

    Publishing is only considered successful when Azure DevOps returns a real
    Test Case with native Steps and, when a source requirement was supplied,
    the correct Tests relation.
    """
    item = await _get_work_item(config, work_item_id, expand="Relations")
    fields = item.get("fields", {}) or {}
    work_item_type = str(fields.get("System.WorkItemType", "")).strip()
    stored_steps = str(
        fields.get("Microsoft.VSTS.TCM.Steps", "") or ""
    ).strip()

    if work_item_type.lower() != "test case":
        raise RuntimeError(
            f"Azure DevOps returned work item {work_item_id} as "
            f"'{work_item_type or 'Unknown'}', not 'Test Case'."
        )

    if not _has_native_steps(stored_steps):
        raise RuntimeError(
            "Azure DevOps created the Test Case but its native Steps field "
            "contains no step nodes."
        )

    linked = None
    if source_id:
        linked = _relation_targets_work_item(item, source_id)
        if not linked:
            raise RuntimeError(
                f"Test Case {work_item_id} was created but is not linked to "
                f"source work item {source_id} with the native Tests relation."
            )

    return {
        "item": item,
        "fields": fields,
        "step_count": _stored_step_count(stored_steps),
        "linked_to_source": linked,
    }

def _normalized_case_key(value: Any) -> str:
    """Normalize a generated case field name for tolerant title lookup."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _is_generic_generated_title(value: Any) -> bool:
    """Identify placeholder titles that must never become the Azure title."""
    title = str(value or "").strip()
    if not title:
        return True

    normalized = re.sub(r"\s+", " ", title).strip().lower()
    normalized = re.sub(r"^\[[^\]]+\]\s*", "", normalized)

    generic_patterns = (
        r"^ai generated test case(?:\s+\d+)?$",
        r"^generated test case(?:\s+\d+)?$",
        r"^test case(?:\s+\d+)?$",
        r"^ai test case(?:\s+\d+)?$",
    )
    return any(re.fullmatch(pattern, normalized, flags=re.I) for pattern in generic_patterns)


def _case_title(case: dict[str, Any], index: int) -> str:
    """
    Resolve the real business/scenario title without changing generated
    test-case content.

    Scenario fields are intentionally preferred over generic title fields.
    This prevents placeholders such as "AI Generated Test Case 1" from being
    published as the Azure DevOps work-item title.
    """
    if not isinstance(case, dict):
        return f"Test Case {index}"

    # Prefer fields that represent the actual scenario/business behavior.
    preferred_keys = (
        "test scenario",
        "scenario",
        "test scenario title",
        "scenario title",
        "test case title",
        "test case name",
        "test objective",
        "business check",
        "objective",
        "title",
        "test case",
    )

    values_by_key = {
        _normalized_case_key(key): value
        for key, value in case.items()
        if value not in (None, "")
    }

    for preferred in preferred_keys:
        value = values_by_key.get(_normalized_case_key(preferred))
        if value is None:
            continue

        # A list/dict title is not a valid work-item title; use its text only
        # when it resolves to a meaningful scalar value.
        if isinstance(value, (dict, list, tuple)):
            value = _clean_step_text(value)

        candidate = str(value).strip()
        if candidate and not _is_generic_generated_title(candidate):
            return candidate

    # Some generator/template versions use a key containing the same title
    # concept with extra wording. Handle those without changing the data.
    for key, value in case.items():
        normalized_key = _normalized_case_key(key)
        if not any(
            token in normalized_key
            for token in ("scenario", "test objective", "business check", "test case title")
        ):
            continue

        candidate = _clean_step_text(value)
        if candidate and not _is_generic_generated_title(candidate):
            return candidate

    # Last-resort fallback: keep the existing behavior, but make the fallback
    # explicit. This is used only when the generated case contains no usable
    # scenario/title information at all.
    return f"AI Generated Test Case {index}"


@app.post("/api/devops/create-test-cases")
async def devops_create_test_cases(
    request: DevOpsCreateRequest,
):
    """
    Publish reviewed AI cases as managed Azure DevOps Test artifacts.

    The publisher does NOT create a Task to represent a test case. Each
    reviewed case becomes a native Test Case work item, with native manual
    Action/Expected Result rows, process-derived Area/Iteration/Assignee
    values, a native Tests link back to the selected requirement work item,
    duplicate/repair handling, and post-write verification.

    A Test Case work item can exist without a Test Plan. When a specific
    test_plan_id and test_suite_id are supplied, the publisher also associates
    the created/repaired Test Cases with that existing Test Suite. It never
    invents a Test Plan or Suite.
    """
    if not request.test_cases:
        return {"success": False, "error": "No test cases to send."}

    source_id = request.user_story_id.strip()
    source_item: dict[str, Any] = {}
    source_fields: dict[str, Any] = {}
    source_url = ""

    if source_id:
        try:
            source_item = await _get_work_item(
                request.config,
                source_id,
                expand="Relations",
            )
        except Exception as exc:
            return {
                "success": False,
                "error": f"Unable to read source work item {source_id}. {exc}",
            }

        source_fields = source_item.get("fields", {}) or {}
        source_type = str(
            source_fields.get("System.WorkItemType", "")
        ).strip()

        # System.WorkItemType is optional metadata for publishing. Some
        # Azure DevOps responses can omit it even when the work item itself
        # was successfully retrieved. Do not block Test Case publishing.
        if not source_type:
            print(
                f"Warning: Azure DevOps work item {source_id} "
                "did not return System.WorkItemType. "
                "Continuing without source work-item type."
            )

        # Keep the publisher compatible with common Azure DevOps process
        # templates. We do not hard-code one process-specific source type,
        # because Agile/Scrum/CMMI use different requirement work-item names.
        source_type_supported = source_type.lower() in {
            "user story",
            "product backlog item",
            "requirement",
            "issue",
            "feature",
            "epic",
        }
        if not source_type_supported:
            return {
                "success": False,
                "error": (
                    f"Source work item {source_id} is '{source_type}'. "
                    "Select a requirement-style work item such as User Story, "
                    "Product Backlog Item, Requirement, Issue, Feature or Epic."
                ),
            }

        source_url = str(
            source_item.get("url")
            or _source_relation_url(request.config, source_id)
        )

    created: list[dict[str, Any]] = []
    repaired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for index, case in enumerate(request.test_cases, 1):
        title = _case_title(case, index)

        module = request.module_name.strip()
        if module and not title.startswith(f"[{module}]"):
            title = f"[{module}] {title}"

        steps_value = None
        expected_value = None

        for key, value in case.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in {"test steps", "steps", "test step"}:
                steps_value = value
            elif normalized_key in {
                "expected result",
                "expected outcome",
                "result",
                "expected results",
            }:
                expected_value = value

        if steps_value is None:
            steps_value = field_value(case, ("test steps", "steps", "test step"))

        if expected_value is None:
            expected_value = field_value(
                case,
                (
                    "expected result",
                    "expected outcome",
                    "result",
                    "expected results",
                ),
            )

        pairs = _step_pairs(steps_value, expected_value)
        if not pairs or not any(action or exp for action, exp in pairs):
            failed.append({
                "title": title,
                "error": (
                    "The test case has no executable steps or expected "
                    "results. It was not published."
                ),
            })
            continue

        step_xml = steps_to_html(steps_value, expected_value)

        try:
            existing = await _find_existing_test_case(
                request.config,
                title,
                source_id,
            )
        except Exception as exc:
            failed.append({
                "title": title,
                "error": f"Duplicate check failed: {exc}",
            })
            continue

        try:
            if existing:
                existing_id = str(existing.get("id", "")).strip()
                if not existing_id:
                    raise RuntimeError("Existing Test Case has no ID.")

                existing_fields = existing.get("fields", {}) or {}
                existing_steps = str(
                    existing_fields.get("Microsoft.VSTS.TCM.Steps", "") or ""
                ).strip()

                if (
                    source_id
                    and _relation_targets_work_item(existing, source_id)
                    and _has_native_steps(existing_steps)
                    and "<P>" not in existing_steps
                    and "<p>" not in existing_steps
                ):
                    skipped.append({
                        "id": existing_id,
                        "title": title,
                        "skipped_duplicate": True,
                        "reason": (
                            "A complete Test Case with the same title is "
                            "already linked to the selected source work item."
                        ),
                    })
                    continue

                repair_patch: list[dict[str, Any]] = [
                    {
                        "op": "add",
                        "path": "/fields/Microsoft.VSTS.TCM.Steps",
                        "value": step_xml,
                    },
                    {
                        "op": "add",
                        "path": "/fields/Microsoft.VSTS.TCM.AutomationStatus",
                        "value": "Not Automated",
                    },
                    {
                        "op": "add",
                        "path": "/fields/System.Description",
                        "value": _case_description(case, title),
                    },
                    {
                        "op": "add",
                        "path": "/fields/System.Tags",
                        "value": _case_tags(case, module),
                    },
                ]

                area_path = source_fields.get("System.AreaPath")
                if area_path:
                    repair_patch.append({
                        "op": "add",
                        "path": "/fields/System.AreaPath",
                        "value": area_path,
                    })

                iteration_path = source_fields.get("System.IterationPath")
                if iteration_path:
                    repair_patch.append({
                        "op": "add",
                        "path": "/fields/System.IterationPath",
                        "value": iteration_path,
                    })

                assigned_to = _identity_patch_value(source_fields.get("System.AssignedTo"))
                if assigned_to:
                    repair_patch.append({
                        "op": "add",
                        "path": "/fields/System.AssignedTo",
                        "value": assigned_to,
                    })

                priority = _valid_priority(case)
                if priority:
                    repair_patch.append({
                        "op": "add",
                        "path": "/fields/Microsoft.VSTS.Common.Priority",
                        "value": int(priority),
                    })

                _add_source_relation_if_missing(
                    repair_patch,
                    existing,
                    source_url,
                )

                repair_url = ado_url(
                    request.config,
                    f"_apis/wit/workitems/{existing_id}?api-version=7.1",
                )

                repaired_item = await ado_request(
                    request.config,
                    "PATCH",
                    repair_url,
                    content_type="application/json-patch+json",
                    json=repair_patch,
                )

                repaired_verification = await _get_work_item(
                    request.config,
                    existing_id,
                    expand="Relations",
                )
                repaired_fields = repaired_verification.get("fields", {}) or {}
                repaired_steps = str(
                    repaired_fields.get("Microsoft.VSTS.TCM.Steps", "") or ""
                ).strip()

                if (
                    not _has_native_steps(repaired_steps)
                    or "<P>" in repaired_steps
                    or "<p>" in repaired_steps
                ):
                    raise RuntimeError(
                        "Azure DevOps accepted the update but the native "
                        "Steps field is not in the renderable format."
                    )

                if source_id and not _relation_targets_work_item(
                    repaired_verification,
                    source_id,
                ):
                    raise RuntimeError(
                        f"Test Case {existing_id} is not linked to source "
                        f"work item {source_id}."
                    )

                repaired.append({
                    "id": existing_id,
                    "title": title,
                    "step_count": _stored_step_count(repaired_steps),
                    "steps_published": True,
                    "linked_to_source": (
                        _relation_targets_work_item(
                            repaired_verification,
                            source_id,
                        )
                        if source_id
                        else None
                    ),
                    "work_item_type": repaired_fields.get("System.WorkItemType"),
                    "web_url": repaired_item.get("_links", {})
                        .get("html", {})
                        .get("href"),
                })
                continue

            patch = _build_test_case_patch(
                case,
                title,
                step_xml,
                source_fields,
                source_url,
                module,
            )

            create_url = ado_url(
                request.config,
                "_apis/wit/workitems/$Test%20Case?api-version=7.1",
            )

            item = await ado_request(
                request.config,
                "POST",
                create_url,
                content_type="application/json-patch+json",
                json=patch,
            )

            work_item_id = str(item.get("id", "")).strip()
            if not work_item_id:
                raise RuntimeError(
                    "Azure DevOps returned a successful response but "
                    "did not return a Test Case ID."
                )

            verification_result = await _verify_test_case(
                request.config,
                work_item_id,
                source_id,
            )
            verification = verification_result["item"]
            verification_fields = verification_result["fields"]
            stored_steps = str(
                verification_fields.get("Microsoft.VSTS.TCM.Steps", "") or ""
            ).strip()


            created.append({
                "id": work_item_id,
                "title": title,
                "step_count": verification_result["step_count"],
                "steps_published": True,
                "linked_to_source": (
                    _relation_targets_work_item(verification, source_id)
                    if source_id
                    else None
                ),
                "area_path": verification_fields.get("System.AreaPath"),
                "iteration_path": verification_fields.get("System.IterationPath"),
                "assigned_to": verification_fields.get("System.AssignedTo"),
                "work_item_type": verification_fields.get("System.WorkItemType"),
                "web_url": item.get("_links", {})
                    .get("html", {})
                    .get("href"),
            })

        except Exception as exc:
            failed.append({
                "title": title,
                "error": str(exc),
            })

    suite_result: dict[str, Any] | None = None
    plan_id = request.test_plan_id.strip()
    suite_id = request.test_suite_id.strip()

    suite_candidate_ids = [
        str(item.get("id"))
        for item in created + repaired
        if item.get("id")
    ]

    if plan_id or suite_id:
        if not plan_id or not suite_id:
            failed.append({
                "title": "Test Plans destination",
                "error": (
                    "Both test_plan_id and test_suite_id are required "
                    "to add published Test Cases to a Test Suite."
                ),
            })
        elif suite_candidate_ids:
            try:
                ids_path = ",".join(suite_candidate_ids)
                suite_url = ado_url(
                    request.config,
                    (
                        f"_apis/test/Plans/{plan_id}/suites/{suite_id}/"
                        f"testcases/{ids_path}?api-version=7.1"
                    ),
                )
                suite_data = await ado_request(
                    request.config,
                    "POST",
                    suite_url,
                    content_type="application/json",
                )
                suite_result = {
                    "success": True,
                    "test_plan_id": plan_id,
                    "test_suite_id": suite_id,
                    "test_case_ids": suite_candidate_ids,
                    "response": suite_data,
                }
            except Exception as exc:
                failed.append({
                    "title": "Test Plans destination",
                    "error": f"Test Suite association failed: {exc}",
                })
                suite_result = {
                    "success": False,
                    "test_plan_id": plan_id,
                    "test_suite_id": suite_id,
                    "test_case_ids": suite_candidate_ids,
                    "error": str(exc),
                }

    published_count = len(created) + len(repaired)

    return {
        "success": published_count > 0 and not failed,
        "created": created,
        "repaired": repaired,
        "skipped": skipped,
        "failed": failed,
        "created_count": len(created),
        "repaired_count": len(repaired),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "published_count": published_count,
        "published_test_case_ids": [
            str(item.get("id"))
            for item in created + repaired
            if item.get("id")
        ],
        "source_work_item_id": source_id or None,
        "source_work_item_type": (
            source_fields.get("System.WorkItemType")
            if source_fields
            else None
        ),
        "test_plan": suite_result,
        "message": (
            f"Published {published_count} Test Case(s), "
            f"repaired {len(repaired)} incomplete case(s), "
            f"skipped {len(skipped)} duplicate(s), "
            f"failed {len(failed)}."
        ),
    }
