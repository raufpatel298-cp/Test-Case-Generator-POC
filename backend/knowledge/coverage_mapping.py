import re
from typing import Any


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        cleaned = _clean_text(item)
        key = cleaned.lower()

        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)

    return result


def _obligation_type(obligation: dict[str, Any]) -> str:
    return _clean_text(
        obligation.get("obligation_type")
        or obligation.get("type")
        or ""
    )


def _source_requirement(obligation: dict[str, Any]) -> str:
    return _clean_text(
        obligation.get("source_requirement")
        or obligation.get("source_text")
        or obligation.get("statement")
        or ""
    )


def _obligation_id(
    obligation: dict[str, Any],
    fallback_index: int,
) -> str:
    return _clean_text(
        obligation.get("obligation_id")
        or f"OBL-{fallback_index + 1:03d}"
    )


def _make_target(
    coverage_type: str,
    title: str,
    obligation: dict[str, Any],
    *,
    expected_behavior: str = "",
) -> dict[str, Any]:
    target: dict[str, Any] = {
        "coverage_type": _clean_text(coverage_type),
        "title": _clean_text(title),
        "obligation_type": _obligation_type(obligation),
        "obligation_subject": _clean_text(
            obligation.get("subject", "")
        ),
        "source_requirement": _source_requirement(
            obligation
        ),
        "status": _clean_text(
            obligation.get("status")
            or "explicit"
        ),
    }

    if expected_behavior:
        target["expected_behavior"] = _clean_text(
            expected_behavior
        )

    if obligation.get("reason"):
        target["reason"] = _clean_text(
            obligation["reason"]
        )

    return target


def _extract_numeric_value(text: str) -> str:
    match = re.search(
        r"\b\d+(?:\.\d+)?\b",
        text,
        flags=re.IGNORECASE,
    )

    return match.group(0) if match else ""


def _extract_time_value(text: str) -> str:
    match = re.search(
        r"\b\d+(?:\.\d+)?\s*"
        r"(?:seconds?|sec|milliseconds?|ms|minutes?|mins?|"
        r"hours?|days?)\b",
        text,
        flags=re.IGNORECASE,
    )

    return match.group(0) if match else ""


def _extract_size_value(text: str) -> str:
    match = re.search(
        r"\b\d+(?:\.\d+)?\s*"
        r"(?:KB|MB|GB|TB|bytes?)\b",
        text,
        flags=re.IGNORECASE,
    )

    return match.group(0) if match else ""


def _extract_status_code(subject: str) -> str:
    match = re.search(
        r"\bHTTP\s+([1-5]\d{2})\b",
        subject,
        flags=re.IGNORECASE,
    )

    return (
        f"HTTP {match.group(1)}"
        if match
        else subject
    )


def _contains_any(
    text: str,
    phrases: list[str],
) -> bool:
    lowered = text.lower()

    return any(
        phrase.lower() in lowered
        for phrase in phrases
    )


