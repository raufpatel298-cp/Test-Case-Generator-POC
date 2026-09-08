import re
from typing import Any

from knowledge.applicability import (
    build_applicable_knowledge_context,
    normalize_requirement_text,
    signal_matches,
)


# ==================================================
# Coverage Planner
# ==================================================
#
# Purpose:
#
# Convert requirement-bound applicability information
# into a structured, module-agnostic coverage plan.
#
# IMPORTANT:
#
# This module does NOT generate test cases.
#
# It identifies coverage areas that should be considered
# based on evidence in the supplied requirements.
#
# The requirement remains the highest authority.
#
# Knowledge can:
#   - suggest testing dimensions
#   - suggest testing techniques
#   - suggest coverage categories
#
# Knowledge cannot:
#   - create business requirements
#   - create unsupported security requirements
#   - create unsupported performance targets
#   - create unsupported APIs
#   - create unsupported accessibility requirements
#   - create unsupported authentication behavior
#
# Every coverage item must have requirement evidence.
# ==================================================


# ==================================================
# Generic Module Signals
# ==================================================
#
# These are deliberately conservative.
#
# A module is reported only when the requirement contains
# a relatively strong application-domain signal.
#
# Generic QA words such as:
#   reject
#   account
#   API
#   pending
#
# must not independently create application modules.
#
# The list is NOT intended to define all possible modules.
# Future domain knowledge can extend module detection.
# ==================================================

MODULE_SIGNAL_GROUPS = {
    "authentication": [
        "login",
        "log in",
        "sign in",
        "sign-in",
        "authentication",
        "authenticate",
        "authentication fields",
        "password",
        "credentials",
        "remember me",
        "single sign-on",
        "sso",
        "oauth login",
        "oauth authentication",
        "logout",
        "log out",
    ],

    "user_management": [
        "user profile",
        "user profiles",
        "user account",
        "user accounts",
        "user management",
        "create user",
        "create users",
        "register user",
        "register users",
        "user registration",
    ],

    "file_management": [
        "file upload",
        "file uploads",
        "upload file",
        "upload files",
        "document upload",
        "document uploads",
        "attachment upload",
        "download file",
        "download files",
        "file download",
        "file downloads",
    ],

    "search": [
        "search products",
        "search users",
        "search orders",
        "search results",
        "search query",
        "search by",
        "filter results",
        "filter products",
        "filter users",
        "sort results",
        "sort products",
    ],

    "payment": [
        "payment",
        "payments",
        "make a payment",
        "make payment",
        "process payment",
        "payment processing",
        "checkout",
        "billing",
        "invoice",
        "credit card",
        "debit card",
        "refund",
        "refund payment",
    ],

    "order_management": [
        "place order",
        "place an order",
        "cancel order",
        "cancel an order",
        "order status",
        "order management",
        "order processing",
        "shipment",
        "delivery",
    ],

    "notification": [
        "notification",
        "notifications",
        "email notification",
        "email notifications",
        "sms notification",
        "sms notifications",
        "push notification",
        "push notifications",
    ],

    "reporting": [
        "reporting",
        "generate report",
        "generate reports",
        "export report",
        "export reports",
        "analytics dashboard",
        "report dashboard",
    ],

    "workflow": [
        "workflow",
        "approval workflow",
        "approval process",
        "review workflow",
        "status transition",
    ],

    "api_integration": [
        "api endpoint",
        "rest api",
        "soap api",
        "http api",
        "webhook",
        "api integration",
        "external system",
        "third-party system",
        "service dependency",
        "external dependency",
    ],
}


# ==================================================
# Generic Coverage Areas
# ==================================================
#
# Coverage areas are generic QA techniques.
#
# They are activated only when:
#
#   1. The applicability engine says the dimension
#      is applicable.
#
#   2. The specific coverage area has evidence in
#      the supplied requirement.
#
# This prevents the knowledge library from introducing
# unrelated scenarios.
# ==================================================

