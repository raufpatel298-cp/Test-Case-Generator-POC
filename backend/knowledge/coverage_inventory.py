from __future__ import annotations

import re
from typing import Any


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _sentences(requirement: str) -> list[str]:
    text = _normalize(requirement)

    if not text:
        return []

    parts = re.split(
        r"(?<=[.!?])\s+|[\r\n]+",
        text,
    )

    return [
        sentence.strip(" .")
        for sentence in parts
        if sentence.strip(" .")
    ]


def _contains_any(text: str, values: list[str]) -> bool:
    return any(value in text for value in values)


def _make_obligation(
    obligation_type: str,
    subject: str,
    source_requirement: str,
    *,
    status: str = "explicit",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "obligation_id": "",
        "type": obligation_type,
        "subject": subject,
        "source_requirement": source_requirement,
        "status": status,
        "details": details or {},
    }


def _add_ids(
    obligations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for index, obligation in enumerate(obligations, start=1):
        obligation["obligation_id"] = f"OBL-{index:03d}"

    return obligations


# ---------------------------------------------------------------------------
# Authentication and credential behavior
# ---------------------------------------------------------------------------

def _extract_authentication_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    has_login = _contains_any(
        lowered,
        [
            "login",
            "log in",
            "sign in",
            "authenticate",
            "authentication",
        ],
    )

    is_cancellation = _contains_any(
        lowered,
        [
            "cancel",
            "cancellation",
            "canceled",
            "cancelled",
        ],
    )

    has_email = "email" in lowered
    has_password = "password" in lowered

    # A cancellation statement must not be treated as
    # successful authentication.
    if has_login and not is_cancellation:
        obligations.append(
            _make_obligation(
                "authentication_behavior",
                "User authentication",
                sentence,
            )
        )

    if has_login and has_email and not is_cancellation:
        obligations.append(
            _make_obligation(
                "authentication_input",
                "Email credential",
                sentence,
                details={
                    "field": "email",
                },
            )
        )

    if has_login and has_password and not is_cancellation:
        obligations.append(
            _make_obligation(
                "authentication_input",
                "Password credential",
                sentence,
                details={
                    "field": "password",
                },
            )
        )

    # -----------------------------------------------------------------------
    # Email behavior
    # -----------------------------------------------------------------------

    if has_email:
        if _contains_any(
            lowered,
            [
                "valid email",
                "valid email address",
                "email format",
                "valid format",
                "correct email",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "valid_input",
                    "Valid email address",
                    sentence,
                )
            )

        if _contains_any(
            lowered,
            [
                "case sensitive",
                "case-sensitive",
                "case insensitive",
                "case-insensitive",
                "uppercase",
                "lowercase",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "case_sensitivity",
                    "Email case handling",
                    sentence,
                )
            )

        if _contains_any(
            lowered,
            [
                "whitespace",
                "leading space",
                "trailing space",
                "leading/trailing space",
                "trim",
                "strip",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "whitespace_handling",
                    "Email whitespace handling",
                    sentence,
                )
            )

        if _contains_any(
            lowered,
            [
                "empty email",
                "blank email",
                "email is empty",
                "email is blank",
                "email required",
                "email is required",
                "email cannot be empty",
                "email cannot be blank",
                "email must not be empty",
                "email must not be blank",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "empty_input",
                    "Empty email",
                    sentence,
                )
            )

        if _contains_any(
            lowered,
            [
                "invalid email",
                "invalid email address",
                "incorrect email",
                "wrong email",
                "malformed email",
                "bad email",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "invalid_input",
                    "Invalid email address",
                    sentence,
                )
            )

    # -----------------------------------------------------------------------
    # Password behavior
    # -----------------------------------------------------------------------

    if has_password:
        minimum_match = re.search(
            r"(?:at least|minimum|min)\s*(\d+)\s*"
            r"(?:characters|chars)",
            lowered,
        )

        if minimum_match:
            minimum = int(minimum_match.group(1))

            obligations.append(
                _make_obligation(
                    "boundary",
                    f"Password minimum length {minimum}",
                    sentence,
                    details={
                        "boundary_type": "minimum",
                        "value": minimum,
                        "unit": "characters",
                    },
                )
            )

        if _contains_any(
            lowered,
            [
                "case sensitive",
                "case-sensitive",
                "case insensitive",
                "case-insensitive",
                "uppercase",
                "lowercase",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "case_sensitivity",
                    "Password case handling",
                    sentence,
                )
            )

        if _contains_any(
            lowered,
            [
                "whitespace",
                "leading space",
                "trailing space",
                "leading/trailing space",
                "trim",
                "strip",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "whitespace_handling",
                    "Password whitespace handling",
                    sentence,
                )
            )

        if _contains_any(
            lowered,
            [
                "empty password",
                "blank password",
                "password is empty",
                "password is blank",
                "password required",
                "password is required",
                "password cannot be empty",
                "password cannot be blank",
                "password must not be empty",
                "password must not be blank",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "empty_input",
                    "Empty password",
                    sentence,
                )
            )

        if _contains_any(
            lowered,
            [
                "invalid password",
                "incorrect password",
                "wrong password",
                "password rejected",
                "password is rejected",
                "bad password",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "invalid_input",
                    "Invalid password",
                    sentence,
                )
            )

    # -----------------------------------------------------------------------
    # Explicit credential combinations
    # -----------------------------------------------------------------------

    if has_login and has_email and has_password and not is_cancellation:
        if _contains_any(
            lowered,
            [
                "valid email and valid password",
                "correct email and correct password",
                "valid credentials",
                "correct credentials",
                "successful login",
                "login succeeds",
                "authentication succeeds",
                "authentication successful",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "credential_combination",
                    "Valid email with valid password",
                    sentence,
                    details={
                        "email": "valid",
                        "password": "valid",
                    },
                )
            )

        if _contains_any(
            lowered,
            [
                "invalid email and valid password",
                "incorrect email and correct password",
                "wrong email and correct password",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "credential_combination",
                    "Invalid email with valid password",
                    sentence,
                    details={
                        "email": "invalid",
                        "password": "valid",
                    },
                )
            )

        if _contains_any(
            lowered,
            [
                "valid email and invalid password",
                "correct email and incorrect password",
                "correct email and wrong password",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "credential_combination",
                    "Valid email with invalid password",
                    sentence,
                    details={
                        "email": "valid",
                        "password": "invalid",
                    },
                )
            )

        if _contains_any(
            lowered,
            [
                "invalid email and invalid password",
                "incorrect email and incorrect password",
                "wrong email and wrong password",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "credential_combination",
                    "Invalid email with invalid password",
                    sentence,
                    details={
                        "email": "invalid",
                        "password": "invalid",
                    },
                )
            )

        if _contains_any(
            lowered,
            [
                "empty email and valid password",
                "blank email and valid password",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "credential_combination",
                    "Empty email with valid password",
                    sentence,
                    details={
                        "email": "empty",
                        "password": "valid",
                    },
                )
            )

        if _contains_any(
            lowered,
            [
                "valid email and empty password",
                "valid email and blank password",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "credential_combination",
                    "Valid email with empty password",
                    sentence,
                    details={
                        "email": "valid",
                        "password": "empty",
                    },
                )
            )

        if _contains_any(
            lowered,
            [
                "empty email and empty password",
                "blank email and blank password",
                "both fields empty",
                "both credentials empty",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "credential_combination",
                    "Empty email with empty password",
                    sentence,
                    details={
                        "email": "empty",
                        "password": "empty",
                    },
                )
            )

    # -----------------------------------------------------------------------
    # Authentication failure
    # -----------------------------------------------------------------------

    if has_login and _contains_any(
        lowered,
        [
            "login fails",
            "login failure",
            "authentication fails",
            "authentication failure",
            "authentication failed",
            "login is rejected",
            "login rejected",
            "credentials are invalid",
            "invalid credentials",
            "incorrect credentials",
        ],
    ):
        obligations.append(
            _make_obligation(
                "authentication_failure",
                "Authentication failure behavior",
                sentence,
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# Session behavior
# ---------------------------------------------------------------------------

def _extract_session_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    has_session = _contains_any(
        lowered,
        [
            "remember me",
            "remember-me",
            "session",
            "logout",
            "log out",
            "sign out",
            "token",
        ],
    )

    is_logout = _contains_any(
        lowered,
        [
            "logout",
            "log out",
            "sign out",
        ],
    )

    has_remember_me_duration = (
        _contains_any(
            lowered,
            [
                "remember me",
                "remember-me",
            ],
        )
        and _contains_any(
            lowered,
            [
                "active for",
                "valid for",
                "last",
                "lasts",
                "duration",
                "expiration",
                "expiry",
                "expires",
                "expire",
            ],
        )
    )

    has_concurrent_sessions = _contains_any(
        lowered,
        [
            "concurrent session",
            "multiple sessions",
            "simultaneous sessions",
            "concurrent login",
        ],
    )

    # Avoid a generic session obligation when a more
    # specific logout, Remember Me expiration, or
    # concurrent-session rule already describes the behavior.
    if (
        has_session
        and not is_logout
        and not has_remember_me_duration
        and not has_concurrent_sessions
    ):
        obligations.append(
            _make_obligation(
                "session_behavior",
                "Session behavior",
                sentence,
            )
        )

    # -----------------------------------------------------------------------
    # Logout
    # -----------------------------------------------------------------------

    if is_logout:
        obligations.append(
            _make_obligation(
                "logout_behavior",
                "Logout behavior",
                sentence,
            )
        )

        if _contains_any(
            lowered,
            [
                "invalidate session",
                "session invalidated",
                "session is invalidated",
                "session must be invalidated",
                "session should be invalidated",
                "invalidate token",
                "token invalidated",
                "token is invalidated",
                "token must be invalidated",
                "session ends",
                "session terminated",
                "cannot access after logout",
                "cannot access after logging out",
            ],
        ):
            obligations.append(
                _make_obligation(
                    "session_invalidation",
                    "Session invalidation after logout",
                    sentence,
                )
            )

    # -----------------------------------------------------------------------
    # Explicit session expiration
    # -----------------------------------------------------------------------

    expiration_match = re.search(
        r"(\d+)\s*"
        r"(?:day|days|hour|hours|minute|minutes)"
        r"\s*(?:expiration|expiry|expires|expire)",
        lowered,
    )

    duration_match = re.search(
        r"(?:session|remember me|remember-me)"
        r".{0,80}?"
        r"(?:active|valid|lasts?|remain(?:s)? active)"
        r"\s*(?:for\s*)?"
        r"(\d+)\s*"
        r"(day|days|hour|hours|minute|minutes)",
        lowered,
    )

    if expiration_match:
        value = int(
            expiration_match.group(1)
        )

        matched_text = expiration_match.group(0)

        if "day" in matched_text:
            normalized_unit = "days"
        elif "hour" in matched_text:
            normalized_unit = "hours"
        else:
            normalized_unit = "minutes"

        obligations.append(
            _make_obligation(
                "session_expiration",
                (
                    f"Session expiration after "
                    f"{value} {normalized_unit}"
                ),
                sentence,
                details={
                    "value": value,
                    "unit": normalized_unit,
                },
            )
        )

    elif duration_match:
        value = int(
            duration_match.group(1)
        )

        unit = duration_match.group(2)

        if unit.startswith("day"):
            normalized_unit = "days"
        elif unit.startswith("hour"):
            normalized_unit = "hours"
        else:
            normalized_unit = "minutes"

        obligations.append(
            _make_obligation(
                "session_expiration",
                (
                    f"Session duration of "
                    f"{value} {normalized_unit}"
                ),
                sentence,
                details={
                    "value": value,
                    "unit": normalized_unit,
                    "source": "explicit_duration",
                },
            )
        )

    # -----------------------------------------------------------------------
    # Expired session behavior
    # -----------------------------------------------------------------------

    if _contains_any(
        lowered,
        [
            "expired session",
            "expired sessions",
            "expired token",
            "after expiration",
            "after expiry",
            "after the session expires",
            "after session expiration",
            "session has expired",
            "session is expired",
        ],
    ):
        obligations.append(
            _make_obligation(
                "expired_session_behavior",
                "Expired session behavior",
                sentence,
            )
        )

    # -----------------------------------------------------------------------
    # Concurrent sessions
    # -----------------------------------------------------------------------

    if has_concurrent_sessions:
        obligations.append(
            _make_obligation(
                "concurrent_session_behavior",
                "Concurrent session behavior",
                sentence,
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# OAuth / SSO
# ---------------------------------------------------------------------------

def _extract_sso_oauth_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    has_oauth = "oauth" in lowered

    has_sso = _contains_any(
        lowered,
        [
            "single sign-on",
            "single sign on",
            "sso",
        ],
    )

    cancellation = _contains_any(
        lowered,
        [
            "cancel",
            "cancelling",
            "canceled",
            "cancelled",
            "cancellation",
        ],
    )

    support_terms = [
        "supported",
        "support",
        "available",
        "allow",
        "use",
        "can use",
    ]

    if (
        has_oauth
        and _contains_any(
            lowered,
            support_terms,
        )
        and not cancellation
    ):
        obligations.append(
            _make_obligation(
                "authentication_method",
                "OAuth login",
                sentence,
            )
        )

    if (
        has_sso
        and _contains_any(
            lowered,
            support_terms,
        )
        and not cancellation
    ):
        obligations.append(
            _make_obligation(
                "authentication_method",
                "Single sign-on",
                sentence,
            )
        )

    if cancellation:
        if has_oauth:
            obligations.append(
                _make_obligation(
                    "negative_behavior",
                    "OAuth cancellation",
                    sentence,
                )
            )
        elif has_sso:
            obligations.append(
                _make_obligation(
                    "negative_behavior",
                    "SSO cancellation",
                    sentence,
                )
            )

    if _contains_any(
        lowered,
        [
            "unregistered",
            "unknown account",
            "account not registered",
            "not registered",
            "unrecognized account",
        ],
    ) and _contains_any(
        lowered,
        [
            "sso",
            "oauth",
        ],
    ):
        obligations.append(
            _make_obligation(
                "negative_behavior",
                "Unregistered SSO/OAuth account",
                sentence,
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

def _extract_security_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    if _contains_any(
        lowered,
        [
            "sql injection",
            "sql injection attack",
            "sql injection handling",
        ],
    ):
        obligations.append(
            _make_obligation(
                "injection_handling",
                "SQL injection handling",
                sentence,
            )
        )

    if _contains_any(
        lowered,
        [
            "xss",
            "cross-site scripting",
            "cross site scripting",
        ],
    ):
        obligations.append(
            _make_obligation(
                "injection_handling",
                "XSS handling",
                sentence,
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# API and integration
# ---------------------------------------------------------------------------

def _extract_api_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    has_api = _contains_any(
        lowered,
        [
            "api",
            "rest api",
            "endpoint",
            "http",
        ],
    )

    if not has_api:
        return obligations

    if _contains_any(
        lowered,
        [
            "valid request",
            "valid input",
            "valid payload",
        ],
    ):
        obligations.append(
            _make_obligation(
                "api_valid_input",
                "Valid API request",
                sentence,
            )
        )

    if _contains_any(
        lowered,
        [
            "invalid request",
            "invalid input",
            "invalid payload",
        ],
    ):
        obligations.append(
            _make_obligation(
                "api_invalid_input",
                "Invalid API request",
                sentence,
            )
        )

    status_codes = re.findall(
        r"\b([1-5]\d{2})\b",
        sentence,
    )

    for code in status_codes:
        obligations.append(
            _make_obligation(
                "api_status_code",
                f"HTTP status {code}",
                sentence,
                details={
                    "status_code": int(code),
                },
            )
        )

    if _contains_any(
        lowered,
        [
            "contract",
            "schema",
            "response structure",
            "response format",
        ],
    ):
        obligations.append(
            _make_obligation(
                "api_contract",
                "API contract",
                sentence,
            )
        )

    has_unavailable = (
        "unavailable" in lowered
        and _contains_any(
            lowered,
            [
                "api",
                "service",
                "backend",
                "server",
            ],
        )
    )

    if has_unavailable:
        obligations.append(
            _make_obligation(
                "api_availability",
                "API/service unavailable behavior",
                sentence,
            )
        )

    if _contains_any(
        lowered,
        [
            "api timeout",
            "api times out",
            "api timed out",
            "request timeout",
            "request times out",
            "request timed out",
            "timeout condition",
            "timeout conditions",
            "backend timeout",
            "server timeout",
            "timeout",
        ],
    ):
        obligations.append(
            _make_obligation(
                "api_timeout",
                "API/request timeout behavior",
                sentence,
            )
        )

    if _contains_any(
        lowered,
        [
            "network failure",
            "network error",
            "connection failure",
            "connection error",
            "backend failure",
            "backend error",
            "server failure",
            "server error",
        ],
    ):
        obligations.append(
            _make_obligation(
                "api_failure",
                "Backend/network failure behavior",
                sentence,
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

def _extract_performance_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    if not _contains_any(
        lowered,
        [
            "performance",
            "response time",
            "concurrent",
            "throughput",
            "load",
        ],
    ):
        return obligations

    response_match = re.search(
        r"(?:under|less than|within|below)\s*"
        r"(\d+(?:\.\d+)?)\s*"
        r"(ms|milliseconds|s|seconds)",
        lowered,
    )

    if response_match:
        value = float(
            response_match.group(1)
        )
        unit = response_match.group(2)

        obligations.append(
            _make_obligation(
                "response_time",
                f"Response time under {value:g} {unit}",
                sentence,
                details={
                    "value": value,
                    "unit": unit,
                },
            )
        )

    concurrent_match = re.search(
        r"(\d+)\s*concurrent",
        lowered,
    )

    if concurrent_match:
        value = int(
            concurrent_match.group(1)
        )

        obligations.append(
            _make_obligation(
                "concurrent_load",
                f"{value} concurrent users/requests",
                sentence,
                details={
                    "value": value,
                },
            )
        )

    throughput_match = re.search(
        r"(\d+(?:\.\d+)?)\s*"
        r"(?:requests?|transactions?|operations?)\s*"
        r"(?:per|/)\s*(?:second|sec)",
        lowered,
    )

    if throughput_match:
        value = float(
            throughput_match.group(1)
        )

        obligations.append(
            _make_obligation(
                "throughput",
                f"Throughput of {value:g} requests/second",
                sentence,
                details={
                    "value": value,
                    "unit": "requests/second",
                },
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

def _extract_file_upload_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    if not _contains_any(
        lowered,
        [
            "file upload",
            "upload file",
            "upload files",
            "file can be uploaded",
            "files can be uploaded",
        ],
    ):
        return obligations

    obligations.append(
        _make_obligation(
            "file_upload_behavior",
            "File upload behavior",
            sentence,
        )
    )

    size_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(mb|gb|kb)",
        lowered,
    )

    if size_match:
        value = float(
            size_match.group(1)
        )
        unit = size_match.group(2)

        obligations.append(
            _make_obligation(
                "boundary",
                f"File upload size limit {value:g} {unit.upper()}",
                sentence,
                details={
                    "boundary_type": "maximum",
                    "value": value,
                    "unit": unit.upper(),
                },
            )
        )

    file_types = re.findall(
        r"\b(pdf|docx|doc|xlsx|xls|csv|txt|png|jpg|jpeg)\b",
        lowered,
    )

    seen_types: set[str] = set()

    for file_type in file_types:
        if file_type in seen_types:
            continue

        seen_types.add(file_type)

        obligations.append(
            _make_obligation(
                "allowed_file_type",
                file_type.upper(),
                sentence,
            )
        )

    if _contains_any(
        lowered,
        [
            "invalid file type",
            "unsupported file type",
            "disallowed file type",
            "file type not allowed",
        ],
    ):
        obligations.append(
            _make_obligation(
                "invalid_input",
                "Invalid input file type",
                sentence,
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# Accessibility
# ---------------------------------------------------------------------------

def _extract_accessibility_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    if _contains_any(
        lowered,
        [
            "keyboard",
            "keyboard accessible",
            "keyboard navigation",
        ],
    ):
        obligations.append(
            _make_obligation(
                "accessibility",
                "Keyboard accessibility",
                sentence,
            )
        )

    if _contains_any(
        lowered,
        [
            "screen reader",
            "screen-reader",
            "aria",
            "assistive technology",
        ],
    ):
        obligations.append(
            _make_obligation(
                "accessibility",
                "Screen reader accessibility",
                sentence,
            )
        )

    if "wcag" in lowered:
        obligations.append(
            _make_obligation(
                "accessibility",
                "WCAG accessibility",
                sentence,
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# State transitions / workflows
# ---------------------------------------------------------------------------

def _extract_state_transition_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    transition_pairs = [
        ("pending", "approved"),
        ("pending", "rejected"),
        ("draft", "submitted"),
        ("submitted", "approved"),
        ("submitted", "rejected"),
        ("open", "closed"),
        ("active", "inactive"),
    ]

    for source_state, target_state in transition_pairs:
        if (
            source_state in lowered
            and target_state in lowered
        ):
            obligations.append(
                _make_obligation(
                    "state_transition",
                    f"{source_state} to {target_state}",
                    sentence,
                    details={
                        "from": source_state,
                        "to": target_state,
                    },
                )
            )

    if "workflow" in lowered and not obligations:
        obligations.append(
            _make_obligation(
                "workflow",
                "Workflow behavior",
                sentence,
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# QA input / missing policy
# ---------------------------------------------------------------------------

def _extract_qa_input_obligations(
    sentence: str,
) -> list[dict[str, Any]]:
    lowered = sentence.lower()
    obligations: list[dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # Concurrent-session policy
    # -----------------------------------------------------------------------

    if _contains_any(
        lowered,
        [
            "concurrent sessions",
            "multiple concurrent sessions",
            "simultaneous sessions",
        ],
    ) and not _contains_any(
        lowered,
        [
            "maximum",
            "limit",
            "allowed",
            "only",
            "up to",
        ],
    ):
        obligations.append(
            _make_obligation(
                "qa_input",
                "Concurrent session policy",
                sentence,
                status="requires_qa_input",
                details={
                    "reason": (
                        "The requirement mentions concurrent sessions "
                        "but does not define the allowed policy."
                    )
                },
            )
        )

    # -----------------------------------------------------------------------
    # Remember Me expiration policy
    # -----------------------------------------------------------------------

    has_remember_me = _contains_any(
        lowered,
        [
            "remember me",
            "remember-me",
        ],
    )

    has_explicit_duration = bool(
        re.search(
            r"\b\d+\s*"
            r"(?:day|days|hour|hours|minute|minutes)\b",
            lowered,
        )
    )

    has_duration_wording = _contains_any(
        lowered,
        [
            "active for",
            "valid for",
            "last",
            "lasts",
            "duration",
            "expiration",
            "expiry",
            "expires",
            "expire",
        ],
    )

    if (
        has_remember_me
        and not has_explicit_duration
        and not has_duration_wording
    ):
        obligations.append(
            _make_obligation(
                "qa_input",
                "Remember Me expiration policy",
                sentence,
                status="requires_qa_input",
                details={
                    "reason": (
                        "Remember Me behavior is specified without "
                        "an explicit expiration policy."
                    )
                },
            )
        )

    # -----------------------------------------------------------------------
    # Performance target
    # -----------------------------------------------------------------------

    has_performance_signal = _contains_any(
        lowered,
        [
            "performance",
            "response time",
            "response-time",
            "latency",
            "throughput",
            "load testing",
            "load test",
            "load handling",
            "concurrent users",
            "concurrent requests",
        ],
    )

    has_measurable_response_target = bool(
        re.search(
            r"\b\d+(?:\.\d+)?\s*"
            r"(?:ms|milliseconds|s|seconds)\b",
            lowered,
        )
    )

    has_measurable_concurrency = bool(
        re.search(
            r"\b\d+\s*concurrent\s*"
            r"(?:users?|requests?|sessions?)?\b",
            lowered,
        )
    )

    has_measurable_throughput = bool(
        re.search(
            r"\b\d+(?:\.\d+)?\s*"
            r"(?:requests?|transactions?|operations?)\s*"
            r"(?:per|/)\s*(?:second|sec)\b",
            lowered,
        )
    )

    if (
        has_performance_signal
        and not has_measurable_response_target
        and not has_measurable_concurrency
        and not has_measurable_throughput
    ):
        obligations.append(
            _make_obligation(
                "qa_input",
                "Performance target",
                sentence,
                status="requires_qa_input",
                details={
                    "reason": (
                        "Performance is mentioned without a "
                        "measurable target."
                    )
                },
            )
        )

    return obligations


# ---------------------------------------------------------------------------
# Main inventory builder
# ---------------------------------------------------------------------------

def extract_coverage_inventory(
    requirement: str,
) -> dict[str, Any]:
    sentences = _sentences(requirement)

    obligations: list[dict[str, Any]] = []

    for sentence in sentences:
        obligations.extend(
            _extract_authentication_obligations(
                sentence
            )
        )

        obligations.extend(
            _extract_session_obligations(
                sentence
            )
        )

        obligations.extend(
            _extract_sso_oauth_obligations(
                sentence
            )
        )

        obligations.extend(
            _extract_security_obligations(
                sentence
            )
        )

        obligations.extend(
            _extract_api_obligations(
                sentence
            )
        )

        obligations.extend(
            _extract_performance_obligations(
                sentence
            )
        )

        obligations.extend(
            _extract_file_upload_obligations(
                sentence
            )
        )

        obligations.extend(
            _extract_accessibility_obligations(
                sentence
            )
        )

        obligations.extend(
            _extract_state_transition_obligations(
                sentence
            )
        )

        obligations.extend(
            _extract_qa_input_obligations(
                sentence
            )
        )

    # Remove exact duplicate obligations while preserving order.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for obligation in obligations:
        key = (
            obligation.get("type"),
            obligation.get("subject"),
            obligation.get("source_requirement"),
            obligation.get("status"),
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(obligation)

    unique = _add_ids(unique)

    return {
        "requirement": requirement,
        "sentence_count": len(sentences),
        "obligation_count": len(unique),
        "obligations": unique,
    }


def build_coverage_inventory(
    requirement: str,
) -> dict[str, Any]:
    return extract_coverage_inventory(requirement)


def get_coverage_inventory(
    requirement: str,
) -> dict[str, Any]:
    return extract_coverage_inventory(requirement)