def _map_boundary(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    statement = _source_requirement(obligation)
    subject = _clean_text(
        obligation.get("subject")
        or ""
    )

    lowered = statement.lower()
    targets: list[dict[str, Any]] = []

    size_value = _extract_size_value(statement)

    if size_value and _contains_any(
        lowered,
        [
            "up to",
            "at most",
            "maximum",
            "max",
            "no more than",
        ],
    ):
        targets.extend(
            [
                _make_target(
                    "boundary",
                    f"Below maximum {size_value}",
                    obligation,
                    expected_behavior=(
                        "Input below the stated maximum "
                        "should follow the requirement."
                    ),
                ),
                _make_target(
                    "boundary",
                    f"Exactly maximum {size_value}",
                    obligation,
                    expected_behavior=(
                        "Input exactly at the stated maximum "
                        "should be accepted when the requirement "
                        "allows values up to that maximum."
                    ),
                ),
                _make_target(
                    "boundary",
                    f"Above maximum {size_value}",
                    obligation,
                    expected_behavior=(
                        "Input above the stated maximum "
                        "should not be accepted."
                    ),
                ),
            ]
        )

        return targets

    numeric_value = _extract_numeric_value(
        statement
    )

    if not numeric_value:
        numeric_value = _extract_numeric_value(
            subject
        )

    if numeric_value and _contains_any(
        lowered,
        [
            "at least",
            "minimum",
            "no less than",
            "not less than",
            "greater than or equal",
        ],
    ):
        targets.extend(
            [
                _make_target(
                    "boundary",
                    f"Below minimum {numeric_value}",
                    obligation,
                    expected_behavior=(
                        "Value below the stated minimum "
                        "should not satisfy the requirement."
                    ),
                ),
                _make_target(
                    "boundary",
                    f"Exactly minimum {numeric_value}",
                    obligation,
                    expected_behavior=(
                        "Value exactly at the stated minimum "
                        "should satisfy the requirement."
                    ),
                ),
                _make_target(
                    "boundary",
                    f"Above minimum {numeric_value}",
                    obligation,
                    expected_behavior=(
                        "Value above the stated minimum "
                        "should satisfy the requirement."
                    ),
                ),
            ]
        )

        return targets

    if numeric_value and _contains_any(
        lowered,
        [
            "at most",
            "maximum",
            "max",
            "no more than",
            "not more than",
            "less than or equal",
        ],
    ):
        targets.extend(
            [
                _make_target(
                    "boundary",
                    f"Below maximum {numeric_value}",
                    obligation,
                    expected_behavior=(
                        "Value below the stated maximum "
                        "should satisfy the requirement."
                    ),
                ),
                _make_target(
                    "boundary",
                    f"Exactly maximum {numeric_value}",
                    obligation,
                    expected_behavior=(
                        "Value exactly at the stated maximum "
                        "should satisfy the requirement."
                    ),
                ),
                _make_target(
                    "boundary",
                    f"Above maximum {numeric_value}",
                    obligation,
                    expected_behavior=(
                        "Value above the stated maximum "
                        "should not satisfy the requirement."
                    ),
                ),
            ]
        )

        return targets

    return [
        _make_target(
            "boundary",
            f"Boundary validation: {subject}",
            obligation,
            expected_behavior=(
                "Validate behavior at and around the "
                "requirement-defined boundary."
            ),
        )
    ]


def _map_valid_input(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "valid input"
    )

    return [
        _make_target(
            "positive",
            f"Valid {subject}",
            obligation,
            expected_behavior=(
                "Valid input should satisfy the "
                "stated requirement."
            ),
        )
    ]


def _map_invalid_input(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "negative",
            "Invalid input",
            obligation,
            expected_behavior=(
                "Invalid input should be rejected or "
                "handled according to the requirement."
            ),
        )
    ]


def _map_empty_input(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "input"
    )

    return [
        _make_target(
            "negative",
            f"Empty or blank {subject}",
            obligation,
            expected_behavior=(
                "Empty or blank input should be handled "
                "according to the explicitly stated requirement."
            ),
        )
    ]


def _map_case_sensitivity(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "input"
    )

    statement = _source_requirement(
        obligation
    )

    lowered = statement.lower()

    if _contains_any(
        lowered,
        [
            "case-insensitive",
            "case insensitive",
            "case does not matter",
        ],
    ):
        title = (
            f"Case-insensitive handling of {subject}"
        )

        expected = (
            "Case variations should be handled "
            "according to the explicitly stated "
            "case-insensitivity requirement."
        )

    elif _contains_any(
        lowered,
        [
            "case-sensitive",
            "case sensitive",
        ],
    ):
        title = (
            f"Case-sensitive handling of {subject}"
        )

        expected = (
            "Changing character case should affect "
            "behavior according to the stated "
            "case-sensitive requirement."
        )

    else:
        title = f"Case handling for {subject}"

        expected = (
            "Validate the explicitly specified "
            "case behavior."
        )

    return [
        _make_target(
            "case_sensitivity",
            title,
            obligation,
            expected_behavior=expected,
        )
    ]


def _map_whitespace(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "input"
    )

    statement = _source_requirement(
        obligation
    )

    lowered = statement.lower()

    if _contains_any(
        lowered,
        [
            "trim",
            "trimmed",
            "strip whitespace",
            "ignore whitespace",
            "leading/trailing whitespace",
            "leading and trailing whitespace",
        ],
    ):
        expected = (
            "Leading and trailing whitespace should be "
            "handled according to the stated trimming/"
            "whitespace-normalization behavior."
        )
    else:
        expected = (
            "Whitespace should be handled according to "
            "the explicitly stated requirement."
        )

    return [
        _make_target(
            "whitespace",
            f"Whitespace handling for {subject}",
            obligation,
            expected_behavior=expected,
        )
    ]