COVERAGE_AREAS = [
    {
        "dimension": "functional",
        "name": "Primary Functional Behavior",
        "signals": [
            "must",
            "should",
            "shall",
            "can",
            "allows",
            "allow",
            "able to",
            "required",
            "optional",
            "workflow",
            "business rule",
            "acceptance criteria",
        ],
        "purpose": (
            "Verify the explicitly described business or user-facing behavior."
        ),
    },

    {
        "dimension": "boundary_and_input",
        "name": "Minimum Boundary",
        "signals": [
            "minimum",
            "at least",
            "minimum length",
            "minimum value",
            "minimum size",
            "no less than",
            "not less than",
            "greater than or equal to",
        ],
        "purpose": (
            "Verify behavior at the explicitly stated minimum boundary."
        ),
    },

    {
        "dimension": "boundary_and_input",
        "name": "Maximum Boundary",
        "signals": [
            "maximum",
            "at most",
            "up to",
            "no more than",
            "not more than",
            "maximum length",
            "maximum value",
            "maximum size",
            "character limit",
        ],
        "purpose": (
            "Verify behavior at the explicitly stated maximum boundary."
        ),
    },

    {
        "dimension": "boundary_and_input",
        "name": "Exact Boundary",
        "signals": [
            "exactly",
            "exact length",
            "exact value",
            "exact number",
        ],
        "purpose": (
            "Verify behavior at an explicitly defined exact boundary."
        ),
    },

    {
        "dimension": "boundary_and_input",
        "name": "Valid Input",
        "signals": [
            "valid",
            "valid input",
            "valid value",
            "valid format",
            "correct format",
        ],
        "purpose": (
            "Verify explicitly supported valid input behavior."
        ),
    },

    {
        "dimension": "boundary_and_input",
        "name": "Invalid Input",
        "signals": [
            "invalid",
            "invalid input",
            "invalid value",
            "invalid format",
            "not allowed",
        ],
        "purpose": (
            "Verify explicitly described rejection or handling of invalid input."
        ),
    },

    {
        "dimension": "boundary_and_input",
        "name": "Empty or Blank Input",
        "signals": [
            "empty",
            "blank",
            "empty field",
            "blank field",
            "required field",
        ],
        "purpose": (
            "Verify explicitly described behavior for empty or blank input."
        ),
    },

    {
        "dimension": "boundary_and_input",
        "name": "Case Sensitivity",
        "signals": [
            "case-sensitive",
            "case-insensitive",
            "case sensitive",
            "case insensitive",
            "uppercase",
            "lowercase",
        ],
        "purpose": (
            "Verify case behavior only when case handling is stated or constrained."
        ),
    },

    {
        "dimension": "boundary_and_input",
        "name": "Whitespace Handling",
        "signals": [
            "whitespace",
            "leading whitespace",
            "trailing whitespace",
            "leading and trailing whitespace",
            "trim",
        ],
        "purpose": (
            "Verify whitespace behavior only when the requirement addresses it."
        ),
    },

    {
        "dimension": "negative_and_error",
        "name": "Negative Behavior",
        "signals": [
            "invalid",
            "reject",
            "rejected",
            "denied",
            "failure",
            "fail",
            "unable",
            "decline",
            "not allowed",
        ],
        "purpose": (
            "Verify explicitly described unsuccessful or rejected behavior."
        ),
    },

    {
        "dimension": "negative_and_error",
        "name": "Error Handling",
        "signals": [
            "error",
            "error message",
            "error response",
            "exception",
            "failure",
            "timeout",
            "timed out",
            "unavailable",
            "retry",
        ],
        "purpose": (
            "Verify explicitly described system error behavior."
        ),
    },

    {
        "dimension": "state_transition",
        "name": "State Transition",
        "signals": [
            "state",
            "status",
            "transition",
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
        ],
        "purpose": (
            "Verify explicitly defined state or status transitions."
        ),
    },

    {
        "dimension": "security",
        "name": "Authentication Security",
        "signals": [
            "authentication",
            "authenticate",
            "credentials",
            "password",
            "login",
            "log in",
            "sign in",
            "sign-in",
        ],
        "purpose": (
            "Verify security behavior directly associated with explicitly "
            "described authentication requirements."
        ),
    },

    {
        "dimension": "security",
        "name": "Authorization and Access Control",
        "signals": [
            "authorization",
            "authorize",
            "permission",
            "permissions",
            "role",
            "roles",
            "access control",
        ],
        "purpose": (
            "Verify explicitly described authorization or access-control behavior."
        ),
    },

    {
        "dimension": "security",
        "name": "Injection Handling",
        "signals": [
            "sql injection",
            "sqli",
            "xss",
            "cross-site scripting",
            "injection",
        ],
        "purpose": (
            "Verify explicitly mentioned injection-related security behavior."
        ),
    },

    {
        "dimension": "security",
        "name": "Session and Token Behavior",
        "signals": [
            "session",
            "token",
            "session expiration",
            "session timeout",
            "remember me",
            "logout",
            "log out",
        ],
        "purpose": (
            "Verify explicitly described session, token, remember-me, "
            "or logout behavior."
        ),
    },

    {
        "dimension": "performance",
        "name": "Response Time",
        "signals": [
            "response time",
            "latency",
            "within 2 seconds",
            "within 5 seconds",
            "within 10 seconds",
            "milliseconds",
            "seconds",
        ],
        "purpose": (
            "Verify explicitly stated response-time or latency expectations."
        ),
    },

    {
        "dimension": "performance",
        "name": "Concurrent Load",
        "signals": [
            "concurrent users",
            "concurrent requests",
            "concurrency",
            "load testing",
            "load test",
        ],
        "purpose": (
            "Verify explicitly stated concurrent-user or concurrent-request behavior."
        ),
    },

    {
        "dimension": "performance",
        "name": "Capacity",
        "signals": [
            "capacity",
            "throughput",
            "requests per second",
            "transactions per second",
            "maximum users",
            "maximum requests",
        ],
        "purpose": (
            "Verify explicitly stated capacity or throughput requirements."
        ),
    },

    {
        "dimension": "api_and_integration",
        "name": "API Contract",
        "signals": [
            "api",
            "api endpoint",
            "rest api",
            "soap api",
            "http api",
            "endpoint",
            "api request",
            "api response",
            "http status",
        ],
        "purpose": (
            "Verify explicitly described API request, response, or contract behavior."
        ),
    },

    {
        "dimension": "api_and_integration",
        "name": "External Integration",
        "signals": [
            "external system",
            "third-party system",
            "integration",
            "integrate with",
            "webhook",
            "external dependency",
            "service dependency",
        ],
        "purpose": (
            "Verify explicitly described external-system integration behavior."
        ),
    },

    {
        "dimension": "accessibility_and_compatibility",
        "name": "Keyboard Accessibility",
        "signals": [
            "keyboard",
            "keyboard navigation",
            "keyboard accessible",
            "keyboard accessibility",
            "navigate using the keyboard",
            "navigate with the keyboard",
        ],
        "purpose": (
            "Verify explicitly stated keyboard accessibility behavior."
        ),
    },

    {
        "dimension": "accessibility_and_compatibility",
        "name": "Screen Reader Accessibility",
        "signals": [
            "screen reader",
            "assistive technology",
            "aria",
            "wcag",
        ],
        "purpose": (
            "Verify explicitly stated screen-reader or assistive-technology behavior."
        ),
    },

    {
        "dimension": "accessibility_and_compatibility",
        "name": "Browser Compatibility",
        "signals": [
            "browser compatibility",
            "chrome",
            "firefox",
            "edge",
            "safari",
        ],
        "purpose": (
            "Verify explicitly stated browser compatibility requirements."
        ),
    },

    {
        "dimension": "accessibility_and_compatibility",
        "name": "Mobile or Device Compatibility",
        "signals": [
            "mobile device",
            "tablet",
            "responsive design",
            "device compatibility",
        ],
        "purpose": (
            "Verify explicitly stated device or responsive behavior."
        ),
    },
]


