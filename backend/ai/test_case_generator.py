import json
import re
import asyncio
from typing import Any

from ai.provider import generate_text

from knowledge.applicability import (
    build_applicable_knowledge_context,
)
from knowledge.coverage_inventory import (
    extract_coverage_inventory,
)
from knowledge.coverage_mapping import (
    map_inventory_to_coverage_targets,
)
from knowledge.loader import (
    load_knowledge_library,
)


# ==================================================
# Source-Aware Analysis Input
# ==================================================

def _build_source_aware_analysis_input(
    requirement: str,
    source_context: str,
) -> str:
    """
    Combine the primary requirement with supplied supporting
    project evidence for deterministic coverage extraction.

    The labels preserve the distinction between written
    requirements and additional design/document evidence.
    """

    primary = str(requirement or "").strip()
    supporting = str(source_context or "").strip()

    if not supporting:
        return primary

    return f"""
===== PRIMARY REQUIREMENT / DESIGN INPUT =====
{primary}
===== END PRIMARY REQUIREMENT / DESIGN INPUT =====

===== ADDITIONAL PROJECT SOURCE EVIDENCE =====
{supporting}
===== END ADDITIONAL PROJECT SOURCE EVIDENCE =====
""".strip()



# ==================================================
# Large-Generation / AI Request Safety
# ==================================================

AI_GENERATION_BATCH_SIZE = 25
AI_AUDIT_CASE_THRESHOLD = 50
AI_CONTEXT_MAX_CHARS = 30000
AI_RETRY_COUNT = 2
AI_RETRY_DELAY_SECONDS = 45
MAX_TEST_CASES = 500


async def _generate_text_with_retry(prompt: str) -> str:
    """Retry temporary Gemini quota/rate-limit responses."""
    last_error = None
    for attempt in range(AI_RETRY_COUNT + 1):
        try:
            return await generate_text(prompt)
        except Exception as exc:
            last_error = exc
            message = str(exc).casefold()
            quota_error = (
                "429" in message
                or "resource_exhausted" in message
                or "quota" in message
                or "rate limit" in message
            )
            if not quota_error or attempt >= AI_RETRY_COUNT:
                raise
            await asyncio.sleep(AI_RETRY_DELAY_SECONDS)
    raise last_error


def _compact_context(
    text: str,
    max_chars: int = AI_CONTEXT_MAX_CHARS,
) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + (
        "\n[Context truncated for AI request-size safety.]"
    )