def _map_authentication_input(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "credential"
    )

    return [
        _make_target(
            "authentication_input",
            f"Authentication input: {subject}",
            obligation,
            expected_behavior=(
                f"The {subject} input should be handled "
                "according to the explicitly stated "
                "authentication requirement."
            ),
        )
    ]


def _map_credential_combination(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "credentials"
    )

    return [
        _make_target(
            "authentication_credentials",
            f"Credential combination: {subject}",
            obligation,
            expected_behavior=(
                "The explicitly required credential "
                "combination should produce the stated "
                "authentication behavior."
            ),
        )
    ]


def _map_authentication_failure(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "authentication failure"
    )

    return [
        _make_target(
            "authentication_failure",
            f"Authentication failure: {subject}",
            obligation,
            expected_behavior=(
                "The explicitly identified authentication "
                "failure condition should be handled "
                "according to the requirement."
            ),
        )
    ]


def _map_session_behavior(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "session"
    )

    return [
        _make_target(
            "session",
            f"Session behavior: {subject}",
            obligation,
            expected_behavior=(
                "Validate the session behavior explicitly "
                "defined by the requirement."
            ),
        )
    ]


def _map_session_expiration(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    statement = _source_requirement(
        obligation
    )

    duration = re.search(
        r"\b\d+\s*"
        r"(?:day|days|hour|hours|minute|minutes)\b",
        statement,
        flags=re.IGNORECASE,
    )

    if duration:
        title = (
            f"Session expiration after "
            f"{duration.group(0)}"
        )
    else:
        title = "Session expiration behavior"

    return [
        _make_target(
            "session_expiration",
            title,
            obligation,
            expected_behavior=(
                "Validate that the session expires according "
                "to the explicitly stated expiration rule."
            ),
        )
    ]


def _map_expired_session(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "session_expiration",
            "Behavior after session expiration",
            obligation,
            expected_behavior=(
                "Validate the explicitly stated behavior "
                "when an expired session is used."
            ),
        )
    ]


def _map_authentication(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "authentication",
            "Successful authentication",
            obligation,
            expected_behavior=(
                "Valid authentication credentials should "
                "allow the explicitly required login behavior."
            ),
        )
    ]


def _map_authentication_method(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "authentication method"
    )

    return [
        _make_target(
            "authentication_method",
            f"Authentication using {subject}",
            obligation,
            expected_behavior=(
                "The explicitly supported authentication "
                "method should complete according to the requirement."
            ),
        )
    ]


def _map_negative_behavior(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "negative behavior"
    )

    return [
        _make_target(
            "negative",
            f"Negative behavior: {subject}",
            obligation,
            expected_behavior=(
                "The explicitly identified negative condition "
                "should be handled according to the requirement."
            ),
        )
    ]


def _map_injection(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "injection"
    )

    return [
        _make_target(
            "security",
            f"Security handling: {subject}",
            obligation,
            expected_behavior=(
                "The explicitly identified injection condition "
                "must be handled according to the requirement."
            ),
        )
    ]


def _map_availability(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "service"
    )

    return [
        _make_target(
            "availability",
            f"Unavailable {subject}",
            obligation,
            expected_behavior=(
                "Validate the explicitly required behavior "
                "when the service is unavailable."
            ),
        )
    ]


def _map_timeout(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "service"
    )

    return [
        _make_target(
            "timeout",
            f"Timeout handling: {subject}",
            obligation,
            expected_behavior=(
                "Validate the explicitly required behavior "
                "when the service or request times out."
            ),
        )
    ]


def _map_file_upload(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "file_upload",
            "File upload behavior",
            obligation,
            expected_behavior=(
                "Users should be able to perform the "
                "explicitly required file upload behavior."
            ),
        )
    ]


def _map_allowed_file_type(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "file type"
    )

    return [
        _make_target(
            "file_type",
            f"Allowed file type: {subject}",
            obligation,
            expected_behavior=(
                f"The explicitly supported {subject} "
                "file type should be accepted."
            ),
        )
    ]


def _map_api_valid(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "api_positive",
            "Valid API request",
            obligation,
            expected_behavior=(
                "A valid API request should receive the "
                "explicitly required successful behavior."
            ),
        )
    ]


def _map_api_invalid(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "api_negative",
            "Invalid API request",
            obligation,
            expected_behavior=(
                "An invalid API request should receive the "
                "explicitly required failure behavior."
            ),
        )
    ]


