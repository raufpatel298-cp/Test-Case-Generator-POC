import re
from typing import Any


# ==================================================
# Applicability Engine
# ==================================================
#
# Purpose:
# Determine which QA knowledge dimensions are actually
# relevant to the supplied requirement.
#
# IMPORTANT:
#
# This module does NOT generate test cases.
#
# Knowledge may suggest a testing dimension.
# Knowledge may NOT create a requirement.
#
# Applicability must be based on meaningful evidence
# in the supplied requirement.
# ==================================================


# ==================================================
# Domain Signal Overrides
# ==================================================

DOMAIN_SIGNAL_OVERRIDES = {
    "authentication": {
        "signals": [
            "login",
            "log in",
            "sign in",
            "sign-in",
            "authentication",
            "authenticate",
            "credentials",
            "password",
            "remember me",
            "single sign-on",
            "sso",
            "oauth",
            "session",
            "logout",
            "log out",
            "token",
        ]
    }
}


# ==================================================
# Dimension Signals
# ==================================================
#
# Signals are intentionally conservative.
#
# Generic words such as:
#   users
#   request
#   response
#
# must NOT independently activate specialized
# testing dimensions.
# ==================================================

DIMENSION_SIGNALS = {
    "functional": [
        "must",
        "should",
        "shall",
        "can",
        "allows",
        "allow",
        "able to",
        "user can",
        "users can",
        "user is able to",
        "users are able to",
        "system shall",
        "system must",
        "system should",
        "required",
        "optional",
        "workflow",
        "business rule",
        "acceptance criteria",
    ],

    "boundary_and_input": [
        "minimum",
        "maximum",
        "at least",
        "at most",
        "up to",
        "no more than",
        "no less than",
        "not more than",
        "not less than",
        "greater than",
        "greater than or equal to",
        "less than",
        "less than or equal to",
        "between",
        "exactly",
        "minimum length",
        "maximum length",
        "character limit",
        "characters",
        "bytes",
        "kb",
        "mb",
        "gb",
        "format",
        "valid",
        "invalid",
        "required field",
        "optional field",
        "empty",
        "blank",
        "case-sensitive",
        "case-insensitive",
        "case sensitive",
        "case insensitive",
        "uppercase",
        "lowercase",
        "whitespace",
        "leading whitespace",
        "trailing whitespace",
        "leading and trailing whitespace",
        "trim",
        "normalize",
        "allowed values",
        "not allowed",
    ],

    "negative_and_error": [
        "invalid",
        "reject",
        "rejected",
        "failure",
        "fail",
        "error",
        "unable",
        "unavailable",
        "timeout",
        "timed out",
        "retry",
        "graceful",
        "exception",
        "denied",
        "decline",
        "error message",
        "error response",
    ],

    "state_transition": [
        "state",
        "status",
        "transition",
        "workflow",
        "submitted",
        "approved",
        "rejected",
        "pending",
        "completed",
        "cancelled",
        "canceled",
        "active",
        "inactive",
        "expired",
        "logged in",
        "logged out",
        "after login",
        "before login",
        "after logout",
        "before logout",
    ],

    "security": [
        "security",
        "secure",
        "authentication",
        "authorization",
        "credential",
        "credentials",
        "password",
        "permission",
        "permissions",
        "role",
        "roles",
        "session",
        "token",
        "sql injection",
        "sqli",
        "xss",
        "cross-site scripting",
        "injection",
        "brute force",
        "rate limit",
        "rate limiting",
        "lockout",
        "sensitive data",
        "confidential data",
        "access control",
        "bypass",
        "encrypt",
        "encryption",
    ],

    "performance": [
        "performance",
        "response time",
        "latency",
        "within 2 seconds",
        "within 5 seconds",
        "within 10 seconds",
        "milliseconds",
        "throughput",
        "concurrent users",
        "concurrent requests",
        "concurrency",
        "load testing",
        "load test",
        "stress testing",
        "stress test",
        "endurance testing",
        "endurance test",
        "capacity",
        "requests per second",
        "transactions per second",
        "uptime",
        "recovery time",
        "performance requirement",
        "performance criteria",
    ],

    "api_and_integration": [
        "api",
        "api endpoint",
        "rest api",
        "soap api",
        "http api",
        "endpoint",
        "api request",
        "api response",
        "external system",
        "third-party system",
        "integration",
        "integrate with",
        "webhook",
        "payload",
        "json payload",
        "http status",
        "rest",
        "soap",
        "service dependency",
        "external dependency",
        "data exchange",
        "service unavailable",
        "api unavailable",
        "api timeout",
    ],

    "accessibility_and_compatibility": [
        "accessibility",
        "accessible",
        "keyboard navigation",
        "keyboard accessible",
        "keyboard accessibility",
        "navigate using the keyboard",
        "navigate with the keyboard",
        "keyboard",
        "screen reader",
        "assistive technology",
        "wcag",
        "aria",
        "responsive design",
        "browser compatibility",
        "chrome",
        "firefox",
        "edge",
        "safari",
        "mobile device",
        "tablet",
        "operating system compatibility",
        "compatibility requirement",
    ],
}