# ==================================================
# Detect Module / Domain Signals
# ==================================================

def detect_modules(
    requirement: str,
) -> list[dict[str, Any]]:
    """
    Detect possible application modules from explicit
    requirement language.

    This is descriptive only.

    It does NOT create requirements and does NOT determine
    what the application must support.

    Module detection is intentionally conservative.
    """

    normalized_requirement = (
        normalize_requirement_text(
            requirement
        )
    )

    if not normalized_requirement:
        return []

    modules = []

    for module_name, signals in MODULE_SIGNAL_GROUPS.items():

        matched_signals = []

        for signal in signals:

            if signal_matches(
                normalized_requirement,
                signal,
            ):
                matched_signals.append(
                    signal
                )

        if matched_signals:

            modules.append(
                {
                    "module": module_name,
                    "matched_signals": _unique_strings(
                        matched_signals
                    ),
                    "source": "provided_requirement",
                }
            )

    return modules


# ==================================================
# Build Requirement Evidence
# ==================================================

def find_requirement_evidence(
    requirement: str,
    signals: list[str],
) -> list[str]:
    """
    Find explicit textual evidence in the requirement.

    Only matched requirement signals are returned.

    No new requirement is created.
    """

    normalized_requirement = (
        normalize_requirement_text(
            requirement
        )
    )

    if not normalized_requirement:
        return []

    matched = []

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
            matched.append(
                signal_text
            )

    return _unique_strings(
        matched
    )