def _map_api_status(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    status = _extract_status_code(
        _clean_text(
            obligation.get("subject")
            or ""
        )
    )

    return [
        _make_target(
            "api_status",
            f"API response status: {status}",
            obligation,
            expected_behavior=(
                f"The API should return {status} for the "
                "condition explicitly associated with that status."
            ),
        )
    ]


def _map_api_contract(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "api_contract",
            "API response contract",
            obligation,
            expected_behavior=(
                "Validate the API response contract explicitly "
                "defined by the requirement."
            ),
        )
    ]


def _map_api_failure(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "API failure"
    )

    return [
        _make_target(
            "api_failure",
            f"API failure handling: {subject}",
            obligation,
            expected_behavior=(
                "Validate the explicitly required behavior "
                "when the API encounters the identified failure."
            ),
        )
    ]


def _map_concurrent_load(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "concurrent load"
    )

    return [
        _make_target(
            "performance",
            f"Concurrent load: {subject}",
            obligation,
            expected_behavior=(
                "Validate the explicitly required concurrent "
                "load level."
            ),
        )
    ]


def _map_response_time(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    statement = _source_requirement(
        obligation
    )

    duration = _extract_time_value(
        statement
    )

    title = (
        f"Response time within {duration}"
        if duration
        else "Response time target"
    )

    return [
        _make_target(
            "performance",
            title,
            obligation,
            expected_behavior=(
                "Validate that response time meets the "
                "explicitly stated target."
            ),
        )
    ]


def _map_throughput(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "performance",
            "Throughput target",
            obligation,
            expected_behavior=(
                "Validate the explicitly stated throughput target."
            ),
        )
    ]


def _map_accessibility(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "accessibility"
    )

    return [
        _make_target(
            "accessibility",
            f"Accessibility: {subject}",
            obligation,
            expected_behavior=(
                "Validate the explicitly required accessibility "
                "behavior."
            ),
        )
    ]


def _map_accessibility_standard(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "accessibility standard"
    )

    return [
        _make_target(
            "accessibility_standard",
            f"Accessibility standard: {subject}",
            obligation,
            expected_behavior=(
                f"Validate the explicitly referenced {subject} "
                "requirement."
            ),
        )
    ]


def _map_state_transition(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    state = _clean_text(
        obligation.get("subject")
        or "state"
    )

    return [
        _make_target(
            "state_transition",
            f"Transition to {state}",
            obligation,
            expected_behavior=(
                f"The explicitly required {state} state "
                "should be reachable according to the requirement."
            ),
        )
    ]


def _map_workflow_behavior(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "workflow",
            "Workflow/state transition behavior",
            obligation,
            expected_behavior=(
                "Validate the workflow behavior explicitly "
                "defined by the requirement."
            ),
        )
    ]


def _map_logout(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "logout",
            "Logout behavior",
            obligation,
            expected_behavior=(
                "The logout behavior explicitly required by "
                "the requirement should occur successfully."
            ),
        )
    ]


def _map_session_invalidation(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _make_target(
            "session_invalidation",
            "Session invalidation after logout",
            obligation,
            expected_behavior=(
                "After logout, the session should be invalidated "
                "as explicitly required by the requirement."
            ),
        )
    ]