# ==================================================
# Normalize Text
# ==================================================

def normalize_requirement_text(
    requirement: str,
) -> str:
    """
    Normalize text for applicability detection.

    The original requirement is never modified.
    """

    if not isinstance(
        requirement,
        str,
    ):
        return ""

    return re.sub(
        r"\s+",
        " ",
        requirement.strip().casefold(),
    )


# ==================================================
# Signal Matching
# ==================================================

def signal_matches(
    normalized_requirement: str,
    signal: str,
) -> bool:
    """
    Determine whether a meaningful signal exists.

    Uses word-boundary matching where possible so that
    short terms do not accidentally match unrelated words.
    """

    normalized_signal = normalize_requirement_text(
        signal
    )

    if not normalized_signal:
        return False

    # Multi-word phrases are safely searched directly.
    if " " in normalized_signal:
        return normalized_signal in normalized_requirement

    # Single-word signals require a word boundary.
    pattern = rf"\b{re.escape(normalized_signal)}\b"

    return re.search(
        pattern,
        normalized_requirement,
    ) is not None


# ==================================================
# Detect Applicable Domains
# ==================================================

def detect_applicable_domains(
    requirement: str,
    knowledge_library: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Detect domains explicitly supported by the requirement.

    A domain is applicable only when there is textual
    evidence for it in the supplied requirement.
    """

    normalized_requirement = (
        normalize_requirement_text(
            requirement
        )
    )

    if not normalized_requirement:
        return []

    detected = []

    knowledge_items = knowledge_library.get(
        "knowledge_items",
        [],
    )

    if not isinstance(
        knowledge_items,
        list,
    ):
        return []

    for item in knowledge_items:

        if not isinstance(
            item,
            dict,
        ):
            continue

        if item.get(
            "knowledge_type"
        ) != "qa_domain":
            continue

        name = str(
            item.get(
                "name",
                "",
            )
        ).strip()

        if not name:
            continue

        signals = item.get(
            "domain_signals",
            [],
        )

        if not isinstance(
            signals,
            list,
        ):
            signals = []

        matched_signals = []

        for signal in signals:

            signal_text = str(
                signal
            ).strip()

            if not signal_text:
                continue

            if signal_matches(
                normalized_requirement,
                signal_text,
            ):
                matched_signals.append(
                    signal_text
                )

        # Built-in overrides provide additional explicit
        # signals for important domains.
        override = DOMAIN_SIGNAL_OVERRIDES.get(
            name.casefold()
        )

        if isinstance(
            override,
            dict,
        ):

            for signal in override.get(
                "signals",
                [],
            ):

                signal_text = str(
                    signal
                ).strip()

                if (
                    signal_text
                    and signal_matches(
                        normalized_requirement,
                        signal_text,
                    )
                    and signal_text.casefold()
                    not in {
                        value.casefold()
                        for value in matched_signals
                    }
                ):
                    matched_signals.append(
                        signal_text
                    )

        if matched_signals:

            detected.append(
                {
                    "domain": name,
                    "matched_signals": _unique_strings(
                        matched_signals
                    ),
                    "source_file": item.get(
                        "_source_file",
                        "",
                    ),
                }
            )

    return detected


# ==================================================
# Detect Applicable Dimensions
# ==================================================

def detect_applicable_dimensions(
    requirement: str,
) -> list[dict[str, Any]]:
    """
    Detect testing dimensions supported by the requirement.

    This function is deliberately conservative.

    A dimension is returned only when meaningful evidence
    exists in the requirement.

    It does NOT create test cases.
    """

    normalized_requirement = (
        normalize_requirement_text(
            requirement
        )
    )

    if not normalized_requirement:
        return []

    results = []

    for dimension, signals in (
        DIMENSION_SIGNALS.items()
    ):

        matched_signals = []

        for signal in signals:

            signal_text = str(
                signal
            ).strip()

            if not signal_text:
                continue

            if signal_matches(
                normalized_requirement,
                signal_text,
            ):
                matched_signals.append(
                    signal_text
                )

        if matched_signals:

            results.append(
                {
                    "dimension": dimension,
                    "matched_signals": _unique_strings(
                        matched_signals
                    ),
                }
            )

    return results


# ==================================================
# Evaluate Applicability
# ==================================================

def evaluate_applicability(
    requirement: str,
    knowledge_library: dict[str, Any],
) -> dict[str, Any]:
    """
    Produce a requirement-bound applicability result.

    Applicable knowledge means that a testing dimension
    should be considered.

    It does NOT mean that the dimension can introduce
    unsupported behavior into the generated test cases.
    """

    domains = detect_applicable_domains(
        requirement,
        knowledge_library,
    )

    dimensions = detect_applicable_dimensions(
        requirement
    )

    return {
        "requirement_present": bool(
            normalize_requirement_text(
                requirement
            )
        ),
        "applicable_domains": domains,
        "applicable_dimensions": dimensions,
        "rules": {
            "requirement_is_authority": True,
            "knowledge_is_guidance": True,
            "knowledge_cannot_create_requirement": True,
            "unsupported_scenarios_must_not_be_generated": True,
            "traceability_required": True,
            "missing_information_must_not_be_invented": True,
        },
    }


# ==================================================
# Get Applicable Knowledge Items
# ==================================================

def get_applicable_knowledge_items(
    requirement: str,
    knowledge_library: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Return knowledge items whose domains or dimensions
    have evidence in the requirement.

    Generic knowledge is NOT automatically applied.
    """

    applicability = evaluate_applicability(
        requirement,
        knowledge_library,
    )

    applicable_dimension_names = {
        item["dimension"]
        for item in applicability.get(
            "applicable_dimensions",
            [],
        )
        if isinstance(
            item,
            dict,
        )
    }

    applicable_domains = {
        item["domain"].casefold()
        for item in applicability.get(
            "applicable_domains",
            [],
        )
        if isinstance(
            item,
            dict,
        )
    }

    results = []

    knowledge_items = knowledge_library.get(
        "knowledge_items",
        [],
    )

    if not isinstance(
        knowledge_items,
        list,
    ):
        return []

    dimension_aliases = {
        "functional testing": "functional",
        "boundary and input testing": "boundary_and_input",
        "negative and error handling testing": "negative_and_error",
        "state transition and workflow testing": "state_transition",
        "security testing": "security",
        "performance and reliability testing": "performance",
        "api and integration testing": "api_and_integration",
        "accessibility and compatibility testing": (
            "accessibility_and_compatibility"
        ),
    }

    for item in knowledge_items:

        if not isinstance(
            item,
            dict,
        ):
            continue

        knowledge_type = item.get(
            "knowledge_type"
        )

        name = str(
            item.get(
                "name",
                "",
            )
        ).strip()

        if knowledge_type == "qa_domain":

            if name.casefold() in applicable_domains:
                results.append(
                    item
                )

            continue

        if knowledge_type != "qa_testing_dimension":
            continue

        mapped_dimension = dimension_aliases.get(
            name.casefold()
        )

        if (
            mapped_dimension
            and mapped_dimension
            in applicable_dimension_names
        ):
            results.append(
                item
            )

    return results


# ==================================================
# Build Safe Knowledge Context
# ==================================================

def build_applicable_knowledge_context(
    requirement: str,
    knowledge_library: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a safe, requirement-bound knowledge context.

    Only knowledge supported by evidence in the supplied
    requirement is returned.
    """

    applicability = evaluate_applicability(
        requirement,
        knowledge_library,
    )

    applicable_items = get_applicable_knowledge_items(
        requirement,
        knowledge_library,
    )

    return {
        "applicability": applicability,
        "applicable_knowledge": applicable_items,
        "safety_rules": {
            "generate_only_from_provided_requirements": True,
            "knowledge_is_not_a_requirement_source": True,
            "unsupported_scenarios_must_not_be_generated": True,
            "unsupported_domain_behavior_is_forbidden": True,
            "unsupported_security_controls_are_forbidden": True,
            "unsupported_performance_targets_are_forbidden": True,
            "unsupported_api_details_are_forbidden": True,
            "unsupported_accessibility_requirements_are_forbidden": True,
            "unsupported_business_rules_are_forbidden": True,
        },
    }


# ==================================================
# Utility
# ==================================================

def _unique_strings(
    values: list[Any],
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