# ==================================================
# Build Coverage Plan
# ==================================================

def build_coverage_plan(
    requirement: str,
    knowledge_library: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a requirement-bound and module-agnostic coverage plan.

    A coverage area is included only when:

        1. Its dimension is applicable.
        2. There is direct evidence for that coverage area.

    The planner does not invent missing policies.
    """

    normalized_requirement = (
        normalize_requirement_text(
            requirement
        )
    )

    if not normalized_requirement:

        return {
            "requirement_present": False,
            "detected_modules": [],
            "coverage_items": [],
            "qa_input_areas": [],
            "rules": _planner_rules(),
        }

    applicability_context = (
        build_applicable_knowledge_context(
            requirement,
            knowledge_library,
        )
    )

    applicability = applicability_context.get(
        "applicability",
        {},
    )

    applicable_dimensions = {
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

    # --------------------------------------------------
    # Evidence from applicability engine
    # --------------------------------------------------
    #
    # The applicability engine is the source of truth
    # for which dimensions have requirement evidence.
    #
    # This avoids having two separate detection systems
    # disagree about whether a dimension applies.
    # --------------------------------------------------

    dimension_evidence = {
        item["dimension"]: item.get(
            "matched_signals",
            [],
        )
        for item in applicability.get(
            "applicable_dimensions",
            [],
        )
        if isinstance(
            item,
            dict,
        )
    }

    coverage_items = []
    seen_items = set()

    for coverage_area in COVERAGE_AREAS:

        dimension = coverage_area[
            "dimension"
        ]

        if dimension not in applicable_dimensions:
            continue

        area_evidence = find_requirement_evidence(
            requirement,
            coverage_area[
                "signals"
            ],
        )

        # --------------------------------------------------
        # Important fallback:
        #
        # If applicability already found evidence for the
        # dimension but this specific coverage area's
        # terminology is slightly different, preserve
        # the applicability evidence only when it clearly
        # belongs to the same coverage dimension.
        #
        # We do NOT automatically create every coverage
        # area for the dimension.
        # --------------------------------------------------

        evidence = area_evidence

        if not evidence:
            evidence = _derive_compatible_evidence(
                coverage_area,
                dimension_evidence.get(
                    dimension,
                    [],
                ),
            )

        if not evidence:
            continue

        name = coverage_area[
            "name"
        ]

        key = (
            dimension.casefold(),
            name.casefold(),
        )

        if key in seen_items:
            continue

        seen_items.add(
            key
        )

        coverage_items.append(
            {
                "dimension": dimension,
                "coverage_area": name,
                "purpose": coverage_area[
                    "purpose"
                ],
                "status": "APPLY",
                "requirement_evidence": _unique_strings(
                    evidence
                ),
                "traceability_required": True,
                "source": "provided_requirement",
            }
        )

    qa_input_areas = identify_qa_input_areas(
        requirement,
        coverage_items,
    )

    return {
        "requirement_present": True,
        "detected_modules": detect_modules(
            requirement
        ),
        "applicable_dimensions": sorted(
            applicable_dimensions
        ),
        "coverage_items": coverage_items,
        "qa_input_areas": qa_input_areas,
        "rules": _planner_rules(),
    }


# ==================================================
# Compatible Evidence
# ==================================================

def _derive_compatible_evidence(
    coverage_area: dict[str, Any],
    dimension_signals: list[str],
) -> list[str]:
    """
    Reuse applicability evidence only when it is
    semantically compatible with the coverage area.

    This prevents a generic dimension signal such as
    "must" from activating every coverage area inside
    that dimension.

    Only explicit evidence already detected from the
    requirement is returned.
    """

    name = str(
        coverage_area.get(
            "name",
            "",
        )
    ).casefold()

    normalized_dimension_signals = {
        str(signal).casefold()
        for signal in dimension_signals
    }

    # Functional coverage can safely use explicit
    # functional intent signals.
    if name == "primary functional behavior":

        functional_signals = {
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
            "required",
            "optional",
            "workflow",
            "business rule",
            "acceptance criteria",
        }

        return [
            signal
            for signal in dimension_signals
            if str(signal).casefold()
            in functional_signals
        ]

    # Accessibility coverage can use explicit
    # accessibility signals already detected by the
    # applicability engine.
    if name == "keyboard accessibility":

        accessibility_signals = {
            "keyboard",
            "keyboard navigation",
            "keyboard accessible",
            "keyboard accessibility",
            "navigate using the keyboard",
            "navigate with the keyboard",
        }

        return [
            signal
            for signal in dimension_signals
            if str(signal).casefold()
            in accessibility_signals
        ]

    if name == "screen reader accessibility":

        accessibility_signals = {
            "screen reader",
            "assistive technology",
            "aria",
            "wcag",
        }

        return [
            signal
            for signal in dimension_signals
            if str(signal).casefold()
            in accessibility_signals
        ]

    if name == "browser compatibility":

        compatibility_signals = {
            "browser compatibility",
            "chrome",
            "firefox",
            "edge",
            "safari",
        }

        return [
            signal
            for signal in dimension_signals
            if str(signal).casefold()
            in compatibility_signals
        ]

    if name == "mobile or device compatibility":

        compatibility_signals = {
            "mobile device",
            "tablet",
            "responsive design",
            "device compatibility",
        }

        return [
            signal
            for signal in dimension_signals
            if str(signal).casefold()
            in compatibility_signals
        ]

    # Boundary coverage can use explicit boundary
    # evidence such as "up to", "at least", etc.
    if name == "minimum boundary":

        minimum_signals = {
            "minimum",
            "at least",
            "minimum length",
            "minimum value",
            "minimum size",
            "no less than",
            "not less than",
            "greater than or equal to",
        }

        return [
            signal
            for signal in dimension_signals
            if str(signal).casefold()
            in minimum_signals
        ]

    if name == "maximum boundary":

        maximum_signals = {
            "maximum",
            "at most",
            "up to",
            "no more than",
            "not more than",
            "maximum length",
            "maximum value",
            "maximum size",
            "character limit",
        }

        return [
            signal
            for signal in dimension_signals
            if str(signal).casefold()
            in maximum_signals
        ]

    if name == "exact boundary":

        exact_signals = {
            "exactly",
            "exact length",
            "exact value",
            "exact number",
        }

        return [
            signal
            for signal in dimension_signals
            if str(signal).casefold()
            in exact_signals
        ]

    # Keep this explicit so we don't accidentally use
    # unrelated signals in future coverage areas.
    return [
        signal
        for signal in dimension_signals
        if str(signal).casefold()
        in normalized_dimension_signals
        and str(signal).casefold()
        in {
            str(value).casefold()
            for value in coverage_area.get(
                "signals",
                [],
            )
        }
    ]


# ==================================================
# Identify QA Input Areas
# ==================================================

def identify_qa_input_areas(
    requirement: str,
    coverage_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Identify situations where the requirement indicates
    a testing concern but does not provide enough policy
    information to invent a precise expectation.

    This function does NOT add unsupported scenarios.
    """

    normalized_requirement = (
        normalize_requirement_text(
            requirement
        )
    )

    areas = []

    # --------------------------------------------------
    # Session
    # --------------------------------------------------

    if any(
        signal_matches(
            normalized_requirement,
            signal,
        )
        for signal in [
            "session",
            "remember me",
            "concurrent sessions",
        ]
    ):

        if not any(
            signal_matches(
                normalized_requirement,
                signal,
            )
            for signal in [
                "session expiration",
                "session timeout",
                "expires after",
                "expire after",
                "expires in",
            ]
        ):

            areas.append(
                {
                    "area": "Session expiration policy",
                    "status": "QA_INPUT",
                    "reason": (
                        "Session-related behavior is mentioned, "
                        "but an explicit expiration policy is not provided."
                    ),
                    "must_not_invent": True,
                }
            )

    # --------------------------------------------------
    # Concurrent Sessions
    # --------------------------------------------------

    if signal_matches(
        normalized_requirement,
        "concurrent sessions",
    ):

        if not any(
            signal_matches(
                normalized_requirement,
                signal,
            )
            for signal in [
                "one session",
                "single session",
                "multiple sessions",
                "maximum sessions",
                "concurrent session limit",
            ]
        ):

            areas.append(
                {
                    "area": "Concurrent session policy",
                    "status": "QA_INPUT",
                    "reason": (
                        "Concurrent-session behavior is referenced "
                        "without defining the allowed session policy."
                    ),
                    "must_not_invent": True,
                }
            )

    # --------------------------------------------------
    # Performance
    # --------------------------------------------------

    if any(
        signal_matches(
            normalized_requirement,
            signal,
        )
        for signal in [
            "performance",
            "response time",
            "latency",
            "concurrent users",
            "concurrent requests",
        ]
    ):

        has_explicit_target = any(
            re.search(
                pattern,
                normalized_requirement,
            )
            for pattern in [
                r"\b\d+\s*(?:milliseconds|ms)\b",
                r"\b\d+\s*(?:seconds|second|s)\b",
                r"\b\d+\s+concurrent users\b",
                r"\b\d+\s+concurrent requests\b",
                r"\b\d+\s+requests per second\b",
            ]
        )

        if not has_explicit_target:

            areas.append(
                {
                    "area": "Performance acceptance criteria",
                    "status": "QA_INPUT",
                    "reason": (
                        "Performance is referenced, but a measurable "
                        "acceptance target is not explicitly provided."
                    ),
                    "must_not_invent": True,
                }
            )

    # --------------------------------------------------
    # Security
    # --------------------------------------------------

    if any(
        signal_matches(
            normalized_requirement,
            signal,
        )
        for signal in [
            "security",
            "secure",
            "authentication",
            "authorization",
            "password",
            "session",
        ]
    ):

        security_policy_signals = [
            "rate limit",
            "rate limiting",
            "lockout",
            "password expiration",
            "password history",
            "mfa",
            "multi-factor",
            "2fa",
        ]

        explicitly_defined_security_policy = any(
            signal_matches(
                normalized_requirement,
                signal,
            )
            for signal in security_policy_signals
        )

        if not explicitly_defined_security_policy:

            areas.append(
                {
                    "area": "Additional security policy",
                    "status": "QA_INPUT",
                    "reason": (
                        "Security-sensitive behavior is present, but "
                        "additional security policies are not explicitly defined."
                    ),
                    "must_not_invent": True,
                }
            )

    return areas


# ==================================================
# Coverage Summary
# ==================================================

def summarize_coverage(
    coverage_plan: dict[str, Any],
) -> dict[str, Any]:
    """
    Produce a concise summary of the coverage plan.
    """

    coverage_items = coverage_plan.get(
        "coverage_items",
        [],
    )

    qa_input_areas = coverage_plan.get(
        "qa_input_areas",
        [],
    )

    dimensions = set()

    for item in coverage_items:

        if not isinstance(
            item,
            dict,
        ):
            continue

        dimension = item.get(
            "dimension"
        )

        if dimension:
            dimensions.add(
                str(dimension)
            )

    return {
        "coverage_item_count": len(
            coverage_items
        ),
        "qa_input_count": len(
            qa_input_areas
        ),
        "module_count": len(
            coverage_plan.get(
                "detected_modules",
                [],
            )
        ),
        "dimensions": sorted(
            dimensions
        ),
    }


# ==================================================
# Planner Rules
# ==================================================

def _planner_rules() -> dict[str, bool]:
    return {
        "requirement_is_authority": True,
        "knowledge_is_guidance": True,
        "knowledge_cannot_create_requirement": True,
        "coverage_requires_requirement_evidence": True,
        "module_detection_is_descriptive_only": True,
        "unsupported_scenarios_must_not_be_generated": True,
        "missing_information_must_be_qa_input": True,
        "performance_targets_must_not_be_invented": True,
        "security_policies_must_not_be_invented": True,
        "authentication_policies_must_not_be_invented": True,
        "api_behavior_must_not_be_invented": True,
        "accessibility_requirements_must_not_be_invented": True,
        "traceability_required": True,
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