def _map_qa_input(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = _clean_text(
        obligation.get("subject")
        or "QA clarification"
    )

    return [
        _make_target(
            "qa_input",
            f"QA clarification required: {subject}",
            obligation,
            expected_behavior=(
                "Do not invent expected behavior. "
                "Obtain QA clarification before treating "
                "this as an authoritative test expectation."
            ),
        )
    ]


def map_obligation_to_targets(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(obligation, dict):
        return []

    obligation_type = _obligation_type(
        obligation
    ).lower()

    mapping = {
        "boundary": _map_boundary,

        "valid_input": _map_valid_input,
        "invalid_input": _map_invalid_input,
        "empty_input": _map_empty_input,

        "case_sensitivity": _map_case_sensitivity,

        "whitespace_handling": _map_whitespace,
        "whitespace_behavior": _map_whitespace,

        "authentication_input": _map_authentication_input,
        "credential_combination": _map_credential_combination,
        "authentication_failure": _map_authentication_failure,

        "session_behavior": _map_session_behavior,
        "session_expiration": _map_session_expiration,
        "expired_session_behavior": _map_expired_session,
        "concurrent_session_behavior": _map_session_behavior,

        "authentication_behavior": _map_authentication,
        "authentication_method": _map_authentication_method,

        "negative_behavior": _map_negative_behavior,

        "injection_handling": _map_injection,

        "logout_behavior": _map_logout,
        "session_invalidation": _map_session_invalidation,

        "api_availability": _map_availability,
        "availability_failure": _map_availability,

        "api_timeout": _map_timeout,
        "timeout_behavior": _map_timeout,

        "api_failure": _map_api_failure,

        "file_upload_behavior": _map_file_upload,
        "allowed_file_type": _map_allowed_file_type,

        "api_valid_request": _map_api_valid,
        "api_valid_input": _map_api_valid,

        "api_invalid_request": _map_api_invalid,
        "api_invalid_input": _map_api_invalid,

        "api_response_status": _map_api_status,
        "api_status_code": _map_api_status,

        "api_contract": _map_api_contract,

        "concurrent_load": _map_concurrent_load,
        "response_time": _map_response_time,
        "throughput": _map_throughput,

        "accessibility": _map_accessibility,
        "accessibility_standard": _map_accessibility_standard,

        "state_transition": _map_state_transition,
        "workflow_behavior": _map_workflow_behavior,

        "qa_input": _map_qa_input,
    }

    mapper = mapping.get(
        obligation_type
    )

    if mapper is None:
        subject = _clean_text(
            obligation.get("subject")
            or obligation_type
            or "requirement"
        )

        return [
            _make_target(
                "requirement_behavior",
                f"Validate: {subject}",
                obligation,
                expected_behavior=(
                    "Validate the behavior explicitly "
                    "stated in the source requirement."
                ),
            )
        ]

    return mapper(obligation)


def map_inventory_to_coverage_targets(
    inventory: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(inventory, dict):
        return {
            "requirement": "",
            "targets": [],
            "target_count": 0,
            "explicit_target_count": 0,
            "qa_input_target_count": 0,
            "obligation_coverage": [],
        }

    requirement = _clean_text(
        inventory.get("requirement", "")
    )

    obligations = inventory.get(
        "obligations",
        [],
    )

    if not isinstance(obligations, list):
        obligations = []

    targets: list[dict[str, Any]] = []
    obligation_coverage: list[dict[str, Any]] = []

    for index, obligation in enumerate(
        obligations
    ):
        if not isinstance(obligation, dict):
            continue

        obligation_id = _obligation_id(
            obligation,
            index,
        )

        mapped_targets = (
            map_obligation_to_targets(
                obligation
            )
        )

        for target in mapped_targets:
            target["obligation_id"] = (
                obligation_id
            )
            targets.append(target)

        obligation_coverage.append(
            {
                "obligation_id": obligation_id,
                "obligation_type": _obligation_type(
                    obligation
                ),
                "subject": _clean_text(
                    obligation.get("subject", "")
                ),
                "source_text": _source_requirement(
                    obligation
                ),
                "target_count": len(
                    mapped_targets
                ),
            }
        )

    explicit_target_count = sum(
        1
        for target in targets
        if target.get("status")
        == "explicit"
    )

    qa_input_target_count = sum(
        1
        for target in targets
        if target.get("status")
        == "requires_qa_input"
    )

    return {
        "requirement": requirement,
        "targets": targets,
        "target_count": len(targets),
        "explicit_target_count": (
            explicit_target_count
        ),
        "qa_input_target_count": (
            qa_input_target_count
        ),
        "obligation_coverage": (
            obligation_coverage
        ),
    }


def summarize_coverage_targets(
    coverage: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(coverage, dict):
        return {
            "target_count": 0,
            "explicit_target_count": 0,
            "qa_input_target_count": 0,
            "coverage_types": [],
            "titles": [],
        }

    targets = coverage.get(
        "targets",
        [],
    )

    if not isinstance(targets, list):
        targets = []

    coverage_types = _unique(
        [
            str(
                target.get(
                    "coverage_type",
                    "",
                )
            )
            for target in targets
            if isinstance(target, dict)
        ]
    )

    titles = _unique(
        [
            str(
                target.get(
                    "title",
                    "",
                )
            )
            for target in targets
            if isinstance(target, dict)
        ]
    )

    return {
        "target_count": coverage.get(
            "target_count",
            0,
        ),
        "explicit_target_count": coverage.get(
            "explicit_target_count",
            0,
        ),
        "qa_input_target_count": coverage.get(
            "qa_input_target_count",
            0,
        ),
        "coverage_types": coverage_types,
        "titles": titles,
    }