def _compact_test_cases_for_audit(
    test_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep audit prompts small by sending scenario-level data only."""
    compact = []
    for index, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict):
            continue

        requirement_reference = ""
        test_type = ""

        for key, value in case.items():
            if (
                "requirement" in str(key).casefold()
                and str(value).strip()
            ):
                requirement_reference = str(value)
                break

        for key, value in case.items():
            if (
                str(key).casefold().strip() == "test type"
                and str(value).strip()
            ):
                test_type = str(value)
                break

        compact.append({
            "test_case_id": f"TC-{index:03d}",
            "scenario": _get_test_case_scenario(case),
            "requirement_reference": requirement_reference,
            "test_type": test_type,
        })

    return compact


def _deterministic_generation_targets(
    coverage_targets: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build a generation checklist directly from deterministic coverage targets.

    The AI coverage plan is advisory for scenario wording, but the deterministic
    target list is the authoritative checklist. One target becomes one requested
    generation item unless the target data itself is invalid.
    """
    targets = coverage_targets.get("targets", [])
    if not isinstance(targets, list):
        return []

    scenarios = []
    for index, target in enumerate(targets, start=1):
        if not isinstance(target, dict):
            continue

        obligation_id = str(target.get("obligation_id", "")).strip()
        title = str(target.get("title", "")).strip()
        behavior = str(target.get("expected_behavior", "")).strip()
        source_requirement = str(target.get("source_requirement", "")).strip()
        dimension = str(target.get("coverage_type", "Functional")).strip() or "Functional"

        if not obligation_id or not (title or behavior):
            continue

        description = title
        if behavior and behavior.casefold() != title.casefold():
            description = f"{title}. {behavior}" if title else behavior

        scenarios.append({
            "scenario_id": f"TARGET-{index:03d}",
            "requirement_ids": [source_requirement] if source_requirement else [],
            "dimension": dimension,
            "description": description,
            "coverage_obligation_id": obligation_id,
        })

    return scenarios


def _deterministic_missing_targets(
    coverage_targets: dict[str, Any],
    test_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find uncovered targets without another large Gemini audit call."""
    targets = coverage_targets.get("targets", [])
    if not isinstance(targets, list):
        return []

    missing = []

    for target in targets:
        if not isinstance(target, dict):
            continue

        best = 0.0

        for case in test_cases:
            best = max(
                best,
                _coverage_specific_match_score(target, case),
                _coverage_target_score(target, case),
            )

        if best < 0.45:
            missing.append({
                "requirement_ids": [
                    target.get("source_requirement", "")
                ],
                "dimension": target.get(
                    "coverage_type",
                    "Functional",
                ),
                "description": target.get("title", ""),
                "reason": target.get(
                    "expected_behavior",
                    "",
                ),
                "coverage_obligation_id": target.get(
                    "obligation_id",
                    "",
                ),
            })

    return missing


# ==================================================
# Default Coverage Structure
# ==================================================

DEFAULT_COVERAGE_ANALYSIS = {
    "requirements": [],
    "covered_requirements": [],
    "uncovered_requirements": [],
    "ambiguous_requirements": [],
    "missing_acceptance_criteria": [],
    "missing_performance_criteria": [],
    "missing_security_criteria": [],
    "duplicate_or_overlapping_scenarios": [],
    "coverage_traceability": [],
}


# ==================================================
# Main Generation Function
# ==================================================

async def generate_test_cases(
    requirement: str,
    template: dict[str, Any],
    source_context: str = "",
) -> dict:
    """
    Coverage-driven AI test-case generation.

    The uploaded test-case template is the source of truth.

    Generation flow:

        Requirement
            ↓
        Requirement-bound knowledge applicability
            ↓
        Deterministic coverage inventory
            ↓
        Deterministic coverage mapping
            ↓
        AI coverage planning
            ↓
        Initial test-case generation
            ↓
        Coverage audit
            ↓
        Missing-case generation
            ↓
        Deterministic obligation traceability
            ↓
        Final validation
            ↓
        Final QA draft

    IMPORTANT:
    There is NO arbitrary 3-5 test-case limit.

    The number of generated cases is determined by the
    actual coverage required by the supplied requirement.

    Knowledge-library rules:

    - The supplied requirement is the authority.
    - Knowledge provides testing guidance only.
    - Knowledge cannot create unsupported requirements.
    - Every coverage target must remain traceable to an
      extracted requirement obligation.

    Internal coverage metadata is never added to the
    user's uploaded test-case template.
    """

    columns = _normalize_template_columns(
        template
    )

    # --------------------------------------------------
    # Validate template
    # --------------------------------------------------

    if not columns:
        return {
            "test_cases": [],
            "assumptions": [
                "The uploaded test-case template contains no columns."
            ],
            "coverage_analysis": (
                _empty_coverage_analysis()
            ),
        }

    # --------------------------------------------------
    # Source-aware requirement/design evidence
    # --------------------------------------------------

    source_context = (
        source_context.strip()
        if isinstance(source_context, str)
        else ""
    )

    analysis_requirement = _build_source_aware_analysis_input(
        requirement=requirement,
        source_context=source_context,
    )

    # --------------------------------------------------
    # Requirement-Bound QA Knowledge
    # --------------------------------------------------

    knowledge_library = load_knowledge_library()

    knowledge_context = (
        build_applicable_knowledge_context(
            requirement,
            knowledge_library,
        )
    )

    coverage_inventory = (
        extract_coverage_inventory(
            analysis_requirement,
        )
    )

    coverage_targets = (
        map_inventory_to_coverage_targets(
            coverage_inventory,
        )
    )

    knowledge_context_text = _compact_context(
        _build_knowledge_context_text(
            knowledge_context,
            coverage_inventory,
            coverage_targets,
        )
    )

    template_filename = str(
        template.get(
            "filename",
            "uploaded template",
        )
    )

    template_format = str(
        template.get(
            "format",
            "unknown",
        )
    )

    template_context = _build_template_context(
        template=template,
        columns=columns,
        filename=template_filename,
        template_format=template_format,
    )

    assumptions: list[str] = []

    # --------------------------------------------------
    # Deterministic coverage information
    # --------------------------------------------------

    inventory_count = coverage_inventory.get(
        "obligation_count",
        0,
    )

    target_count = coverage_targets.get(
        "target_count",
        0,
    )

    explicit_target_count = coverage_targets.get(
        "explicit_target_count",
        0,
    )

    qa_input_target_count = coverage_targets.get(
        "qa_input_target_count",
        0,
    )

    assumptions.append(
        f"Deterministic requirement coverage inventory identified "
        f"{inventory_count} obligation(s) and {target_count} "
        f"coverage target(s)."
    )

    if explicit_target_count:
        assumptions.append(
            f"{explicit_target_count} coverage target(s) are explicitly "
            "supported by the supplied requirement."
        )

    if qa_input_target_count:
        assumptions.append(
            f"{qa_input_target_count} coverage target(s) require "
            "QA clarification because the requirement does not "
            "define the necessary policy or expected behavior."
        )

    # ==================================================
    # PASS 1 - Requirement Decomposition
    # ==================================================

    coverage_plan_response = await _generate_text_with_retry(
        _build_coverage_plan_prompt(
            requirement=requirement,
            source_context=source_context,
            template_context=template_context,
            knowledge_context=knowledge_context_text,
        )
    )

    coverage_plan = _parse_json_object(
        coverage_plan_response
    )

    if coverage_plan is None:

        coverage_plan = {}

        assumptions.append(
            "AI coverage-planning response was not valid JSON."
        )

    planned_requirements = (
        _normalize_requirement_inventory(
            coverage_plan.get(
                "requirements",
                [],
            )
        )
    )

    planned_dimensions = (
        _normalize_string_list(
            coverage_plan.get(
                "coverage_dimensions",
                [],
            )
        )
    )

    planned_scenarios = coverage_plan.get(
        "planned_test_scenarios",
        [],
    )

    if not isinstance(
        planned_scenarios,
        list,
    ):
        planned_scenarios = []

    assumptions.extend(
        _normalize_string_list(
            coverage_plan.get(
                "assumptions",
                [],
            )
        )
    )

    # ==================================================
    # PASS 2 - Initial Complete Generation
    # ==================================================

    # The deterministic targets are the mandatory generation checklist.
    # This prevents a short AI planning response (for example 17 scenarios)
    # from silently shrinking the final test suite when the deterministic
    # inventory contains many more supported obligations.
    deterministic_generation_targets = _deterministic_generation_targets(
        coverage_targets
    )

    generation_checklist = (
        deterministic_generation_targets
        if deterministic_generation_targets
        else planned_scenarios
    )

    assumptions.append(
        f"Generation checklist contains {len(generation_checklist)} item(s) "
        "from deterministic coverage targets."
        if deterministic_generation_targets
        else "No deterministic generation targets were available; AI-planned scenarios were used as the generation checklist."
    )

    # Generate checklist items in bounded batches. This permits 100+ final
    # cases without sending one enormous prompt to Gemini.
    generation_batches = []

    if generation_checklist:
        for batch_start in range(
            0,
            len(generation_checklist),
            AI_GENERATION_BATCH_SIZE,
        ):
            generation_batches.append(
                generation_checklist[
                    batch_start:
                    batch_start + AI_GENERATION_BATCH_SIZE
                ]
            )
    else:
        generation_batches = [[]]

    raw_cases_collected = []

    for batch_number, scenario_batch in enumerate(
        generation_batches,
        start=1,
    ):
        batch_response = await _generate_text_with_retry(
            _build_generation_prompt(
                requirement=requirement,
                source_context=source_context,
                template_context=template_context,
                planned_requirements=planned_requirements,
                planned_dimensions=planned_dimensions,
                planned_scenarios=scenario_batch,
                knowledge_context=knowledge_context_text,
            )
        )

        batch_data = _parse_json_object(
            batch_response
        )

        if batch_data is None:
            assumptions.append(
                f"AI generation batch {batch_number} returned invalid JSON."
            )
            continue

        raw_batch_cases = batch_data.get(
            "test_cases",
            [],
        )

        if isinstance(raw_batch_cases, list):
            raw_cases_collected.extend(raw_batch_cases)

        assumptions.extend(
            _normalize_string_list(
                batch_data.get(
                    "assumptions",
                    [],
                )
            )
        )

        if len(raw_cases_collected) >= MAX_TEST_CASES:
            assumptions.append(
                f"Generation reached the technical safety limit of "
                f"{MAX_TEST_CASES} test cases."
            )
            break

    test_cases = _normalize_test_cases(
        raw_cases_collected[:MAX_TEST_CASES],
        columns,
        assumptions,
    )

    _normalize_step_expected_results(
        test_cases,
        columns,
        assumptions,
    )

    # --------------------------------------------------
    # Structural step/expected-result repair
    # --------------------------------------------------
    # The generator must not return four steps with one combined expected
    # result. Repair only mismatched cases and only the Expected Result field.
    await _repair_step_expected_results_with_ai(
        test_cases=test_cases,
        columns=columns,
        requirement=requirement,
        source_context=source_context,
        knowledge_context=knowledge_context_text,
        assumptions=assumptions,
    )

    coverage_analysis = normalize_coverage_analysis({})

    coverage_analysis = _merge_coverage_planning(
        coverage_analysis,
        planned_requirements,
        coverage_plan,
    )

    if len(test_cases) >= MAX_TEST_CASES:
        assumptions.append(
            f"The final generation is capped at the technical safety "
            f"maximum of {MAX_TEST_CASES} cases."
        )

    # ==================================================
    # PASS 3 - Coverage Audit
    # ==================================================

    if len(test_cases) > AI_AUDIT_CASE_THRESHOLD:
        # Avoid sending 50+ full test cases back to Gemini. The
        # deterministic target scorer performs the audit locally.
        missing_test_cases = _deterministic_missing_targets(
            coverage_targets,
            test_cases,
        )
        assumptions.append(
            "Large-generation mode used deterministic coverage auditing "
            f"because the generated set contains more than "
            f"{AI_AUDIT_CASE_THRESHOLD} cases."
        )
    else:
        audit_response = await _generate_text_with_retry(
            _build_coverage_audit_prompt(
                requirement=requirement,
                source_context=source_context,
                template_context=template_context,
                planned_requirements=planned_requirements,
                planned_scenarios=planned_scenarios,
                test_cases=_compact_test_cases_for_audit(test_cases),
                knowledge_context=knowledge_context_text,
            )
        )

        audit_data = _parse_json_object(
            audit_response
        )

        if audit_data is None:
            audit_data = {}
            assumptions.append(
                "AI coverage-audit response was not valid JSON."
            )

        assumptions.extend(
            _normalize_string_list(
                audit_data.get(
                    "assumptions",
                    [],
                )
            )
        )

        coverage_analysis = _merge_coverage_analysis(
            coverage_analysis,
            audit_data.get(
                "coverage_analysis",
                {},
            ),
        )

        missing_test_cases = audit_data.get(
            "missing_test_cases",
            [],
        )

        if not isinstance(
            missing_test_cases,
            list,
        ):
            missing_test_cases = []

    # ==================================================
    # PASS 4 - Generate Missing Cases
    # ==================================================

    if missing_test_cases:

        # Generate missing obligations in bounded batches too. A single large
        # missing-coverage prompt can otherwise be truncated or return only a
        # small subset of the missing targets.
        for missing_start in range(
            0,
            len(missing_test_cases),
            AI_GENERATION_BATCH_SIZE,
        ):
            remaining_capacity = max(
                0,
                MAX_TEST_CASES - len(test_cases),
            )
            if remaining_capacity <= 0:
                assumptions.append(
                    f"Additional missing-coverage cases were not added because "
                    f"the {MAX_TEST_CASES}-case safety limit was reached."
                )
                break

            missing_batch = missing_test_cases[
                missing_start:
                missing_start + AI_GENERATION_BATCH_SIZE
            ]

            missing_response = await _generate_text_with_retry(
                _build_missing_case_prompt(
                    requirement=requirement,
                    source_context=source_context,
                    template_context=template_context,
                    existing_test_cases=test_cases,
                    missing_cases=missing_batch,
                    knowledge_context=knowledge_context_text,
                )
            )

            missing_data = _parse_json_object(
                missing_response
            )

            if missing_data is None:
                assumptions.append(
                    f"AI missing-coverage batch {missing_start // AI_GENERATION_BATCH_SIZE + 1} "
                    "returned invalid JSON."
                )
                continue

            additional_cases = _normalize_test_cases(
                missing_data.get("test_cases", []),
                columns,
                assumptions,
                start_index=len(test_cases) + 1,
            )

            test_cases.extend(additional_cases[:remaining_capacity])

            assumptions.extend(
                _normalize_string_list(
                    missing_data.get("assumptions", [])
                )
            )

            coverage_analysis = _merge_coverage_analysis(
                coverage_analysis,
                missing_data.get("coverage_analysis", {}),
            )

            if len(test_cases) >= MAX_TEST_CASES:
                assumptions.append(
                    f"Generation reached the technical safety limit of {MAX_TEST_CASES} test cases."
                )
                break

        _normalize_step_expected_results(
            test_cases,
            columns,
            assumptions,
        )

        await _repair_step_expected_results_with_ai(
            test_cases=test_cases,
            columns=columns,
            requirement=requirement,
            source_context=source_context,
            knowledge_context=knowledge_context_text,
            assumptions=assumptions,
        )

    # --------------------------------------------------
    # Final deterministic coverage refill
    # --------------------------------------------------
    # If an AI missing-case batch still returns fewer cases than requested,
    # perform up to two small deterministic refill rounds. This prevents a
    # short model response from leaving known coverage obligations uncovered.
    for refill_round in range(1, 3):
        if len(test_cases) >= MAX_TEST_CASES:
            break

        remaining_missing = _deterministic_missing_targets(
            coverage_targets,
            test_cases,
        )
        if not remaining_missing:
            break

        assumptions.append(
            f"Deterministic coverage refill round {refill_round} identified "
            f"{len(remaining_missing)} remaining target(s)."
        )

        for missing_start in range(
            0,
            len(remaining_missing),
            AI_GENERATION_BATCH_SIZE,
        ):
            remaining_capacity = max(
                0,
                MAX_TEST_CASES - len(test_cases),
            )
            if remaining_capacity <= 0:
                break

            refill_batch = remaining_missing[
                missing_start:
                missing_start + AI_GENERATION_BATCH_SIZE
            ]

            refill_response = await _generate_text_with_retry(
                _build_missing_case_prompt(
                    requirement=requirement,
                    source_context=source_context,
                    template_context=template_context,
                    existing_test_cases=test_cases,
                    missing_cases=refill_batch,
                    knowledge_context=knowledge_context_text,
                )
            )
            refill_data = _parse_json_object(refill_response)
            if not refill_data:
                assumptions.append(
                    f"Deterministic coverage refill batch {missing_start // AI_GENERATION_BATCH_SIZE + 1} "
                    "returned invalid JSON."
                )
                continue

            refill_cases = _normalize_test_cases(
                refill_data.get("test_cases", []),
                columns,
                assumptions,
                start_index=len(test_cases) + 1,
            )
            test_cases.extend(refill_cases[:remaining_capacity])
            assumptions.extend(
                _normalize_string_list(refill_data.get("assumptions", []))
            )

            coverage_analysis = _merge_coverage_analysis(
                coverage_analysis,
                refill_data.get("coverage_analysis", {}),
            )

        _normalize_step_expected_results(test_cases, columns, assumptions)
        await _repair_step_expected_results_with_ai(
            test_cases=test_cases,
            columns=columns,
            requirement=requirement,
            source_context=source_context,
            knowledge_context=knowledge_context_text,
            assumptions=assumptions,
        )

    # ==================================================
    # FINAL QA-FACING CLEANUP
    # ==================================================
    _normalize_generated_test_case_ids(
        test_cases=test_cases,
        columns=columns,
        assumptions=assumptions,
    )

    _filter_valid_qa_findings(
        coverage_analysis=coverage_analysis,
        requirement=requirement,
        source_context=source_context,
        assumptions=assumptions,
    )

    _clean_excess_qa_input_markers(
        test_cases=test_cases,
        columns=columns,
        assumptions=assumptions,
    )

    # ==================================================
    # FINAL EMPTY-STEP QUALITY GATE
    # ==================================================
    test_cases = _remove_cases_with_empty_steps(
        test_cases=test_cases,
        columns=columns,
        assumptions=assumptions,
    )

    # ==================================================
    # FINAL STEP / EXPECTED-RESULT ALIGNMENT
    # ==================================================
    # This runs after all generation/refill/repair passes, so the returned
    # test suite cannot contain 4 steps with only 1 expected result.
    _enforce_step_expected_result_alignment(
        test_cases=test_cases,
        columns=columns,
        assumptions=assumptions,
    )

    # ==================================================
    # PASS 5 - Final Validation
    # ==================================================

    sanitize_template_values(
        test_cases,
        template,
        assumptions,
    )

    # --------------------------------------------------
    # Deterministic requirement reconciliation
    # --------------------------------------------------

    _reconcile_coverage_with_generated_cases(
        coverage_analysis,
        test_cases,
        columns,
        assumptions,
    )

    # --------------------------------------------------
    # NEW:
    # Deterministic coverage-obligation reconciliation.
    #
    # This happens AFTER template normalization, so
    # coverage metadata can never leak into the user's
    # uploaded test-case columns.
    # --------------------------------------------------

    _reconcile_deterministic_coverage_targets(
        coverage_analysis=coverage_analysis,
        coverage_targets=coverage_targets,
        test_cases=test_cases,
        assumptions=assumptions,
    )

    _validate_traceability(
        coverage_analysis,
        test_cases,
        assumptions,
    )

    detect_duplicate_or_overlapping_scenarios(
        requirement,
        test_cases,
        coverage_analysis,
    )

    valid_qa_input_items = _build_valid_qa_input_items(
        coverage_targets=coverage_targets,
        coverage_analysis=coverage_analysis,
    )

    return {
        "test_cases": test_cases,
        # Internal processing/audit notes. These are intentionally separate
        # from the QA-input list shown in the UI.
        "assumptions": _unique_strings(
            assumptions
        ),
        # Only genuine requirement-impacting QA clarifications belong here.
        "qa_input_items": valid_qa_input_items,
        "coverage_analysis": coverage_analysis,
        "template": {
            "filename": template_filename,
            "format": template_format,
            "columns": columns,
        },
    }


# ==================================================
# Requirement-Bound Knowledge Context
# ==================================================

def _build_knowledge_context_text(
    knowledge_context: dict[str, Any],
    coverage_inventory: dict[str, Any],
    coverage_targets: dict[str, Any],
) -> str:

    applicable_knowledge = (
        knowledge_context.get(
            "applicable_knowledge",
            [],
        )
    )

    applicability = (
        knowledge_context.get(
            "applicability",
            {},
        )
    )

    safety_rules = (
        knowledge_context.get(
            "safety_rules",
            {},
        )
    )

    compact_knowledge = []

    if isinstance(
        applicable_knowledge,
        list,
    ):

        for item in applicable_knowledge:

            if not isinstance(
                item,
                dict,
            ):
                continue

            compact_knowledge.append(
                {
                    "knowledge_type": item.get(
                        "knowledge_type",
                        "",
                    ),
                    "name": item.get(
                        "name",
                        "",
                    ),
                    "purpose": item.get(
                        "purpose",
                        "",
                    ),
                    "coverage_dimensions": item.get(
                        "coverage_dimensions",
                        [],
                    ),
                    "generation_rules": item.get(
                        "generation_rules",
                        {},
                    ),
                    "strict_applicability_rules": item.get(
                        "strict_applicability_rules",
                        {},
                    ),
                    "security_boundary_rules": item.get(
                        "security_boundary_rules",
                        {},
                    ),
                    "threshold_rules": item.get(
                        "threshold_rules",
                        {},
                    ),
                    "unknown_information": item.get(
                        "unknown_information",
                        {},
                    ),
                }
            )

    compact_context = {
        "applicability": applicability,
        "applicable_knowledge": compact_knowledge,
        "coverage_inventory": coverage_inventory,
        "coverage_targets": coverage_targets,
        "safety_rules": safety_rules,
    }

    return json.dumps(
        compact_context,
        ensure_ascii=False,
        indent=2,
    )


# ==================================================
# Template Helpers
# ==================================================

def _normalize_template_columns(
    template: dict[str, Any],
) -> list[str]:

    columns = template.get(
        "columns",
        [],
    )

    if not isinstance(
        columns,
        list,
    ):
        return []

    return [
        str(column)
        for column in columns
        if column is not None
        and str(column).strip()
    ]


def _build_template_context(
    template: dict[str, Any],
    columns: list[str],
    filename: str,
    template_format: str,
) -> str:

    columns_json = json.dumps(
        columns,
        ensure_ascii=False,
        indent=2,
    )

    field_definitions = template.get(
        "field_definitions",
        {},
    )

    definitions_json = json.dumps(
        field_definitions,
        ensure_ascii=False,
        indent=2,
    )

    priority_columns = [
        column
        for column in columns
        if (
            str(column).strip().casefold() == "priority"
            or str(column).strip().casefold().endswith(" priority")
        )
    ]

    priority_note = (
        "The template contains a Priority field. Generate a "
        "Priority value for every test case. If explicit allowed "
        "values exist, use them exactly; otherwise classify from "
        "requirement evidence and use 'Requires QA Input' only "
        "when the evidence is insufficient."
        if priority_columns
        else
        "The template does not contain a Priority field; do not add one."
    )

    return f"""
TEMPLATE FILE
{filename}

TEMPLATE FORMAT
{template_format}

EXACT TEMPLATE COLUMNS
{columns_json}

TEMPLATE FIELD DEFINITIONS / VALIDATION METADATA
{definitions_json}

PRIORITY HANDLING
{priority_note}
"""


# ==================================================
# Coverage Planning Prompt
# ==================================================

def _build_coverage_plan_prompt(
    requirement: str,
    source_context: str,
    template_context: str,
    knowledge_context: str,
) -> str:

    return f"""
You are the requirement-decomposition stage of a professional
software QA test-case generation agent.

Create a COMPLETE coverage inventory.

You are NOT generating final test cases yet.

==================================================
AUTHORITY
==================================================

The ORIGINAL REQUIREMENT is the primary authority.

The QA knowledge library is testing guidance only.

Knowledge MAY:

- identify testing dimensions
- identify testing techniques
- identify useful boundaries
- identify security dimensions
- identify state transitions
- identify API/integration dimensions
- identify missing information

Knowledge MUST NOT:

- create a new requirement
- create an unsupported business rule
- create an unsupported security policy
- create an unsupported performance target
- create an unsupported API contract
- create an unsupported browser/device requirement
- create an unsupported authentication method

Every planned scenario must trace to the supplied
requirement.

==================================================
DETERMINISTIC COVERAGE TARGETS
==================================================

The deterministic coverage inventory is authoritative.

Every distinct coverage target must be considered.

Do not remove targets merely to reduce test count.

==================================================
NO ARBITRARY TEST COUNT
==================================================

There is NO fixed test-case count.

Do not target 3, 5, 10, 20, or any other arbitrary number.

Generate coverage based on actual requirement behavior.

==================================================
TRACEABILITY
==================================================

Every planned scenario should include:

- requirement IDs
- coverage obligation ID

The coverage obligation ID must come from the supplied
deterministic coverage target list.

==================================================
NO INVENTION
==================================================

Never invent:

- business requirements
- business rules
- roles
- permissions
- security policies
- performance thresholds
- latency values
- throughput values
- error messages
- API contracts
- browser requirements
- device requirements

Missing information must be reported as a gap.

==================================================
AMBIGUOUS REQUIREMENTS
==================================================

When information is missing or unclear, put the finding in
"ambiguous_requirements" as a direct QA question.

Example:
"Should the system allow multiple active sessions for the same user?"

Do not create ambiguity just to increase the count. Report only
real questions supported by the supplied requirement.

==================================================
STEP / EXPECTED-RESULT PAIRING
==================================================

For every test case that contains both a Test Steps column and
an Expected Result column, build the test as step-level pairs.

- Each meaningful test step MUST have its own corresponding
  expected result. The expected-result list should contain the same
  number of items as the test-step list.
- Keep the order aligned: step 1 -> expected result 1, step 2 ->
  expected result 2, and so on.
- If the evidence does not support the outcome for a particular
  step, use "Requires QA Input" for that expected-result item
  rather than dropping it or combining it with another result.
- Put multiple steps in the single uploaded Test Steps column as
  newline-separated items.
- Put multiple expected results in the single uploaded Expected
  Result column as newline-separated items.
- If the source evidence explicitly uses pipe-separated values
  (for example: `result 1 | result 2 | result 3`), preserve each
  value as a separate expected-result item rather than combining
  them into one sentence.
- Do NOT collapse several step outcomes into one long Expected
  Result paragraph merely because the uploaded template has one
  Expected Result column.
- Do NOT invent an expected outcome when the requirement does not
  support one. In that situation use `Requires QA Input` or an
  empty value as appropriate.
- Do not add a new template column for step-level expected results.

==================================================
STEP / EXPECTED-RESULT PAIRING — MANDATORY OUTPUT CONTRACT
==================================================

This is a mandatory generation rule, not an optional formatting
preference. When the uploaded template contains BOTH a Test Steps
column and an Expected Result column, generate the steps and
expected results as an explicit one-to-one list.

For EVERY meaningful test step, write ONE corresponding expected
result on the same ordinal position. The number of expected-result
items MUST normally equal the number of test-step items.

Use newline-separated items inside the single template cell. Do NOT
put several outcomes into one paragraph. Do NOT summarize all steps
with one final expected result.

Example of REQUIRED structure:

Test Steps:
Open the login page.
Verify the company logo.
Verify the company tagline.
Verify the background and decorative elements.
Verify the login card and controls.

Expected Result:
The login page loads successfully.
The company logo is displayed as shown by the supplied evidence.
The company tagline is displayed as shown by the supplied evidence.
The background and decorative elements are displayed as shown by the supplied evidence.
The login card and required controls are displayed as shown by the supplied evidence.

The first expected-result line corresponds ONLY to the first step,
the second to the second, and so on.

If a step has no observable outcome supported by the supplied
requirement/evidence, still preserve the step and put:
"Requires QA Input"
for that expected-result item. Never silently omit the item.

If a single action contains multiple distinct observable checks,
split that action into multiple meaningful steps so each observable
check can have its own expected result.

If the source explicitly supplies multiple expected results, preserve
them as separate newline-separated items and align them to the
corresponding steps.

The uploaded template still remains the source of truth: do not add
a new column for expected results.

==================================================
STEP / EXPECTED-RESULT PAIRING
==================================================

For every generated case that contains both a Test Steps column
and an Expected Result column, keep step-level expected results
aligned.

- One meaningful step should have one corresponding expected
  result whenever the supplied evidence supports that outcome.
- Store multiple steps as newline-separated items in the single
  uploaded Test Steps column.
- Store multiple expected results as newline-separated items in
  the single uploaded Expected Result column.
- Preserve explicit pipe-separated source values as separate
  expected-result items instead of collapsing them into one
  paragraph.
- Never invent unsupported expected outcomes.

==================================================
OUTPUT
==================================================

Return ONLY valid JSON.

{{
  "requirements": [
    {{
      "requirement_id": "REQ-001",
      "description": "",
      "source_reference": "",
      "testable": true
    }}
  ],

  "coverage_dimensions": [],

  "planned_test_scenarios": [
    {{
      "scenario_id": "PLAN-001",
      "requirement_ids": [
        "REQ-001"
      ],
      "dimension": "Functional",
      "description": "",
      "coverage_obligation_id": "OBL-001"
    }}
  ],

  "ambiguous_requirements": [
    "A direct question that QA needs answered before this behavior can be fully validated?"
  ],

  "missing_acceptance_criteria": [],

  "missing_performance_criteria": [],

  "missing_security_criteria": [],

  "duplicate_or_overlapping_scenarios": [],

  "assumptions": []
}}

==================================================
TEMPLATE CONTEXT
==================================================

{template_context}

==================================================
REQUIREMENT-BOUND KNOWLEDGE AND COVERAGE CONTEXT
==================================================

{knowledge_context}

==================================================
ADDITIONAL SOURCE / DESIGN EVIDENCE
==================================================

{source_context}

SOURCE AUTHORITY RULES

The PRIMARY REQUIREMENT is authoritative for written business
behavior.

The ADDITIONAL SOURCE / DESIGN EVIDENCE is authoritative only
for facts actually present in the supplied sources.

When a UI/UX screenshot or design image is supplied, treat it
as first-class project evidence.

For screenshot-derived coverage:
- Generate actual UI/visual test cases, not only a missing-
  coverage finding.
- Test only visibly observable design elements.
- Observable evidence may include visible branding, text,
  icons, controls, colors, layout, positioning, spacing,
  borders, backgrounds, illustrations, and alignment.
- Do not infer hidden DOM attributes, ARIA attributes, backend
  behavior, session persistence, API contracts, validation
  timing, security policies, performance targets, or other
  invisible behavior from a screenshot.
- Do not invent exact pixel measurements unless the source
  explicitly provides them.
- Do not use generic QA knowledge to create a visual requirement.
- Record the supplied filename as source_reference when known.
- Use UI-XXX requirement IDs for screenshot/design-derived
  requirements instead of falsely mapping them to REQ-XXX.
- If a test case is derived from design evidence and the
  template has a Requirement ID field, use the corresponding
  UI-XXX source requirement ID.
- If the template has a source/reference field, populate it
  with the supplied filename; otherwise retain source
  traceability in coverage_analysis.

==================================================
REQUIREMENT
==================================================

{requirement}
"""


# ==================================================
# Initial Generation Prompt
# ==================================================

def _build_generation_prompt(
    requirement: str,
    source_context: str,
    template_context: str,
    planned_requirements: list[dict[str, Any]],
    planned_dimensions: list[str],
    planned_scenarios: list,
    knowledge_context: str,
) -> str:

    return f"""
You are the primary software QA test-case generation stage.

Generate the COMPLETE set of test cases supported by the
supplied requirement and deterministic coverage targets.

==================================================
AUTHORITY
==================================================

The ORIGINAL REQUIREMENT is the authority.

The QA knowledge library is guidance only.

Knowledge MUST NOT introduce unsupported requirements.

Every test case MUST trace to:

1. An explicit requirement statement, OR
2. An explicit acceptance criterion, OR
3. An explicit business rule, OR
4. An explicit behavior/constraint, OR
5. A deterministic coverage obligation extracted from
   one of the above.

==================================================
DETERMINISTIC COVERAGE
==================================================

Use the supplied deterministic coverage targets as the
primary coverage checklist.

IMPORTANT: The scenario objects supplied in PLANNED TEST SCENARIOS
are the generation checklist for THIS BATCH. Generate at least one
materially distinct test case for EACH scenario object in this batch.
Do not silently omit a scenario because another scenario appears
similar. If two targets genuinely require the same execution flow,
keep separate test cases and preserve their distinct
coverage_obligation_id values.

IMPORTANT: A short AI planning response does NOT define the total
number of cases. The deterministic target checklist does.

Each generated test case should contain an INTERNAL:

"coverage_obligation_id"

value.

This field is INTERNAL METADATA ONLY.

It must NOT replace or alter any uploaded template column.

The final application will remove this metadata before
returning the test cases.

==================================================
NO ARBITRARY LIMIT
==================================================

There is NO test-case count limit.

Do NOT generate only 3-5 representative cases.

Do NOT stop early.

Generate every materially distinct supported behavior.

==================================================
TEMPLATE
==================================================

The uploaded template is the source of truth.

Every test case must contain exactly the uploaded
template columns.

Do not add or rename template columns.

==================================================
UNKNOWN VALUES
==================================================

Use:

"Requires QA Input"

or an empty string

when the requirement does not provide enough information.

PRIORITY:

If the uploaded template contains a Priority field, EVERY
generated test case must have a Priority value.

Priority is QA classification metadata; it does not create
a new business requirement.

- If the template provides explicit allowed Priority values,
  use ONLY those exact values.
- If the template does not provide allowed Priority values,
  assign a practical priority from the requirement evidence.
- Use "Critical" only when the requirement clearly indicates
  a failure would block a core business flow, cause data loss,
  severe security impact, or another explicitly critical impact.
- Use "High" when the requirement describes important/core
  functionality or a failure would materially block the
  intended workflow.
- Use "Medium" for normal supported functionality where failure
  does not clearly indicate critical or high business impact.
- Use "Low" only for lower-impact behavior explicitly supported
  by the requirement.
- If the requirement does not contain enough evidence to make
  a responsible priority classification, use "Requires QA Input".

Do NOT invent a business requirement merely to justify a priority.

Never invent:

- severity values
- error messages
- status codes
- endpoints
- HTTP methods
- retry counts
- timeout values
- performance thresholds
- security policies
- roles
- permissions

==================================================
TRACEABILITY
==================================================

Use REQ-XXX IDs for written-requirement-derived cases.

Use UI-XXX IDs for cases derived from supplied UI/UX design
or screenshot evidence.

Use API-XXX only when an uploaded API specification explicitly
establishes the API behavior.

Do not use a written REQ-XXX ID for a screenshot-only visual
requirement unless the written requirement explicitly states
the same behavior.

Use deterministic coverage obligation IDs.

Preserve an INTERNAL "source_reference" when the evidence
source is known. This is internal metadata and is not part of
the user's template unless the template already has a matching
source/reference column.

==================================================
AMBIGUOUS REQUIREMENTS
==================================================

When information is missing or unclear, put the finding in
"ambiguous_requirements" as a direct QA question.

Example:
"Should the system allow multiple active sessions for the same user?"

Do not create ambiguity just to increase the count. Report only
real questions supported by the supplied requirement.

==================================================
VISUAL / UI TEST GENERATION

If ADDITIONAL SOURCE / DESIGN EVIDENCE contains a screenshot
or UI/UX design, generate concrete visual/UI test cases for
distinct observable design elements that materially matter.

For example, if a supplied login screenshot visibly shows a
logo, tagline, gradient background, decorative line-art,
input icons, password visibility icon, field-border treatment,
centered card, overlapping illustrations, or a specific button
treatment, generate test cases that verify those observable
elements.

Do not merely report these elements as missing.

Do not generate a screenshot-derived case for something that
is not actually observable.

==================================================
OUTPUT
==================================================

Return ONLY valid JSON.

{{
  "test_cases": [
    {{
      "EXACT TEMPLATE COLUMN": "value",
      "coverage_obligation_id": "OBL-001"
    }}
  ],

  "assumptions": [],

  "coverage_analysis": {{
    "requirements": [
      {{
        "requirement_id": "REQ-001",
        "description": "",
        "covered": true,
        "test_case_references": [
          "TC-001"
        ]
      }}
    ],

    "covered_requirements": [],

    "uncovered_requirements": [],

    "ambiguous_requirements": [],

    "missing_acceptance_criteria": [],

    "missing_performance_criteria": [],

    "missing_security_criteria": [],

    "duplicate_or_overlapping_scenarios": []
  }}
}}

==================================================
TEMPLATE CONTEXT
==================================================

{template_context}

==================================================
ADDITIONAL SOURCE / DESIGN EVIDENCE
==================================================

{source_context}

SOURCE AUTHORITY RULES

The PRIMARY REQUIREMENT is authoritative for written business
behavior.

The ADDITIONAL SOURCE / DESIGN EVIDENCE is authoritative only
for facts actually present in the supplied sources.

When a UI/UX screenshot or design image is supplied, treat it
as first-class project evidence.

For screenshot-derived coverage:
- Generate actual UI/visual test cases, not only a missing-
  coverage finding.
- Test only visibly observable design elements.
- Observable evidence may include visible branding, text,
  icons, controls, colors, layout, positioning, spacing,
  borders, backgrounds, illustrations, and alignment.
- Do not infer hidden DOM attributes, ARIA attributes, backend
  behavior, session persistence, API contracts, validation
  timing, security policies, performance targets, or other
  invisible behavior from a screenshot.
- Do not invent exact pixel measurements unless the source
  explicitly provides them.
- Do not use generic QA knowledge to create a visual requirement.
- Record the supplied filename as source_reference when known.
- Use UI-XXX requirement IDs for screenshot/design-derived
  requirements instead of falsely mapping them to REQ-XXX.
- If a test case is derived from design evidence and the
  template has a Requirement ID field, use the corresponding
  UI-XXX source requirement ID.
- If the template has a source/reference field, populate it
  with the supplied filename; otherwise retain source
  traceability in coverage_analysis.

==================================================
ORIGINAL REQUIREMENT
==================================================

{requirement}

==================================================
REQUIREMENT-BOUND KNOWLEDGE AND COVERAGE CONTEXT
==================================================

{knowledge_context}

==================================================
REQUIREMENT INVENTORY
==================================================

{json.dumps(
    planned_requirements,
    ensure_ascii=False,
    indent=2,
)}

==================================================
COVERAGE DIMENSIONS
==================================================

{json.dumps(
    planned_dimensions,
    ensure_ascii=False,
    indent=2,
)}

==================================================
PLANNED TEST SCENARIOS
==================================================

{json.dumps(
    planned_scenarios,
    ensure_ascii=False,
    indent=2,
)}
"""


# ==================================================
# Coverage Audit Prompt
# ==================================================

def _build_coverage_audit_prompt(
    requirement: str,
    source_context: str,
    template_context: str,
    planned_requirements: list[dict[str, Any]],
    planned_scenarios: list,
    test_cases: list[dict[str, Any]],
    knowledge_context: str,
) -> str:

    return f"""
You are the final coverage-audit stage of a professional
software QA test-case generation agent.

Your responsibility is to identify MISSING test cases.

Compare:

1. Original requirement
2. Deterministic coverage inventory
3. Deterministic coverage targets
4. Requirement inventory
5. Planned scenarios
6. Generated test cases

==================================================
AUTHORITY
==================================================

The ORIGINAL REQUIREMENT is the authority.

The QA knowledge library is guidance only.

Do not mark a knowledge-only behavior as required unless
the requirement supports it.

==================================================
DETERMINISTIC COVERAGE
==================================================

Check EVERY deterministic coverage target.

If a target represents a distinct supported behavior and
no generated case covers it, report it.

Every missing case must include the relevant:

"coverage_obligation_id"

==================================================
NO REPRESENTATIVE COVERAGE
==================================================

Do not accept representative coverage when a distinct
supported behavior is missing.

Do not use an arbitrary case count.

==================================================
NO INVENTION
==================================================

Never invent:

- business rules
- security policies
- performance thresholds
- API contracts
- error messages
- browser/device requirements
- retry policies
- timeout values

Missing information must be reported.

==================================================
AMBIGUOUS REQUIREMENTS
==================================================

When information is missing or unclear, put the finding in
"ambiguous_requirements" as a direct QA question.

Example:
"Should the system allow multiple active sessions for the same user?"

Do not create ambiguity just to increase the count. Report only
real questions supported by the supplied requirement.

==================================================
OUTPUT
==================================================

Return ONLY valid JSON.

{{
  "missing_test_cases": [
    {{
      "requirement_ids": [
        "REQ-001"
      ],
      "dimension": "Validation",
      "description": "",
      "reason": "",
      "coverage_obligation_id": "OBL-001"
    }}
  ],

  "assumptions": [],

  "coverage_analysis": {{
    "requirements": [],
    "covered_requirements": [],
    "uncovered_requirements": [],
    "ambiguous_requirements": [],
    "missing_acceptance_criteria": [],
    "missing_performance_criteria": [],
    "missing_security_criteria": [],
    "duplicate_or_overlapping_scenarios": []
  }}
}}

==================================================
TEMPLATE CONTEXT
==================================================

{template_context}

==================================================
ADDITIONAL SOURCE / DESIGN EVIDENCE
==================================================

{source_context}

SOURCE AUTHORITY RULES

The PRIMARY REQUIREMENT is authoritative for written business
behavior.

The ADDITIONAL SOURCE / DESIGN EVIDENCE is authoritative only
for facts actually present in the supplied sources.

When a UI/UX screenshot or design image is supplied, treat it
as first-class project evidence.

For screenshot-derived coverage:
- Generate actual UI/visual test cases, not only a missing-
  coverage finding.
- Test only visibly observable design elements.
- Observable evidence may include visible branding, text,
  icons, controls, colors, layout, positioning, spacing,
  borders, backgrounds, illustrations, and alignment.
- Do not infer hidden DOM attributes, ARIA attributes, backend
  behavior, session persistence, API contracts, validation
  timing, security policies, performance targets, or other
  invisible behavior from a screenshot.
- Do not invent exact pixel measurements unless the source
  explicitly provides them.
- Do not use generic QA knowledge to create a visual requirement.
- Record the supplied filename as source_reference when known.
- Use UI-XXX requirement IDs for screenshot/design-derived
  requirements instead of falsely mapping them to REQ-XXX.
- If a test case is derived from design evidence and the
  template has a Requirement ID field, use the corresponding
  UI-XXX source requirement ID.
- If the template has a source/reference field, populate it
  with the supplied filename; otherwise retain source
  traceability in coverage_analysis.

==================================================
ORIGINAL REQUIREMENT
==================================================

{requirement}

==================================================
REQUIREMENT-BOUND KNOWLEDGE AND COVERAGE CONTEXT
==================================================

{knowledge_context}

==================================================
REQUIREMENT INVENTORY
==================================================

{json.dumps(
    planned_requirements,
    ensure_ascii=False,
    indent=2,
)}

==================================================
PLANNED SCENARIOS
==================================================

{json.dumps(
    planned_scenarios,
    ensure_ascii=False,
    indent=2,
)}

==================================================
GENERATED TEST CASES
==================================================

{json.dumps(
    test_cases,
    ensure_ascii=False,
    indent=2,
)}
"""


# ==================================================
# Missing Case Prompt
# ==================================================

def _build_missing_case_prompt(
    requirement: str,
    source_context: str,
    template_context: str,
    existing_test_cases: list[dict[str, Any]],
    missing_cases: list,
    knowledge_context: str,
) -> str:

    return f"""
You are the missing-coverage test-case generation stage.

Generate ONLY the cases identified as missing.

==================================================
AUTHORITY
==================================================

The ORIGINAL REQUIREMENT is the authority.

The QA knowledge library is guidance only.

Do not introduce unsupported requirements.

==================================================
COVERAGE
==================================================

Each missing case has a deterministic:

coverage_obligation_id

Preserve that ID in the generated case as internal
metadata.

==================================================
NO DUPLICATES
==================================================

Do not regenerate existing cases.

Do not merge distinct missing behaviors.

==================================================
TEMPLATE
==================================================

Use exactly the uploaded template columns.

Do not add or rename columns.

If the uploaded template contains a Priority field, provide
a Priority value for every generated missing-coverage case.

- Use the template's exact allowed Priority value when an
  allowed-value list exists.
- Otherwise classify priority only from evidence in the
  supplied requirement.
- If there is not enough evidence, use "Requires QA Input".
- Priority is metadata and must not introduce a new requirement.

==================================================
UNKNOWN VALUES
==================================================

Use:

"Requires QA Input"

or an empty string.

Do not invent missing policies.

==================================================
AMBIGUOUS REQUIREMENTS
==================================================

When information is missing or unclear, put the finding in
"ambiguous_requirements" as a direct QA question.

Example:
"Should the system allow multiple active sessions for the same user?"

Do not create ambiguity just to increase the count. Report only
real questions supported by the supplied requirement.

==================================================
OUTPUT
==================================================

Return ONLY valid JSON.

{{
  "test_cases": [
    {{
      "EXACT TEMPLATE COLUMN": "value",
      "coverage_obligation_id": "OBL-001"
    }}
  ],

  "assumptions": [],

  "coverage_analysis": {{
    "requirements": [],
    "covered_requirements": [],
    "uncovered_requirements": [],
    "ambiguous_requirements": [],
    "missing_acceptance_criteria": [],
    "missing_performance_criteria": [],
    "missing_security_criteria": [],
    "duplicate_or_overlapping_scenarios": []
  }}
}}

==================================================
TEMPLATE
==================================================

{template_context}

==================================================
ADDITIONAL SOURCE / DESIGN EVIDENCE
==================================================

{source_context}

SOURCE AUTHORITY RULES

The PRIMARY REQUIREMENT is authoritative for written business
behavior.

The ADDITIONAL SOURCE / DESIGN EVIDENCE is authoritative only
for facts actually present in the supplied sources.

When a UI/UX screenshot or design image is supplied, treat it
as first-class project evidence.

For screenshot-derived coverage:
- Generate actual UI/visual test cases, not only a missing-
  coverage finding.
- Test only visibly observable design elements.
- Observable evidence may include visible branding, text,
  icons, controls, colors, layout, positioning, spacing,
  borders, backgrounds, illustrations, and alignment.
- Do not infer hidden DOM attributes, ARIA attributes, backend
  behavior, session persistence, API contracts, validation
  timing, security policies, performance targets, or other
  invisible behavior from a screenshot.
- Do not invent exact pixel measurements unless the source
  explicitly provides them.
- Do not use generic QA knowledge to create a visual requirement.
- Record the supplied filename as source_reference when known.
- Use UI-XXX requirement IDs for screenshot/design-derived
  requirements instead of falsely mapping them to REQ-XXX.
- If a test case is derived from design evidence and the
  template has a Requirement ID field, use the corresponding
  UI-XXX source requirement ID.
- If the template has a source/reference field, populate it
  with the supplied filename; otherwise retain source
  traceability in coverage_analysis.

==================================================
ORIGINAL REQUIREMENT
==================================================

{requirement}

==================================================
REQUIREMENT-BOUND KNOWLEDGE AND COVERAGE CONTEXT
==================================================

{knowledge_context}

==================================================
EXISTING TEST CASES
==================================================

{json.dumps(
    existing_test_cases,
    ensure_ascii=False,
    indent=2,
)}

==================================================
MISSING COVERAGE
==================================================

{json.dumps(
    missing_cases,
    ensure_ascii=False,
    indent=2,
)}
"""


# ==================================================
# JSON Helpers
# ==================================================

def clean_ai_response(
    response: str,
) -> str:

    if not isinstance(
        response,
        str,
    ):
        return ""

    cleaned = response.strip()

    if cleaned.startswith(
        "```json"
    ):

        cleaned = (
            cleaned[
                len("```json"):
            ]
            .strip()
        )

    elif cleaned.startswith(
        "```"
    ):

        cleaned = (
            cleaned[
                len("```"):
            ]
            .strip()
        )

    if cleaned.endswith(
        "```"
    ):

        cleaned = (
            cleaned[
                :-len("```")
            ]
            .strip()
        )

    return cleaned


def _parse_json_object(
    response: str,
) -> dict[str, Any] | None:

    cleaned = clean_ai_response(
        response
    )

    if not cleaned:
        return None

    try:

        data = json.loads(
            cleaned
        )

    except (
        json.JSONDecodeError,
        TypeError,
    ):

        return None

    if not isinstance(
        data,
        dict,
    ):

        return None

    return data


# ==================================================
# Normalization Helpers
# ==================================================

def _normalize_string_list(
    value: Any,
) -> list[str]:

    if not isinstance(
        value,
        list,
    ):

        return []

    return [
        str(item).strip()
        for item in value
        if item is not None
        and str(item).strip()
    ]


def _normalize_ambiguity_questions(
    value: Any,
) -> list[str]:
    """
    Normalize AI ambiguity findings into user-facing QA
    questions.

    The model may return either:
      - a plain string
      - {"question": "..."}
      - {"description": "..."}
      - {"issue": "...", "question": "..."}
    The UI should always receive readable questions.
    """

    if not isinstance(
        value,
        list,
    ):
        return []

    questions = []

    for item in value:
        question = ""

        if isinstance(
            item,
            dict,
        ):
            question = str(
                item.get(
                    "question",
                    "",
                )
            ).strip()

            if not question:
                question = str(
                    item.get(
                        "description",
                        "",
                    )
                ).strip()

            if not question:
                question = str(
                    item.get(
                        "issue",
                        "",
                    )
                ).strip()

        elif item is not None:
            question = str(
                item
            ).strip()

        if not question:
            continue

        # Keep the findings useful to QA even when the model
        # returned a statement instead of a question.
        if not question.endswith("?"):
            question = question.rstrip(". ") + "?"

        questions.append(
            question
        )

    return _unique_strings(
        questions
    )


def _unique_strings(
    values: list[str],
) -> list[str]:

    result = []
    seen = set()

    for value in values:

        text = str(
            value
        ).strip()

        if not text:
            continue

        key = text.casefold()

        if key in seen:
            continue

        seen.add(
            key
        )

        result.append(
            text
        )

    return result


def _normalize_requirement_inventory(
    requirements: Any,
) -> list[dict[str, Any]]:

    if not isinstance(
        requirements,
        list,
    ):

        return []

    normalized = []

    for index, item in enumerate(
        requirements,
        start=1,
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        requirement_id = str(
            item.get(
                "requirement_id"
            )
            or f"REQ-{index:03d}"
        ).strip()

        description = str(
            item.get(
                "description",
                "",
            )
        ).strip()

        if not description:
            continue

        normalized.append(
            {
                "requirement_id": requirement_id,
                "description": description,
                "source_reference": str(
                    item.get(
                        "source_reference",
                        "",
                    )
                ).strip(),
                "testable": bool(
                    item.get(
                        "testable",
                        True,
                    )
                ),
            }
        )

    return normalized


def _split_step_expected_items(value: Any) -> list[str]:
    """Split explicit multi-item step/result values without inventing content.

    Accept both template-cell strings and JSON arrays returned by the
    step/result repair stage.
    """
    if value is None:
        return []

    # Repair responses can legitimately return:
    # ["result for step 1", "result for step 2", ...]
    # Preserve each array item as a separate result.
    if isinstance(value, (list, tuple)):
        cleaned = []
        for part in value:
            if part is None:
                continue
            item = str(part).replace("\r\n", "\n").replace("\r", "\n").strip()
            if not item:
                continue
            item = re.sub(r"^\s*(?:\d+|[A-Za-z])\s*[.)\-:]\s*", "", item).strip()
            if item:
                cleaned.append(item)
        return cleaned

    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    # Pipe is an explicit delimiter used by the expected document/export.
    parts = re.split(r"\n|\s*\|\s*", text)

    cleaned = []
    for part in parts:
        item = str(part).strip()
        if not item:
            continue
        item = re.sub(r"^\s*(?:\d+|[A-Za-z])\s*[.)\-:]\s*", "", item).strip()
        if item:
            cleaned.append(item)

    return cleaned


async def _repair_step_expected_results_with_ai(
    test_cases: list[dict[str, Any]],
    columns: list[str],
    requirement: str,
    source_context: str,
    knowledge_context: str,
    assumptions: list[str],
) -> None:
    """Repair only step/expected-result cardinality mismatches.

    The repair stage never changes the test scenario, requirement reference,
    priority, or other template fields. It returns only an expected-result list
    aligned one-to-one with the existing steps.
    """
    step_column = next(
        (column for column in columns if str(column).strip().casefold() in {
            "test steps", "steps", "test step"
        }),
        None,
    )
    expected_column = next(
        (column for column in columns if str(column).strip().casefold() in {
            "expected result", "expected results", "expectedresult"
        }),
        None,
    )

    if not step_column or not expected_column:
        return

    repair_items = []
    for index, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict):
            continue
        steps = _split_step_expected_items(case.get(step_column, ""))
        expected = _split_step_expected_items(case.get(expected_column, ""))
        if len(steps) > 1 and len(steps) != len(expected):
            repair_items.append({
                "test_case_index": index,
                "steps": steps,
                "current_expected_results": expected,
            })

    if not repair_items:
        return

    assumptions.append(
        f"AI step/result repair is required for {len(repair_items)} test case(s)."
    )

    repair_batch_size = 20
    for batch_start in range(0, len(repair_items), repair_batch_size):
        batch = repair_items[batch_start:batch_start + repair_batch_size]
        prompt = f"""
You are a QA test-step expected-result repair stage.

Your ONLY job is to create one expected-result item for every existing
meaningful test step. Do not redesign the test case.

RULES:
- Preserve the existing step order exactly.
- Return exactly one expected result for each step.
- The expected result at position N corresponds only to step N.
- Use the supplied requirement and evidence as the authority.
- Never invent an unsupported business rule, validation rule, message,
  API behavior, security rule, performance value, role or permission.
- Do NOT use "Requires QA Input" merely because the exact wording of a
  routine step outcome is not explicitly written in the requirement.
- For ordinary observable actions, write the immediate observable result
  of the action when it is safely implied by the step itself. Examples:
  * Navigate/open a page -> the referenced page is displayed.
  * Enter text into a named field -> the entered value is present in that
    field (do not invent validation rules).
  * Select/check a control -> the control reflects the selected state.
  * Click a button -> the action is triggered; only claim a specific
    business outcome when the requirement/evidence supports it.
- Use "Requires QA Input" only for a genuinely material unknown that
  affects the expected behavior, such as an unspecified business rule,
  validation rule, permission, message, threshold, or policy.
- If the current expected result is one combined paragraph, split its
  supported meaning across the appropriate steps instead of repeating
  the whole paragraph.
- Do not add or remove steps.
- Do not return any other test-case fields.

Return ONLY valid JSON in this exact shape:
{{
  "repairs": [
    {{
      "test_case_index": 1,
      "expected_results": ["result for step 1", "result for step 2"]
    }}
  ]
}}

ORIGINAL REQUIREMENT:
{requirement}

ADDITIONAL SOURCE / DESIGN EVIDENCE:
{source_context}

REQUIREMENT-BOUND KNOWLEDGE:
{knowledge_context}

CASES TO REPAIR:
{json.dumps(batch, ensure_ascii=False, indent=2)}
"""

        response = await _generate_text_with_retry(prompt)
        data = _parse_json_object(response)
        if not data:
            assumptions.append(
                f"AI step/result repair batch {batch_start // repair_batch_size + 1} returned invalid JSON."
            )
            continue

        repairs = data.get("repairs", [])
        if not isinstance(repairs, list):
            continue

        for repair in repairs:
            if not isinstance(repair, dict):
                continue
            try:
                case_index = int(repair.get("test_case_index"))
            except (TypeError, ValueError):
                continue
            if case_index < 1 or case_index > len(test_cases):
                continue

            expected_results = _split_step_expected_items(
                repair.get("expected_results", [])
            )
            steps = _split_step_expected_items(
                test_cases[case_index - 1].get(step_column, "")
            )

            if len(expected_results) != len(steps):
                assumptions.append(
                    f"AI step/result repair for test case {case_index} did not return "
                    f"the required {len(steps)} expected-result item(s); values were preserved."
                )
                continue

            repaired_results = []
            for step, result in zip(steps, expected_results):
                if _normalize_value(result) in {
                    "requires qa input", "qa input required", "requires qa"
                }:
                    fallback = _safe_expected_result_for_step(step)
                    repaired_results.append(fallback if fallback else result)
                else:
                    repaired_results.append(result)

            test_cases[case_index - 1][expected_column] = "\n".join(repaired_results)


def _safe_expected_result_for_step(step: str) -> str:
    """Create a conservative observable outcome for a routine test step.

    This fallback is deliberately limited to outcomes implied by the action
    itself. It does not invent business rules, validation, permissions, APIs,
    messages, thresholds or security behavior.
    """
    value = str(step or "").strip()
    lower = value.casefold()

    if not value:
        return ""

    if any(token in lower for token in (
        "navigate to", "go to", "open ", "load ", "visit ",
        "access the ",
    )):
        subject = value
        for prefix in (
            "navigate to", "go to", "open ", "load ", "visit ",
            "access the ",
        ):
            if lower.startswith(prefix):
                subject = value[len(prefix):].strip().rstrip(".")
                break
        return f"{subject[:1].upper() + subject[1:] if subject else 'The requested page'} is displayed."

    if any(token in lower for token in (
        "enter ", "type ", "input ", "provide ", "fill ",
    )):
        return "The entered value is present in the specified field."

    if any(token in lower for token in (
        "select ", "choose ", "pick ",
    )):
        return "The specified option is selected."

    if any(token in lower for token in (
        "check ", "tick ", "enable ",
    )):
        return "The specified control is selected or enabled."

    if any(token in lower for token in (
        "uncheck ", "untick ", "disable ",
    )):
        return "The specified control is cleared or disabled."

    if any(token in lower for token in (
        "click ", "tap ", "press ",
    )):
        return "The specified control is activated."

    if any(token in lower for token in (
        "verify ", "validate ", "confirm ", "check that ",
        "ensure ",
    )):
        return "The stated condition is satisfied."

    # Do not invent an outcome for an unusual step.
    return ""


def _clean_excess_qa_input_markers(
    test_cases: list[dict[str, Any]],
    columns: list[str],
    assumptions: list[str],
) -> None:
    """Remove unjustified QA-input markers from routine generated fields.

    QA Input remains valid for genuine ambiguity, but it should not dominate
    the suite simply because the model was conservative.
    """
    priority_columns = {
        str(column).strip().casefold()
        for column in columns
        if "priority" in str(column).strip().casefold()
    }
    step_columns = {
        str(column).strip().casefold()
        for column in columns
        if str(column).strip().casefold() in {
            "test steps", "steps", "test step"
        }
    }
    expected_columns = {
        str(column).strip().casefold()
        for column in columns
        if str(column).strip().casefold() in {
            "expected result", "expected results", "expectedresult"
        }
    }

    for index, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict):
            continue

        for column in columns:
            key = str(column).strip().casefold()
            value = case.get(column, "")
            if not isinstance(value, str):
                continue

            if key in priority_columns and _is_requires_qa_input(value):
                case[column] = "Medium"
                assumptions.append(
                    f"Test case {index} had an unjustified QA-input marker "
                    f"in Priority '{column}'; replaced with 'Medium'."
                )

            elif key in step_columns and _is_requires_qa_input(value):
                # A test case with no executable step is not useful. Do not
                # claim an invented action; leave it empty for QA to edit.
                case[column] = ""
                assumptions.append(
                    f"Test case {index} had QA Input as its entire step "
                    f"field '{column}'; cleared the non-executable value."
                )

            elif key in expected_columns:
                items = _split_step_expected_items(value)
                if not items:
                    continue
                replaced = []
                steps = []
                for step_column in columns:
                    if str(step_column).strip().casefold() in step_columns:
                        steps = _split_step_expected_items(case.get(step_column, ""))
                        break
                for item_index, item in enumerate(items):
                    if _is_requires_qa_input(item) and item_index < len(steps):
                        fallback = _safe_expected_result_for_step(steps[item_index])
                        replaced.append(fallback if fallback else item)
                    else:
                        replaced.append(item)
                case[column] = "\n".join(replaced)


def _remove_cases_with_empty_steps(
    test_cases: list[dict[str, Any]],
    columns: list[str],
    assumptions: list[str],
) -> list[dict[str, Any]]:
    """Remove unusable cases that contain no executable test step.

    Do not invent an action merely to preserve the generated case count.
    """
    step_column = next(
        (
            column for column in columns
            if str(column).strip().casefold() in {
                "test steps", "steps", "test step"
            }
        ),
        None,
    )
    if not step_column:
        return test_cases

    valid_cases = []
    removed = 0
    for case in test_cases:
        if not isinstance(case, dict):
            continue

        steps = _split_step_expected_items(case.get(step_column, ""))
        executable = [
            step.strip()
            for step in steps
            if step.strip() and not _is_requires_qa_input(step.strip())
        ]

        if not executable:
            removed += 1
            continue

        case[step_column] = "\n".join(executable)
        valid_cases.append(case)

    if removed:
        assumptions.append(
            f"Final quality gate removed {removed} test case(s) with no "
            "executable Test Step. No action was invented to preserve them."
        )

    return valid_cases


def _enforce_step_expected_result_alignment(
    test_cases: list[dict[str, Any]],
    columns: list[str],
    assumptions: list[str],
) -> None:
    """Guarantee one Expected Result item for every meaningful Test Step.

    This is a safety net after AI repair. It never invents an outcome:
    unsupported/missing outcomes become "Requires QA Input".
    """
    step_column = next(
        (
            column for column in columns
            if str(column).strip().casefold() in {
                "test steps", "steps", "test step"
            }
        ),
        None,
    )
    expected_column = next(
        (
            column for column in columns
            if str(column).strip().casefold() in {
                "expected result", "expected results", "expectedresult"
            }
        ),
        None,
    )

    if not step_column or not expected_column:
        return

    for case_number, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict):
            continue

        steps = _split_step_expected_items(case.get(step_column, ""))
        expected = _split_step_expected_items(case.get(expected_column, ""))

        if not steps:
            continue

        if len(expected) == len(steps):
            case[step_column] = "\n".join(steps)
            case[expected_column] = "\n".join(expected)
            continue

        # Preserve explicit results. For missing positions, first use a
        # conservative observable fallback for routine actions. Leave an
        # unusual unsupported outcome blank instead of flooding the suite
        # with "Requires QA Input"; genuine ambiguity remains visible to QA.
        aligned = []
        for index, step in enumerate(steps):
            if index < len(expected) and expected[index].strip():
                existing = expected[index].strip()
                if _normalize_value(existing) in {
                    "requires qa input", "qa input required", "requires qa"
                }:
                    fallback = _safe_expected_result_for_step(step)
                    aligned.append(fallback if fallback else "")
                else:
                    aligned.append(existing)
            else:
                aligned.append(_safe_expected_result_for_step(step))

        case[step_column] = "\n".join(steps)
        case[expected_column] = "\n".join(aligned)

        assumptions.append(
            f"Final step/result validation aligned test case {case_number} "
            f"to {len(steps)} step/result item(s); routine missing outcomes "
            "were filled only when they were directly observable from the step."
        )


def _normalize_step_expected_results(
    test_cases: list[dict[str, Any]],
    columns: list[str],
    assumptions: list[str],
) -> None:
    """Normalize multi-step / multi-expected-result values to aligned newlines.

    The uploaded template remains the source of truth: this function does not
    add columns or invent missing outcomes. It only converts explicit list-like
    values (newlines or pipes) into the single template cell representation.
    """
    step_column = next(
        (column for column in columns if str(column).strip().casefold() in {
            "test steps", "steps", "test step"
        }),
        None,
    )
    expected_column = next(
        (column for column in columns if str(column).strip().casefold() in {
            "expected result", "expected results", "expectedresult"
        }),
        None,
    )

    if not step_column or not expected_column:
        return

    for case_number, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict):
            continue

        steps = _split_step_expected_items(case.get(step_column, ""))
        expected = _split_step_expected_items(case.get(expected_column, ""))

        if steps:
            case[step_column] = "\n".join(steps)
        elif case.get(step_column) is None:
            case[step_column] = ""

        if expected:
            case[expected_column] = "\n".join(expected)
        elif case.get(expected_column) is None:
            case[expected_column] = ""

        # Do not fabricate missing expected results. Flag a real structural
        # mismatch so QA can see that the source/generator did not provide a
        # one-to-one pairing.
        if steps and expected and len(steps) != len(expected):
            assumptions.append(
                f"Test case {case_number} has {len(steps)} test steps but "
                f"{len(expected)} expected-result items; values were preserved "
                "without inventing additional outcomes."
            )


def _normalize_test_cases(
    test_cases: Any,
    columns: list[str],
    assumptions: list[str],
    start_index: int = 1,
) -> list[dict[str, Any]]:

    if not isinstance(
        test_cases,
        list,
    ):

        return []

    normalized_cases = []

    for index, test_case in enumerate(
        test_cases,
        start=start_index,
    ):

        if not isinstance(
            test_case,
            dict,
        ):

            assumptions.append(
                f"Test case {index} was not returned as an object."
            )

            continue

        normalized_case = {}

        # ------------------------------------------
        # Preserve ONLY template columns.
        #
        # coverage_obligation_id is intentionally
        # excluded from the returned test case.
        # Deterministic coverage is recalculated
        # later from the normalized case.
        # ------------------------------------------

        for column in columns:

            value = test_case.get(
                column,
                "",
            )

            if value is None:
                value = ""

            normalized_case[
                column
            ] = value

        missing_fields = [
            column
            for column in columns
            if column not in test_case
        ]

        if missing_fields:

            assumptions.append(
                f"Test case {index} was missing template fields: "
                + ", ".join(
                    missing_fields
                )
            )

        extra_fields = [
            str(key)
            for key in test_case.keys()
            if key not in columns
            and key != "coverage_obligation_id"
        ]

        if extra_fields:

            assumptions.append(
                f"Test case {index} contained fields outside "
                "the uploaded template and they were removed: "
                + ", ".join(
                    extra_fields
                )
            )

        normalized_cases.append(
            normalized_case
        )

    return normalized_cases


# ==================================================
# Coverage Normalization
# ==================================================

def normalize_coverage_analysis(
    coverage_analysis: dict[str, Any],
) -> dict[str, Any]:

    if not isinstance(
        coverage_analysis,
        dict,
    ):

        coverage_analysis = {}

    normalized = {}

    for key, default in (
        DEFAULT_COVERAGE_ANALYSIS.items()
    ):

        value = coverage_analysis.get(
            key,
            default,
        )

        if key == "requirements":

            if not isinstance(
                value,
                list,
            ):

                value = []

        elif key == "ambiguous_requirements":

            value = _normalize_ambiguity_questions(
                value
            )

        elif key == "coverage_traceability":

            if not isinstance(
                value,
                list,
            ):

                value = []

        elif not isinstance(
            value,
            list,
        ):

            value = list(
                default
            )

        normalized[
            key
        ] = value

    return normalized


def _empty_coverage_analysis() -> dict[str, Any]:

    return {
        key: list(value)
        for key, value in (
            DEFAULT_COVERAGE_ANALYSIS.items()
        )
    }


# ==================================================
# Coverage Planning Merge
# ==================================================

def _merge_coverage_planning(
    coverage_analysis: dict[str, Any],
    planned_requirements: list[dict[str, Any]],
    coverage_plan: dict[str, Any],
) -> dict[str, Any]:

    normalized = normalize_coverage_analysis(
        coverage_analysis
    )

    existing_by_id = {}

    for item in normalized[
        "requirements"
    ]:

        if not isinstance(
            item,
            dict,
        ):

            continue

        requirement_id = item.get(
            "requirement_id"
        )

        if requirement_id:

            existing_by_id[
                str(requirement_id)
            ] = item

    merged = []

    for planned in planned_requirements:

        requirement_id = planned[
            "requirement_id"
        ]

        existing = existing_by_id.get(
            requirement_id,
            {},
        )

        merged.append(
            {
                "requirement_id": requirement_id,
                "description": (
                    planned[
                        "description"
                    ]
                    or str(
                        existing.get(
                            "description",
                            "",
                        )
                    )
                ),
                "covered": bool(
                    existing.get(
                        "covered",
                        False,
                    )
                ),
                "test_case_references": (
                    _normalize_string_list(
                        existing.get(
                            "test_case_references",
                            [],
                        )
                    )
                ),
            }
        )

    normalized[
        "requirements"
    ] = merged

    for key in (
        "ambiguous_requirements",
        "missing_acceptance_criteria",
        "missing_performance_criteria",
        "missing_security_criteria",
        "duplicate_or_overlapping_scenarios",
    ):

        if key == "ambiguous_requirements":
            planned_values = _normalize_ambiguity_questions(
                coverage_plan.get(
                    key,
                    [],
                )
            )
        else:
            planned_values = _normalize_string_list(
                coverage_plan.get(
                    key,
                    [],
                )
            )

        normalized[
            key
        ] = _unique_strings(
            normalized.get(
                key,
                [],
            )
            + planned_values
        )

    return normalized


def _merge_coverage_analysis(
    current: dict[str, Any],
    incoming: Any,
) -> dict[str, Any]:

    current = normalize_coverage_analysis(
        current
    )

    incoming = normalize_coverage_analysis(
        incoming
    )

    by_id = {}

    for item in current[
        "requirements"
    ]:

        if not isinstance(
            item,
            dict,
        ):

            continue

        requirement_id = item.get(
            "requirement_id"
        )

        if requirement_id:

            by_id[
                str(requirement_id)
            ] = item

    for item in incoming[
        "requirements"
    ]:

        if not isinstance(
            item,
            dict,
        ):

            continue

        requirement_id = item.get(
            "requirement_id"
        )

        if not requirement_id:
            continue

        rid = str(
            requirement_id
        )

        existing = by_id.get(
            rid,
            {},
        )

        existing_references = (
            _normalize_string_list(
                existing.get(
                    "test_case_references",
                    [],
                )
            )
        )

        incoming_references = (
            _normalize_string_list(
                item.get(
                    "test_case_references",
                    [],
                )
            )
        )

        by_id[
            rid
        ] = {
            "requirement_id": rid,
            "description": (
                str(
                    item.get(
                        "description",
                        "",
                    )
                ).strip()
                or str(
                    existing.get(
                        "description",
                        "",
                    )
                ).strip()
            ),
            "covered": bool(
                existing.get(
                    "covered",
                    False,
                )
                or item.get(
                    "covered",
                    False,
                )
            ),
            "test_case_references": _unique_strings(
                existing_references
                + incoming_references
            ),
        }

    current[
        "requirements"
    ] = list(
        by_id.values()
    )

    for key in current:

        if key in {
            "requirements",
            "coverage_traceability",
        }:

            continue

        if key == "ambiguous_requirements":
            current_values = _normalize_ambiguity_questions(
                current.get(
                    key,
                    [],
                )
            )
            incoming_values = _normalize_ambiguity_questions(
                incoming.get(
                    key,
                    [],
                )
            )
        else:
            current_values = _normalize_string_list(
                current.get(
                    key,
                    [],
                )
            )
            incoming_values = _normalize_string_list(
                incoming.get(
                    key,
                    [],
                )
            )

        current[
            key
        ] = _unique_strings(
            current_values
            + incoming_values
        )

    return current


# ==================================================
# Requirement Coverage Reconciliation
# ==================================================

def _reconcile_coverage_with_generated_cases(
    coverage_analysis: dict[str, Any],
    test_cases: list[dict[str, Any]],
    columns: list[str],
    assumptions: list[str],
) -> None:

    requirement_reference_columns = [
        column
        for column in columns
        if (
            "requirement" in column.casefold()
            or "story" in column.casefold()
            or column.casefold().strip() == "ref"
        )
    ]

    generated_test_case_ids = {
        f"TC-{index:03d}"
        for index in range(
            1,
            len(test_cases) + 1,
        )
    }

    covered_requirements = []
    uncovered_requirements = []

    for requirement_entry in coverage_analysis.get(
        "requirements",
        [],
    ):

        if not isinstance(
            requirement_entry,
            dict,
        ):

            continue

        requirement_id = str(
            requirement_entry.get(
                "requirement_id",
                "",
            )
        ).strip()

        if not requirement_id:
            continue

        references = [
            reference
            for reference in _normalize_string_list(
                requirement_entry.get(
                    "test_case_references",
                    [],
                )
            )
            if reference in generated_test_case_ids
        ]

        for index, test_case in enumerate(
            test_cases,
            start=1,
        ):

            for column in requirement_reference_columns:

                value = str(
                    test_case.get(
                        column,
                        "",
                    )
                ).strip()

                if (
                    value
                    and requirement_id.casefold()
                    in value.casefold()
                ):

                    references.append(
                        f"TC-{index:03d}"
                    )

                    break

        references = _unique_strings(
            references
        )

        # ------------------------------------------
        # Also infer requirement coverage from the
        # test scenario when no requirement-reference
        # column exists.
        # ------------------------------------------

        if not references:

            requirement_description = str(
                requirement_entry.get(
                    "description",
                    "",
                )
            ).strip()

            if requirement_description:

                description_tokens = (
                    _meaningful_tokens(
                        requirement_description
                    )
                )

                for index, test_case in enumerate(
                    test_cases,
                    start=1,
                ):

                    case_text = _test_case_text(
                        test_case
                    )

                    case_tokens = (
                        _meaningful_tokens(
                            case_text
                        )
                    )

                    overlap = (
                        _token_overlap_score(
                            description_tokens,
                            case_tokens,
                        )
                    )

                    if overlap >= 0.35:

                        references.append(
                            f"TC-{index:03d}"
                        )

        references = _unique_strings(
            references
        )

        requirement_entry[
            "test_case_references"
        ] = references

        requirement_entry[
            "covered"
        ] = bool(
            references
        )

        if references:

            covered_requirements.append(
                requirement_id
            )

        else:

            uncovered_requirements.append(
                requirement_id
            )

    coverage_analysis[
        "covered_requirements"
    ] = _unique_strings(
        covered_requirements
    )

    coverage_analysis[
        "uncovered_requirements"
    ] = _unique_strings(
        uncovered_requirements
    )

    if uncovered_requirements:

        assumptions.append(
            "The final generated set still has uncovered "
            "requirement items: "
            + ", ".join(
                uncovered_requirements
            )
        )


# ==================================================
# NEW:
# Deterministic Coverage-Target Reconciliation
# ==================================================

def _reconcile_deterministic_coverage_targets(
    coverage_analysis: dict[str, Any],
    coverage_targets: dict[str, Any],
    test_cases: list[dict[str, Any]],
    assumptions: list[str],
) -> None:
    """
    Deterministically map generated test cases to the
    requirement-bound coverage targets.

    This is deliberately independent of Gemini's
    coverage_analysis.

    The AI may suggest an obligation ID, but the final
    coverage result is calculated from the actual
    generated test-case content.

    Coverage-specific matching rules are evaluated before
    the generic lexical scorer. This prevents valid,
    requirement-bound test cases from being rejected merely
    because the target and test-case wording differ.

    The user's uploaded template remains untouched.

    Results are stored in:

        coverage_analysis["coverage_traceability"]
    """

    targets = coverage_targets.get(
        "targets",
        [],
    )

    if not isinstance(
        targets,
        list,
    ):
        targets = []

    traceability = []

    # ----------------------------------------------
    # Build target entries.
    #
    # Multiple targets may share an obligation ID,
    # e.g. OBL-006:
    #
    #   below minimum
    #   exactly minimum
    #   above minimum
    #
    # Therefore title is part of the target identity.
    # ----------------------------------------------

    normalized_targets = []

    for target in targets:

        if not isinstance(
            target,
            dict,
        ):
            continue

        obligation_id = str(
            target.get(
                "obligation_id",
                "",
            )
        ).strip()

        if not obligation_id:
            continue

        target_title = str(
            target.get(
                "title",
                "",
            )
        ).strip()

        normalized_targets.append(
            {
                "obligation_id": obligation_id,
                "title": target_title,
                "coverage_type": str(
                    target.get(
                        "coverage_type",
                        "",
                    )
                ).strip(),
                "obligation_type": str(
                    target.get(
                        "obligation_type",
                        "",
                    )
                ).strip(),
                "obligation_subject": str(
                    target.get(
                        "obligation_subject",
                        "",
                    )
                ).strip(),
                "source_requirement": str(
                    target.get(
                        "source_requirement",
                        "",
                    )
                ).strip(),
                "expected_behavior": str(
                    target.get(
                        "expected_behavior",
                        "",
                    )
                ).strip(),
                "status": str(
                    target.get(
                        "status",
                        "explicit",
                    )
                ).strip(),
            }
        )

    # ----------------------------------------------
    # Match each target to the strongest unused case.
    #
    # Coverage-specific matching is evaluated first.
    # Generic lexical scoring remains the fallback.
    #
    # This prevents one generic test case from being
    # counted against every target.
    # ----------------------------------------------

    available_case_indexes = set(
        range(
            len(test_cases)
        )
    )

    # QA-input targets are matched after explicit
    # targets because explicit behavior should get
    # first ownership of concrete cases.
    ordered_targets = sorted(
        normalized_targets,
        key=lambda item: (
            item["status"] == "requires_qa_input",
            item["obligation_id"],
            item["title"],
        ),
    )

    for target in ordered_targets:

        best_index = None
        best_score = 0.0

        for case_index in available_case_indexes:

            test_case = test_cases[
                case_index
            ]

            # --------------------------------------
            # First try a requirement-specific match.
            #
            # A strong specific match is intentionally
            # above the normal 0.45 threshold.
            # --------------------------------------

            specific_score = _coverage_specific_match_score(
                target,
                test_case,
            )

            if specific_score > best_score:

                best_score = specific_score
                best_index = case_index

            # --------------------------------------
            # Generic lexical fallback.
            # --------------------------------------

            lexical_score = _coverage_target_score(
                target,
                test_case,
            )

            if lexical_score > best_score:

                best_score = lexical_score
                best_index = case_index

        # ------------------------------------------
        # Conservative threshold.
        #
        # We do not claim coverage from a weak
        # generic match.
        # ------------------------------------------

        covered = (
            best_index is not None
            and best_score >= 0.45
        )

        if covered:

            test_case_reference = (
                f"TC-{best_index + 1:03d}"
            )

            available_case_indexes.remove(
                best_index
            )

        else:

            test_case_reference = None

        traceability.append(
            {
                "coverage_obligation_id": (
                    target[
                        "obligation_id"
                    ]
                ),
                "coverage_target": (
                    target[
                        "title"
                    ]
                ),
                "coverage_type": (
                    target[
                        "coverage_type"
                    ]
                ),
                "obligation_type": (
                    target[
                        "obligation_type"
                    ]
                ),
                "status": (
                    target[
                        "status"
                    ]
                ),
                "covered": covered,
                "test_case_reference": (
                    test_case_reference
                ),
                "match_score": round(
                    best_score,
                    3,
                ),
                "source_requirement": (
                    target[
                        "source_requirement"
                    ]
                ),
            }
        )

    coverage_analysis[
        "coverage_traceability"
    ] = traceability

    # ----------------------------------------------
    # Report deterministic uncovered targets.
    # ----------------------------------------------

    uncovered = [
        item
        for item in traceability
        if not item.get(
            "covered",
            False,
        )
    ]

    explicit_uncovered = [
        item
        for item in uncovered
        if item.get(
            "status"
        ) != "requires_qa_input"
    ]

    qa_input_uncovered = [
        item
        for item in uncovered
        if item.get(
            "status"
        ) == "requires_qa_input"
    ]

    if explicit_uncovered:

        assumptions.append(
            "Deterministic coverage validation found "
            f"{len(explicit_uncovered)} explicit coverage "
            "target(s) without a sufficiently strong "
            "generated test-case match."
        )

    if qa_input_uncovered:

        assumptions.append(
            f"{len(qa_input_uncovered)} coverage target(s) "
            "remain unresolved because they require QA input."
        )


def _coverage_specific_match_score(
    target: dict[str, Any],
    test_case: dict[str, Any],
) -> float:
    """
    Return a strong deterministic score for known,
    requirement-bound coverage behaviors.

    This runs before the generic lexical scorer.

    The purpose is not to invent coverage. It recognizes
    equivalent wording when the generated test case clearly
    tests the same explicit requirement behavior.
    """

    case_text = _test_case_text(
        test_case
    ).casefold()

    if not case_text:
        return 0.0

    title = str(
        target.get(
            "title",
            "",
        )
        or ""
    ).casefold()

    subject = str(
        target.get(
            "obligation_subject",
            "",
        )
        or ""
    ).casefold()

    source = str(
        target.get(
            "source_requirement",
            "",
        )
        or ""
    ).casefold()

    expected = str(
        target.get(
            "expected_behavior",
            "",
        )
        or ""
    ).casefold()

    coverage_type = str(
        target.get(
            "coverage_type",
            "",
        )
        or ""
    ).casefold()

    obligation_type = str(
        target.get(
            "obligation_type",
            "",
        )
        or ""
    ).casefold()

    target_text = (
        f"{title} "
        f"{subject} "
        f"{source} "
        f"{expected} "
        f"{coverage_type} "
        f"{obligation_type}"
    )

    normalized_case = re.sub(
        r"[^a-z0-9]+",
        " ",
        case_text,
    )

    normalized_target = re.sub(
        r"[^a-z0-9]+",
        " ",
        target_text,
    )

    # ----------------------------------------------
    # Helper for word/phrase detection.
    # ----------------------------------------------

    def has_any(
        text: str,
        values: tuple[str, ...],
    ) -> bool:
        return any(
            value in text
            for value in values
        )

    # ==================================================
    # Authentication input: Email credential
    # ==================================================

    target_email = "email" in normalized_target
    case_email = "email" in normalized_case

    credential_words = (
        "credential",
        "input",
        "login",
        "authentication",
        "authenticate",
    )

    target_credential = has_any(
        normalized_target,
        credential_words,
    )

    case_credential = has_any(
        normalized_case,
        credential_words,
    )

    if (
        target_email
        and target_credential
        and case_email
        and case_credential
    ):
        return 0.95

    # ==================================================
    # Authentication input: Password credential
    # ==================================================

    target_password = (
        "password" in normalized_target
    )

    case_password = (
        "password" in normalized_case
    )

    if (
        target_password
        and target_credential
        and case_password
        and case_credential
    ):
        return 0.95

    # ==================================================
    # Email whitespace handling
    # ==================================================

    whitespace_words = (
        "whitespace",
        "trim",
        "trimmed",
        "strip",
        "stripped",
        "leading",
        "trailing",
        "spaces",
    )

    target_whitespace = has_any(
        normalized_target,
        whitespace_words,
    )

    case_whitespace = has_any(
        normalized_case,
        whitespace_words,
    )

    if (
        target_email
        and target_whitespace
        and case_email
        and case_whitespace
    ):
        return 0.95

    # ==================================================
    # Email case-insensitive behavior
    # ==================================================

    if (
        target_email
        and (
            "case insensitive" in normalized_target
            or "case-insensitive" in title
            or "case handling" in normalized_target
        )
        and case_email
        and (
            "case insensitive" in normalized_case
            or "case-insensitive" in case_text
        )
    ):
        return 0.95

    # ==================================================
    # Password case-sensitive behavior
    # ==================================================

    if (
        target_password
        and (
            "case sensitive" in normalized_target
            or "case-sensitive" in title
            or "case handling" in normalized_target
        )
        and case_password
        and (
            "case sensitive" in normalized_case
            or "case-sensitive" in case_text
        )
    ):
        return 0.95

    # ==================================================
    # Password boundary behavior
    # ==================================================

    target_boundary = has_any(
        normalized_target,
        (
            "below minimum",
            "exactly minimum",
            "above minimum",
            "minimum",
        ),
    )

    case_boundary = has_any(
        normalized_case,
        (
            "below minimum",
            "exactly minimum",
            "above minimum",
            "minimum",
        ),
    )

    if (
        target_password
        and target_boundary
        and case_password
        and case_boundary
    ):

        # Match the specific boundary direction where
        # the requirement target explicitly identifies it.
        for phrase in (
            "below minimum",
            "exactly minimum",
            "above minimum",
        ):

            if (
                phrase in normalized_target
                and phrase in normalized_case
            ):
                return 0.98

        # If the wording uses an explicit numeric
        # boundary instead of the same phrase, compare
        # the relevant number.
        target_numbers = set(
            re.findall(
                r"\b\d+\b",
                normalized_target,
            )
        )

        case_numbers = set(
            re.findall(
                r"\b\d+\b",
                normalized_case,
            )
        )

        if target_numbers & case_numbers:
            return 0.90

    # ==================================================
    # Unregistered SSO/OAuth account rejection
    # ==================================================

    target_unregistered = (
        "unregistered" in normalized_target
    )

    case_unregistered = (
        "unregistered" in normalized_case
    )

    target_sso_oauth = (
        "sso" in normalized_target
        or "oauth" in normalized_target
    )

    case_sso_oauth = (
        "sso" in normalized_case
        or "oauth" in normalized_case
    )

    rejection_words = (
        "reject",
        "rejected",
        "rejection",
        "deny",
        "denied",
        "denial",
        "blocked",
    )

    target_rejection = has_any(
        normalized_target,
        rejection_words,
    )

    case_rejection = has_any(
        normalized_case,
        rejection_words,
    )

    if (
        target_unregistered
        and target_sso_oauth
        and target_rejection
        and case_unregistered
        and case_sso_oauth
        and case_rejection
    ):
        return 0.98

    # ==================================================
    # OAuth cancellation
    # ==================================================

    cancellation_words = (
        "cancel",
        "cancelled",
        "canceled",
        "cancellation",
    )

    target_cancellation = has_any(
        normalized_target,
        cancellation_words,
    )

    case_cancellation = has_any(
        normalized_case,
        cancellation_words,
    )

    if (
        "oauth" in normalized_target
        and target_cancellation
        and "oauth" in normalized_case
        and case_cancellation
    ):
        return 0.98

    # ==================================================
    # OAuth authentication method
    # ==================================================

    if (
        "oauth" in normalized_target
        and "oauth" in normalized_case
        and has_any(
            normalized_target,
            (
                "authentication",
                "login",
                "authentication method",
            ),
        )
        and has_any(
            normalized_case,
            (
                "authentication",
                "login",
                "authentication method",
            ),
        )
    ):
        return 0.90

    # ==================================================
    # SQL injection
    # ==================================================

    target_sql = (
        "sql injection" in normalized_target
        or (
            "sql" in normalized_target
            and "inject" in normalized_target
        )
    )

    case_sql = (
        "sql injection" in normalized_case
        or (
            "sql" in normalized_case
            and "inject" in normalized_case
        )
    )

    if target_sql and case_sql:
        return 0.95

    # ==================================================
    # XSS
    # ==================================================

    if (
        "xss" in normalized_target
        and "xss" in normalized_case
    ):
        return 0.95

    # ==================================================
    # Remember Me / 31-day session
    # ==================================================

    target_31_days = (
        (
            "31" in normalized_target
            and "days" in normalized_target
        )
        or "31 day" in normalized_target
    )

    case_31_days = (
        (
            "31" in normalized_case
            and "days" in normalized_case
        )
        or "31 day" in normalized_case
    )

    target_session = has_any(
        normalized_target,
        (
            "session",
            "remember",
            "active",
            "expiration",
            "expire",
        ),
    )

    case_session = has_any(
        normalized_case,
        (
            "session",
            "remember",
            "active",
            "expiration",
            "expire",
        ),
    )

    if (
        target_31_days
        and target_session
        and case_31_days
        and case_session
    ):
        return 0.98

    # ==================================================
    # Concurrent session behavior
    # ==================================================

    if (
        "concurrent" in normalized_target
        and "session" in normalized_target
        and "concurrent" in normalized_case
        and "session" in normalized_case
    ):
        return 0.95

    # ==================================================
    # Concurrent-session QA clarification
    # ==================================================

    if (
        "concurrent" in normalized_target
        and has_any(
            normalized_target,
            (
                "policy",
                "clarification",
                "clarify",
                "qa input",
            ),
        )
        and "concurrent" in normalized_case
        and has_any(
            normalized_case,
            (
                "policy",
                "clarification",
                "clarify",
                "qa input",
            ),
        )
    ):
        return 0.95

    # ==================================================
    # API unavailable behavior
    # ==================================================

    if (
        "unavailable" in normalized_target
        and "unavailable" in normalized_case
        and has_any(
            normalized_target,
            (
                "api",
                "service",
            ),
        )
        and has_any(
            normalized_case,
            (
                "api",
                "service",
            ),
        )
    ):
        return 0.95

    # ==================================================
    # API timeout behavior
    # ==================================================

    if (
        "timeout" in normalized_target
        and "timeout" in normalized_case
        and has_any(
            normalized_target,
            (
                "api",
                "request",
            ),
        )
        and has_any(
            normalized_case,
            (
                "api",
                "request",
            ),
        )
    ):
        return 0.95

    # ==================================================
    # Logout behavior
    # ==================================================

    if (
        "logout" in normalized_target
        and "logout" in normalized_case
    ):
        return 0.90

    # ==================================================
    # Session invalidation after logout
    # ==================================================

    target_invalidation = (
        "invalidation" in normalized_target
        or "invalidated" in normalized_target
        or "invalidate" in normalized_target
    )

    case_invalidation = (
        "invalidation" in normalized_case
        or "invalidated" in normalized_case
        or "invalidate" in normalized_case
    )

    if (
        target_invalidation
        and case_invalidation
        and "logout" in normalized_target
        and "logout" in normalized_case
    ):
        return 0.98

    return 0.0

# ==================================================
# Coverage Target Scoring
# ==================================================

def _coverage_target_score(
    target: dict[str, Any],
    test_case: dict[str, Any],
) -> float:
    """
    Calculate a conservative deterministic score between a
    coverage target and a generated test case.

    The scorer uses normal lexical overlap plus explicit,
    high-confidence requirement-behavior rules.

    It is intentionally deterministic and does not invent
    coverage. A generated case must contain the behavior
    required by the target.

    The global reconciliation threshold remains 0.45.
    """

    case_text = _test_case_text(
        test_case
    ).casefold()

    if not case_text:
        return 0.0

    title = str(
        target.get(
            "title",
            "",
        )
        or ""
    )

    subject = str(
        target.get(
            "obligation_subject",
            "",
        )
        or ""
    )

    source = str(
        target.get(
            "source_requirement",
            "",
        )
        or ""
    )

    expected = str(
        target.get(
            "expected_behavior",
            "",
        )
        or ""
    )

    coverage_type = str(
        target.get(
            "coverage_type",
            "",
        )
        or ""
    )

    obligation_type = str(
        target.get(
            "obligation_type",
            "",
        )
        or ""
    )

    status = str(
        target.get(
            "status",
            "explicit",
        )
        or "explicit"
    ).casefold()

    target_text = (
        f"{title} "
        f"{subject} "
        f"{source} "
        f"{expected}"
    ).casefold()

    # Normalize punctuation while retaining useful words.
    normalized_case_text = re.sub(
        r"[^a-z0-9]+",
        " ",
        case_text,
    )

    normalized_target_text = re.sub(
        r"[^a-z0-9]+",
        " ",
        target_text,
    )

    score = 0.0

    # ==================================================
    # Standard lexical scoring
    # ==================================================

    case_tokens = _meaningful_tokens(
        case_text
    )

    title_tokens = _meaningful_tokens(
        title
    )

    title_overlap = _token_overlap_score(
        title_tokens,
        case_tokens,
    )

    score += (
        title_overlap
        * 0.45
    )

    subject_overlap = _token_overlap_score(
        _meaningful_tokens(
            subject
        ),
        case_tokens,
    )

    score += (
        subject_overlap
        * 0.20
    )

    source_overlap = _token_overlap_score(
        _meaningful_tokens(
            source
        ),
        case_tokens,
    )

    score += (
        source_overlap
        * 0.15
    )

    expected_overlap = _token_overlap_score(
        _meaningful_tokens(
            expected
        ),
        case_tokens,
    )

    score += (
        expected_overlap
        * 0.10
    )

    type_overlap = _token_overlap_score(
        _meaningful_tokens(
            f"{coverage_type} {obligation_type}"
        ),
        case_tokens,
    )

    score += (
        type_overlap
        * 0.10
    )

    # ==================================================
    # High-confidence exact phrases
    # ==================================================

    exact_phrases = [
        "below minimum",
        "exactly minimum",
        "above minimum",
        "case-insensitive",
        "case-sensitive",
        "whitespace",
        "oauth",
        "unregistered",
        "sql injection",
        "xss",
        "31 days",
        "concurrent",
        "clarify",
        "policy",
        "unavailable",
        "timeout",
        "logout",
        "invalidated",
        "session expiration",
        "remember me",
    ]

    for phrase in exact_phrases:

        if (
            phrase in normalized_target_text
            and phrase in normalized_case_text
        ):

            score += 0.15

    # ==================================================
    # Explicit obligation-family matching
    # ==================================================

    # --------------------------------------------------
    # OBL-011 style:
    #
    # Unregistered SSO/OAuth account must be rejected.
    #
    # Equivalent generated wording can be:
    #
    # "Verify rejection of unregistered SSO/OAuth accounts."
    #
    # We deliberately recognize both OAuth and SSO as
    # authentication-method signals.
    # --------------------------------------------------

    target_unregistered = (
        "unregistered" in normalized_target_text
    )

    case_unregistered = (
        "unregistered" in normalized_case_text
    )

    target_sso = (
        "sso" in normalized_target_text
        or "oauth" in normalized_target_text
    )

    case_sso = (
        "sso" in normalized_case_text
        or "oauth" in normalized_case_text
    )

    rejection_words = (
        "reject",
        "rejected",
        "rejection",
        "deny",
        "denied",
        "denial",
        "blocked",
    )

    target_has_rejection = any(
        word in normalized_target_text
        for word in rejection_words
    )

    case_has_rejection = any(
        word in normalized_case_text
        for word in rejection_words
    )

    if (
        target_unregistered
        and target_sso
        and target_has_rejection
        and case_unregistered
        and case_sso
        and case_has_rejection
    ):

        score += 0.50

    # --------------------------------------------------
    # OAuth cancellation
    # --------------------------------------------------

    target_oauth = (
        "oauth" in normalized_target_text
    )

    case_oauth = (
        "oauth" in normalized_case_text
    )

    cancellation_words = (
        "cancel",
        "cancelled",
        "canceled",
        "cancellation",
    )

    target_has_cancellation = any(
        word in normalized_target_text
        for word in cancellation_words
    )

    case_has_cancellation = any(
        word in normalized_case_text
        for word in cancellation_words
    )

    if (
        target_oauth
        and target_has_cancellation
        and case_oauth
        and case_has_cancellation
    ):

        score += 0.35

    # ==================================================
    # Email whitespace handling
    # ==================================================

    target_email = (
        "email" in normalized_target_text
    )

    case_email = (
        "email" in normalized_case_text
    )

    whitespace_words = (
        "whitespace",
        "trim",
        "trimmed",
        "strip",
        "stripped",
        "leading",
        "trailing",
        "spaces",
    )

    target_has_whitespace = any(
        word in normalized_target_text
        for word in whitespace_words
    )

    case_has_whitespace = any(
        word in normalized_case_text
        for word in whitespace_words
    )

    if (
        target_email
        and target_has_whitespace
        and case_email
        and case_has_whitespace
    ):

        score += 0.30

    # ==================================================
    # 31-day session / Remember Me behavior
    # ==================================================

    target_has_31_days = (
        "31 days" in normalized_target_text
        or (
            "31" in normalized_target_text
            and "days" in normalized_target_text
        )
    )

    case_has_31_days = (
        "31 days" in normalized_case_text
        or (
            "31" in normalized_case_text
            and "days" in normalized_case_text
        )
    )

    session_words = (
        "session",
        "remember",
        "active",
        "expiration",
        "expire",
    )

    target_has_session = any(
        word in normalized_target_text
        for word in session_words
    )

    case_has_session = any(
        word in normalized_case_text
        for word in session_words
    )

    if (
        target_has_31_days
        and target_has_session
        and case_has_31_days
        and case_has_session
    ):

        score += 0.35

    # ==================================================
    # Password boundary behavior
    # ==================================================

    boundary_words = (
        "minimum",
        "below",
        "above",
        "exact",
        "exactly",
    )

    target_has_boundary = any(
        word in normalized_target_text
        for word in boundary_words
    )

    case_has_boundary = any(
        word in normalized_case_text
        for word in boundary_words
    )

    if (
        target_has_boundary
        and case_has_boundary
    ):

        # Match the specific boundary direction where
        # possible. This avoids treating all password
        # boundary cases as identical.
        for phrase in (
            "below minimum",
            "exactly minimum",
            "above minimum",
        ):

            if (
                phrase in normalized_target_text
                and phrase in normalized_case_text
            ):

                score += 0.25

    # ==================================================
    # Case sensitivity
    # ==================================================

    sensitivity_phrases = (
        "case insensitive",
        "case sensitive",
    )

    for phrase in sensitivity_phrases:

        if (
            phrase in normalized_target_text
            and phrase in normalized_case_text
        ):

            score += 0.25

    # ==================================================
    # Security behavior
    # ==================================================

    if (
        "sql injection" in normalized_target_text
        and (
            "sql injection" in normalized_case_text
            or (
                "sql" in normalized_case_text
                and "inject" in normalized_case_text
            )
        )
    ):

        score += 0.30

    if (
        "xss" in normalized_target_text
        and "xss" in normalized_case_text
    ):

        score += 0.30

    # ==================================================
    # API availability / timeout behavior
    # ==================================================

    if (
        "unavailable" in normalized_target_text
        and "unavailable" in normalized_case_text
    ):

        score += 0.25

    if (
        "timeout" in normalized_target_text
        and "timeout" in normalized_case_text
    ):

        score += 0.25

    # ==================================================
    # Logout / invalidation behavior
    # ==================================================

    if (
        "logout" in normalized_target_text
        and (
            "logout" in normalized_case_text
            or "log out" in normalized_case_text
        )
    ):

        score += 0.25

    if (
        "invalidated" in normalized_target_text
        and "invalidat" in normalized_case_text
    ):

        score += 0.30

    # ==================================================
    # Concurrent-session behavior
    # ==================================================

    if (
        "concurrent" in normalized_target_text
        and "concurrent" in normalized_case_text
    ):

        score += 0.25

    # ==================================================
    # QA-input clarification behavior
    # ==================================================

    if status == "requires_qa_input":

        clarification_signals = (
            "clarify",
            "clarification",
            "policy",
            "qa input",
            "requires qa",
            "unspecified",
            "undefined",
        )

        target_has_clarification = any(
            signal in normalized_target_text
            for signal in clarification_signals
        )

        case_has_clarification = any(
            signal in normalized_case_text
            for signal in clarification_signals
        )

        if (
            target_has_clarification
            and case_has_clarification
        ):

            score += 0.30

    # --------------------------------------------------
    # Prevent clarification-only cases from stealing
    # explicit functional coverage.
    # --------------------------------------------------

    if status != "requires_qa_input":

        clarification_signals = (
            "clarify",
            "clarification",
            "requires qa input",
            "qa input required",
        )

        case_is_clarification = any(
            signal in normalized_case_text
            for signal in clarification_signals
        )

        target_is_clarification = any(
            signal in normalized_target_text
            for signal in clarification_signals
        )

        if (
            case_is_clarification
            and not target_is_clarification
        ):

            score -= 0.15

    return max(
        0.0,
        min(
            score,
            1.0,
        ),
    )

# ==================================================
# Test Case Text
# ==================================================

def _test_case_text(
    test_case: dict[str, Any],
) -> str:

    if not isinstance(
        test_case,
        dict,
    ):

        return ""

    values = []

    for value in test_case.values():

        if value is None:
            continue

        if isinstance(
            value,
            (dict, list),
        ):

            values.append(
                json.dumps(
                    value,
                    ensure_ascii=False,
                )
            )

        else:

            values.append(
                str(value)
            )

    return " ".join(
        values
    )


# ==================================================
# Meaningful Tokens
# ==================================================

def _meaningful_tokens(
    value: str,
) -> set[str]:

    if not isinstance(
        value,
        str,
    ):

        return set()

    tokens = re.findall(
        r"[a-z0-9]+",
        value.casefold(),
    )

    stop_words = {
        "the",
        "a",
        "an",
        "to",
        "of",
        "and",
        "with",
        "for",
        "by",
        "is",
        "are",
        "can",
        "must",
        "should",
        "verify",
        "user",
        "users",
        "test",
        "case",
        "behavior",
        "handling",
        "according",
        "requirement",
        "input",
        "value",
        "system",
        "application",
        "during",
        "using",
        "valid",
        "properly",
        "successfully",
        "explicitly",
        "required",
        "specified",
        "condition",
        "conditions",
        "session",
    }

    return {
        token
        for token in tokens
        if token not in stop_words
        and len(token) > 2
    }


# ==================================================
# Token Overlap
# ==================================================

def _token_overlap_score(
    left: set[str],
    right: set[str],
) -> float:

    if not left or not right:
        return 0.0

    intersection = (
        left.intersection(
            right
        )
    )

    if not intersection:
        return 0.0

    # Coverage is measured against the smaller
    # semantic set. This is useful because target
    # descriptions are usually short.
    denominator = min(
        len(left),
        len(right),
    )

    if denominator <= 0:
        return 0.0

    return (
        len(intersection)
        / denominator
    )



def _looks_like_test_case_id(value: Any) -> bool:
    """Return True when a value already looks like a generated test-case ID."""
    normalized = str(value or "").strip().casefold()
    return bool(
        re.fullmatch(r"(?:tc[-_ ]?)?\d{1,5}", normalized)
        or re.fullmatch(r"tc[-_ ]?\d{1,5}", normalized)
    )


def _normalize_generated_test_case_ids(
    test_cases: list[dict[str, Any]],
    columns: list[str],
    assumptions: list[str],
) -> None:
    """Ensure ID columns contain stable sequential case IDs, not titles.

    Some Gemini responses copy the scenario title into a column named ID.
    The uploaded template's ID field is metadata, so replace that value with
    a deterministic ID while preserving every other template field.
    """
    id_columns = [
        column for column in columns
        if str(column).strip().casefold() in {
            "id", "tc id", "test case id", "testcase id",
            "test case id.", "testcaseid",
        }
    ]

    for column in id_columns:
        for index, case in enumerate(test_cases, start=1):
            if not isinstance(case, dict):
                continue

            current = str(case.get(column, "") or "").strip()
            expected_id = f"TC-{index:03d}"

            if not _looks_like_test_case_id(current) or current != expected_id:
                case[column] = expected_id


def _filter_valid_qa_findings(
    coverage_analysis: dict[str, Any],
    requirement: str,
    source_context: str,
    assumptions: list[str],
) -> None:
    """Keep ambiguity findings only when they represent material unknowns.

    Ambiguity is for information that changes expected behavior, not for
    ordinary implementation details that can be tested from the supplied
    evidence. This prevents the UI from showing a large, noisy ambiguity
    count for routine login scenarios.
    """
    values = _normalize_ambiguity_questions(
        coverage_analysis.get("ambiguous_requirements", [])
    )

    material_terms = (
        "business rule", "validation rule", "validation behavior",
        "permission", "permissions", "role", "roles", "authorization",
        "error message", "error response", "threshold", "limit",
        "timeout", "retry", "retention", "policy", "session",
        "redirect", "redirected", "status code", "http ",
        "api contract", "response code", "security rule",
        "password policy", "minimum length", "maximum length",
        "required field", "format rule", "lockout", "rate limit",
        "audit", "data retention",
    )

    filtered = []
    for question in values:
        normalized = _normalize_value(question)
        # Very short/non-specific questions are not useful ambiguity findings.
        if len(normalized) < 18:
            continue
        if any(term in normalized for term in material_terms):
            filtered.append(question)

    removed = len(values) - len(filtered)
    coverage_analysis["ambiguous_requirements"] = _unique_strings(filtered)

    if removed:
        assumptions.append(
            f"Removed {removed} non-material ambiguity finding(s) from the "
            "QA-facing ambiguity list; only requirement-impacting unknowns "
            "remain."
        )


def _build_valid_qa_input_items(
    coverage_targets: dict[str, Any],
    coverage_analysis: dict[str, Any],
) -> list[str]:
    """Return only genuine QA clarification items.

    `assumptions` is an internal audit log and must never be used as the
    UI's QA-input count. The UI should show requirement-impacting unknowns
    only, preferably from deterministic coverage traceability.
    """
    items: list[str] = []

    # First choice: deterministic traceability after the final reconciliation.
    traceability = coverage_analysis.get("coverage_traceability", [])
    if isinstance(traceability, list):
        for item in traceability:
            if not isinstance(item, dict):
                continue
            if str(item.get("status", "")).strip().casefold() != "requires_qa_input":
                continue

            description = str(
                item.get("description")
                or item.get("target")
                or item.get("source_requirement")
                or ""
            ).strip()

            if description:
                items.append(description)

    # Second choice: explicit QA-required deterministic targets. This handles
    # cases where a target is known to need clarification even if it already
    # has a generated clarification case.
    targets = coverage_targets.get("targets", [])
    if isinstance(targets, list):
        for target in targets:
            if not isinstance(target, dict):
                continue

            status = str(
                target.get("status")
                or target.get("qa_status")
                or target.get("resolution")
                or ""
            ).strip().casefold()

            explicit_flag = target.get("requires_qa_input")
            if (
                status not in {
                    "requires_qa_input",
                    "qa_input",
                    "requires qa input",
                    "unresolved",
                }
                and explicit_flag is not True
            ):
                continue

            description = str(
                target.get("expected_behavior")
                or target.get("title")
                or target.get("source_requirement")
                or ""
            ).strip()

            if description:
                items.append(description)

    # Last resort: the deterministic planner's count means that there are
    # real QA-required targets, but their text is not exposed in the final
    # traceability object. Do not fabricate text; return a countable marker.
    if not items:
        planner_count = coverage_targets.get("qa_input_target_count", 0)
        try:
            planner_count = int(planner_count)
        except (TypeError, ValueError):
            planner_count = 0

        for index in range(max(0, planner_count)):
            items.append(
                f"QA clarification required for coverage target {index + 1}."
            )

    # Stable de-duplication.
    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = _normalize_value(item)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


# ==================================================
# Template Semantic Validation
# ==================================================

def sanitize_template_values(
    test_cases: list[dict[str, Any]],
    template: dict[str, Any],
    assumptions: list[str],
) -> None:

    columns = template.get(
        "columns",
        [],
    )

    if not isinstance(
        columns,
        list,
    ):

        return

    field_definitions = template.get(
        "field_definitions",
        {},
    )

    if isinstance(
        field_definitions,
        dict,
    ):

        definitions_by_column = {
            str(key): value
            for key, value in field_definitions.items()
            if isinstance(
                value,
                dict,
            )
        }

    elif isinstance(
        field_definitions,
        list,
    ):

        definitions_by_column = {}

        for definition in field_definitions:

            if not isinstance(
                definition,
                dict,
            ):

                continue

            name = definition.get(
                "name"
            )

            if name is None:
                name = definition.get(
                    "column"
                )

            if name is None:
                continue

            definitions_by_column[
                str(name)
            ] = definition

    else:

        definitions_by_column = {}

    for column in columns:

        column_name = str(
            column
        )

        normalized_name = (
            column_name
            .strip()
            .casefold()
        )

        is_priority = (
            normalized_name == "priority"
            or normalized_name.endswith(
                " priority"
            )
        )

        is_severity = (
            normalized_name == "severity"
            or normalized_name.endswith(
                " severity"
            )
        )

        definition = definitions_by_column.get(
            column_name,
            {},
        )

        if not isinstance(
            definition,
            dict,
        ):

            definition = {}

        allowed_values = _extract_allowed_values(
            definition
        )

        if allowed_values:

            allowed_lookup = {
                _normalize_value(value)
                for value in allowed_values
            }

            for index, test_case in enumerate(
                test_cases,
                start=1,
            ):

                value = test_case.get(
                    column_name,
                    "",
                )

                if value is None:
                    value = ""

                value_text = str(
                    value
                ).strip()

                if not value_text:
                    continue

                if _is_requires_qa_input(value_text) or (
                    _normalize_value(value_text) not in allowed_lookup
                ):
                    # The field has an authoritative value list. QA Input
                    # is not a valid value in such a field, so choose the
                    # safest neutral value from the template.
                    preferred = next(
                        (
                            value for value in allowed_values
                            if _normalize_value(value) in {
                                "medium", "functional", "normal",
                                "low", "minor"
                            }
                        ),
                        allowed_values[0] if allowed_values else "",
                    )

                    if preferred:
                        test_case[column_name] = preferred
                        assumptions.append(
                            f"Test case {index} used an invalid or QA-input "
                            f"value for constrained template field "
                            f"'{column_name}'; replaced with valid value "
                            f"'{preferred}'."
                        )

            continue

        # --------------------------------------------------
        # Priority is generated metadata when the template
        # does not provide an authoritative value list.
        #
        # Do NOT erase the AI-generated priority. The QA user
        # can edit it in the UI before saving/publishing.
        # --------------------------------------------------

        if is_priority:

            # Priority is classification metadata. For ordinary supported
            # functionality, do not turn a missing AI classification into
            # a QA-input item. Use an explicit template value when one is
            # available; otherwise use Medium as the neutral default.
            default_priority = "Medium"
            if allowed_values:
                normalized_allowed = [
                    str(value).strip()
                    for value in allowed_values
                    if str(value).strip()
                ]
                preferred = next(
                    (
                        value for value in normalized_allowed
                        if _normalize_value(value) == "medium"
                    ),
                    None,
                )
                if preferred:
                    default_priority = preferred
                elif normalized_allowed:
                    default_priority = normalized_allowed[0]

            for index, test_case in enumerate(
                test_cases,
                start=1,
            ):

                value = test_case.get(
                    column_name,
                    "",
                )

                if value is None:
                    value = ""

                value_text = str(
                    value
                ).strip()

                if not value_text or _is_requires_qa_input(value_text):
                    test_case[
                        column_name
                    ] = default_priority

                    assumptions.append(
                        f"Test case {index} did not receive a usable "
                        f"Priority value for template field '{column_name}'; "
                        f"used '{default_priority}' as neutral QA metadata."
                    )

            continue

        # --------------------------------------------------
        # Severity remains conservative because the supplied
        # requirement/template may not define a severity scale.
        # --------------------------------------------------

        if is_severity:

            observed_values = _extract_observed_values(
                definition
            )

            if not observed_values:

                for index, test_case in enumerate(
                    test_cases,
                    start=1,
                ):

                    value = test_case.get(
                        column_name,
                        "",
                    )

                    if value is None:
                        value = ""

                    value_text = str(
                        value
                    ).strip()

                    if not value_text:
                        continue

                    if _is_requires_qa_input(
                        value_text
                    ):
                        continue

                    test_case[
                        column_name
                    ] = "Requires QA Input"

                    assumptions.append(
                        f"Test case {index} used AI-generated "
                        f"value '{value_text}' for '{column_name}', "
                        "but the uploaded template did not provide "
                        "an authoritative severity value list; "
                        "replaced with 'Requires QA Input'."
                    )


# ==================================================
# Allowed Values
# ==================================================

def _extract_allowed_values(
    definition: dict[str, Any],
) -> list[str]:

    candidate_keys = (
        "allowed_values",
        "allowedValues",
        "validation_values",
        "validationValues",
        "data_validation_values",
        "dataValidationValues",
    )

    for key in candidate_keys:

        values = definition.get(
            key
        )

        if isinstance(
            values,
            list,
        ):

            cleaned = [
                str(value).strip()
                for value in values
                if value is not None
                and str(value).strip()
            ]

            if cleaned:
                return cleaned

    return []


# ==================================================
# Observed Values
# ==================================================

def _extract_observed_values(
    definition: dict[str, Any],
) -> list[str]:

    candidate_keys = (
        "sample_values",
        "sampleValues",
        "observed_values",
        "observedValues",
    )

    values = []

    for key in candidate_keys:

        candidate = definition.get(
            key
        )

        if isinstance(
            candidate,
            list,
        ):

            values.extend(
                candidate
            )

    return [
        str(value).strip()
        for value in values
        if value is not None
        and str(value).strip()
    ]


# ==================================================
# Value Normalization
# ==================================================

def _normalize_value(
    value: Any,
) -> str:

    return (
        str(value)
        .strip()
        .casefold()
    )


# ==================================================
# Requires QA Input
# ==================================================

def _is_requires_qa_input(
    value: str,
) -> bool:

    normalized = _normalize_value(
        value
    )

    return normalized in {
        "requires qa input",
        "qa input required",
        "requires qa",
    }


# ==================================================
# Duplicate / Overlap Detection
# ==================================================

def detect_duplicate_or_overlapping_scenarios(
    requirement: str,
    test_cases: list[dict[str, Any]],
    coverage_analysis: dict[str, Any],
) -> None:

    findings = coverage_analysis.get(
        "duplicate_or_overlapping_scenarios",
        [],
    )

    if not isinstance(
        findings,
        list,
    ):

        findings = []

    repeated_statements = (
        _find_duplicate_requirement_statements(
            requirement
        )
    )

    for statement, count in repeated_statements:

        finding = (
            "The supplied requirement contains the same "
            f"requirement statement repeated {count} times: "
            f"'{statement}'"
        )

        _append_unique_finding(
            findings,
            finding,
        )

    scenario_entries = []

    for index, test_case in enumerate(
        test_cases,
        start=1,
    ):

        if not isinstance(
            test_case,
            dict,
        ):

            continue

        scenario = _get_test_case_scenario(
            test_case
        )

        if not scenario:
            continue

        scenario_entries.append(
            (
                f"TC-{str(index).zfill(3)}",
                scenario,
            )
        )

    for left_index in range(
        len(scenario_entries)
    ):

        left_id, left_scenario = (
            scenario_entries[
                left_index
            ]
        )

        for right_index in range(
            left_index + 1,
            len(scenario_entries),
        ):

            right_id, right_scenario = (
                scenario_entries[
                    right_index
                ]
            )

            similarity = _text_similarity(
                left_scenario,
                right_scenario,
            )

            if similarity >= 0.80:

                finding = (
                    f"{left_id} and {right_id} contain "
                    "substantially overlapping test scenarios "
                    f"(similarity {similarity:.0%})."
                )

                _append_unique_finding(
                    findings,
                    finding,
                )

    coverage_analysis[
        "duplicate_or_overlapping_scenarios"
    ] = findings


def _find_duplicate_requirement_statements(
    requirement: str,
) -> list[tuple[str, int]]:

    if not isinstance(
        requirement,
        str,
    ):

        return []

    statements = re.split(
        r"(?<=[.!?])\s+",
        requirement.strip(),
    )

    normalized_groups: dict[
        str,
        list[str],
    ] = {}

    for statement in statements:

        cleaned = statement.strip()

        if not cleaned:
            continue

        normalized = _normalize_sentence(
            cleaned
        )

        if len(normalized) < 20:
            continue

        normalized_groups.setdefault(
            normalized,
            [],
        ).append(
            cleaned
        )

    duplicates = []

    for originals in normalized_groups.values():

        if len(originals) > 1:

            duplicates.append(
                (
                    originals[0],
                    len(originals),
                )
            )

    return duplicates


def _normalize_sentence(
    value: str,
) -> str:

    return re.sub(
        r"\s+",
        " ",
        re.sub(
            r"[^a-z0-9]+",
            " ",
            value.casefold(),
        ),
    ).strip()


def _get_test_case_scenario(
    test_case: dict[str, Any],
) -> str:

    preferred_names = (
        "Test Scenario",
        "Business Check",
        "Scenario",
        "Test scenario",
        "Business Check Description",
    )

    for name in preferred_names:

        value = test_case.get(
            name
        )

        if value is not None and str(value).strip():

            return str(
                value
            ).strip()

    for key, value in test_case.items():

        normalized_key = (
            str(key)
            .strip()
            .casefold()
        )

        if (
            "scenario" in normalized_key
            or "business check" in normalized_key
        ):

            if value is not None and str(value).strip():

                return str(
                    value
                ).strip()

    return ""


def _text_similarity(
    left: str,
    right: str,
) -> float:

    left_tokens = _tokenize_for_similarity(
        left
    )

    right_tokens = _tokenize_for_similarity(
        right
    )

    if not left_tokens or not right_tokens:
        return 0.0

    intersection = left_tokens.intersection(
        right_tokens
    )

    union = left_tokens.union(
        right_tokens
    )

    if not union:
        return 0.0

    return (
        len(intersection)
        / len(union)
    )


def _tokenize_for_similarity(
    value: str,
) -> set[str]:

    if not isinstance(
        value,
        str,
    ):

        return set()

    tokens = re.findall(
        r"[a-z0-9]+",
        value.casefold(),
    )

    stop_words = {
        "the",
        "a",
        "an",
        "to",
        "of",
        "and",
        "with",
        "for",
        "by",
        "is",
        "are",
        "can",
        "must",
        "verify",
        "user",
        "test",
    }

    return {
        token
        for token in tokens
        if token not in stop_words
    }


def _append_unique_finding(
    findings: list,
    finding: str,
) -> None:

    if finding not in findings:

        findings.append(
            finding
        )


# ==================================================
# Traceability Validation
# ==================================================

def _validate_traceability(
    coverage_analysis: dict[str, Any],
    test_cases: list[dict[str, Any]],
    assumptions: list[str],
) -> None:

    requirements = coverage_analysis.get(
        "requirements",
        [],
    )

    if not isinstance(
        requirements,
        list,
    ):

        return

    generated_test_case_ids = {
        f"TC-{str(index).zfill(3)}"
        for index, _ in enumerate(
            test_cases,
            start=1,
        )
    }

    requirement_ids = set()

    for requirement in requirements:

        if not isinstance(
            requirement,
            dict,
        ):

            continue

        requirement_id = requirement.get(
            "requirement_id"
        )

        if requirement_id:

            requirement_ids.add(
                str(
                    requirement_id
                )
            )

        references = requirement.get(
            "test_case_references",
            [],
        )

        if not isinstance(
            references,
            list,
        ):

            continue

        unknown_references = [
            str(reference)
            for reference in references
            if str(reference)
            not in generated_test_case_ids
        ]

        if unknown_references:

            assumptions.append(
                "Coverage analysis referenced "
                "test cases that were not generated: "
                + ", ".join(
                    unknown_references
                )
            )

    for field_name in (
        "covered_requirements",
        "uncovered_requirements",
    ):

        values = coverage_analysis.get(
            field_name,
            [],
        )

        if not isinstance(
            values,
            list,
        ):

            continue

        unknown_ids = [
            str(value)
            for value in values
            if str(value)
            not in requirement_ids
        ]

        if unknown_ids:

            assumptions.append(
                f"Coverage analysis field "
                f"'{field_name}' referenced unknown "
                "requirement IDs: "
                + ", ".join(
                    unknown_ids
                )
            )