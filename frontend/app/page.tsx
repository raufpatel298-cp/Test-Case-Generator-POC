"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { supabase } from "./lib/supabase";

// CKEditor is isolated in a client-only component. This prevents
// Next.js/Turbopack from evaluating CKEditor in the server/module
// environment where document.createElement is unavailable.
const RichTextEditor = dynamic(
  () => import("./RichTextEditor"),
  { ssr: false }
);
import * as XLSX from "xlsx";

type Template = {
  filename: string;
  format: string;
  columns: string[];
  [key: string]: unknown;
};

type CaseRow = Record<string, unknown>;

type Result = {
  test_cases: CaseRow[];
  assumptions: unknown[];
  qa_input_items?: unknown[];
  coverage_analysis: Record<string, unknown>;
  template: Template;
};

const API =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

const PRESETS: Record<string, Template> = {
  standard: {
    filename: "Standard QA",
    format: "standard",
    columns: [
      "TC ID",
      "Requirement ID",
      "Test Scenario",
      "Preconditions",
      "Test Steps",
      "Expected Result",
      "Priority",
      "Test Type",
    ],
  },
  devops: {
    filename: "Azure DevOps Test Case",
    format: "azure-devops",
    columns: [
      "ID",
      "Work Item",
      "Test Case Title",
      "Preconditions",
      "Steps",
      "Expected Result",
      "Priority",
      "Test Type",
    ],
  },
  bdd: {
    filename: "BDD",
    format: "bdd",
    columns: [
      "Scenario",
      "Given",
      "When",
      "Then",
      "Requirement ID",
      "Priority",
    ],
  },
  minimal: {
    filename: "Minimal QA",
    format: "minimal",
    columns: [
      "Test Case",
      "Requirement",
      "Steps",
      "Expected Result",
    ],
  },
};

function valueFrom(row: CaseRow, column: string): unknown {
  const exact = row[column];

  if (exact !== undefined) {
    return exact;
  }

  const wanted = column
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");

  const found = Object.entries(row).find(
    ([k]) =>
      k.toLowerCase().replace(/[^a-z0-9]/g, "") === wanted
  );

  return found?.[1] ?? "";
}

function setValue(
  row: CaseRow,
  column: string,
  value: string
): CaseRow {
  const key = Object.keys(row).find(
    (k) =>
      k.toLowerCase().replace(/[^a-z0-9]/g, "") ===
      column.toLowerCase().replace(/[^a-z0-9]/g, "")
  );

  return {
    ...row,
    [key || column]: value,
  };
}

function titleOf(row: CaseRow) {
  return String(
    valueFrom(row, "Test Scenario") ||
      valueFrom(row, "Test Case Title") ||
      valueFrom(row, "Scenario") ||
      valueFrom(row, "Test Case") ||
      valueFrom(row, "title") ||
      ""
  );
}

function displayCaseId(row: CaseRow, index: number, idColumn: string) {
  const raw = normalizeQaText(valueFrom(row, idColumn));

  // The generator may occasionally place the scenario/title into the ID
  // field. Never expose that as the ID in the review UI.
  if (/^(?:TC|TEST[-_ ]?CASE)[-_ ]?\d+$/i.test(raw)) {
    return raw.replace(/[-_ ]/g, "").toUpperCase();
  }

  return `TC${String(index + 1).padStart(3, "0")}`;
}

function moduleOf(coverage: Record<string, unknown>) {
  const text = JSON.stringify(coverage).toLowerCase();

  if (text.includes("admission")) return "Admission";
  if (text.includes("appointment")) return "Appointment";
  if (text.includes("patient")) return "Patient";
  if (text.includes("login") || text.includes("authentication")) {
    return "Login";
  }

  return "General";
}

function plainTextToHtml(value: string) {
  const escaped = value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

  return escaped
    .split(/\r?\n/)
    .map((line) => `<p>${line || "<br>"}</p>`)
    .join("");
}

function htmlToPlainText(value: string) {
  if (!value) return "";
  if (typeof window === "undefined") {
    return value.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  }

  const parser = new DOMParser();
  const doc = parser.parseFromString(value, "text/html");
  return (doc.body.textContent || "")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .trim();
}

// Editing helper: unlike htmlToPlainText(), this intentionally does not trim
// or collapse whitespace. Controlled textareas must preserve a user's spaces
// while they are typing (including a trailing space before the next word).
function htmlToPlainTextForEditing(value: string) {
  if (!value) return "";

  if (typeof window === "undefined") {
    return value
      .replace(/<[^>]*>/g, "")
      .replace(/\u00a0/g, " ");
  }

  const parser = new DOMParser();
  const doc = parser.parseFromString(value, "text/html");
  return (doc.body.textContent || "").replace(/\u00a0/g, " ");
}

function normalizeRichHtml(value: string) {
  return value
    .replace(/\r?\n/g, "")
    .replace(/>\s+</g, "><")
    .trim();
}

function stripLeadingNumber(value: string) {
  const normalized = normalizeRichHtml(value);

  if (/<[a-z][\s\S]*>/i.test(normalized)) {
    return normalized.replace(
      /^<p>\s*(?:step\s*)?\d+[.)-]?\s*/i,
      "<p>"
    );
  }

  return normalized
    .replace(/^\s*(?:step\s*)?\d+[.)-]?\s*/i, "")
    .trim();
}

// Editing-only helper: remove a generated step number without trimming
// the user's actual text. This is important because getSteps/getExpectedResults
// are called again after every keystroke in controlled textareas.
function stripLeadingNumberForEditing(value: string) {
  const text = htmlToPlainTextForEditing(value);

  return text.replace(
    /^(?:step\s*)?\d+[.)-][ \t]*/i,
    ""
  );
}

function isRichHtml(value: string) {
  return /<(?:p|strong|b|em|i|u|a|ul|ol|li|blockquote|table|br)(?:\s|>)/i.test(value);
}

function normalizeQaText(value: unknown): string {
  return String(value ?? "")
    .replace(/\s+/g, " ")
    .trim();
}

function isGenuineQaInput(value: unknown): boolean {
  const text = normalizeQaText(value);
  if (!text) return false;

  const lower = text.toLowerCase();

  // These are generator/audit messages, not user-facing QA questions.
  const internalPatterns = [
    "test case ",
    "ai step/result repair",
    "deterministic coverage",
    "coverage target",
    "generation batch",
    "generation refill",
    "final step/result validation",
    "final qa-input cleanup",
    "unjustified qa-input marker",
    "used 'medium' as neutral",
    "used \"medium\" as neutral",
    "did not receive a usable priority",
    "aligned test case",
    "routine missing outcomes",
    "qa-input marker in priority",
    "validation aligned",
    "coverage planning",
    "semantic similarity",
    "duplicate",
    "normalization",
  ];

  if (
    internalPatterns.some((pattern) =>
      lower.includes(pattern)
    )
  ) {
    return false;
  }

  // A real QA item should describe a missing/uncertain requirement,
  // rule, behavior, threshold, permission, message, or policy.
  const genuinePatterns = [
    "requires qa input",
    "qa input required",
    "need clarification",
    "needs clarification",
    "not specified",
    "not defined",
    "undefined",
    "unspecified",
    "unclear",
    "ambiguous",
    "clarify",
    "confirm whether",
    "confirm the",
    "qa should confirm",
    "qa must confirm",
    "business rule",
    "validation rule",
    "error message",
    "permission",
    "role",
    "threshold",
    "limit",
    "timeout",
    "retry",
    "session expiration",
    "lockout",
    "password expiration",
    "reset link expiration",
    "performance threshold",
  ];

  return genuinePatterns.some((pattern) =>
    lower.includes(pattern)
  );
}

function getQaInputItems(result: Result | null): string[] {
  if (!result) return [];

  // v24+ backend: use the dedicated QA-input collection.
  if (Array.isArray(result.qa_input_items)) {
    return Array.from(
      new Set(
        result.qa_input_items
          .map(normalizeQaText)
          .filter(isGenuineQaInput)
      )
    );
  }

  // Backward-compatible fallback for older backend responses.
  return Array.from(
    new Set(
      (result.assumptions || [])
        .map(normalizeQaText)
        .filter(isGenuineQaInput)
    )
  );
}

export default function Home() {
  const router = useRouter();

  const [authLoading, setAuthLoading] = useState(true);
  const [userEmail, setUserEmail] = useState("");

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [fileName, setFileName] = useState("");
  const [supportingFiles, setSupportingFiles] = useState<string[]>([]);
  const [supportingContextByFile, setSupportingContextByFile] = useState<Record<string, string>>({});
  const [templateMode, setTemplateMode] = useState("standard");
  const [template, setTemplate] = useState<Template>(
    PRESETS.standard
  );
  const [templateSavePromptOpen, setTemplateSavePromptOpen] = useState(false);
  const [hasProjectTemplate, setHasProjectTemplate] = useState(false);
  const [importedTemplateReady, setImportedTemplateReady] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const inputCardRef = useRef<HTMLDivElement | null>(null);
  const [inputCardHeight, setInputCardHeight] = useState(0);
  const [editingMode, setEditingMode] = useState<"single" | "all" | null>(null);
  const [editingCaseIndex, setEditingCaseIndex] = useState<number | null>(null);
  const [editSnapshot, setEditSnapshot] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const element = inputCardRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;

    const updateHeight = () => {
      setInputCardHeight(
        Math.ceil(element.getBoundingClientRect().height)
      );
    };

    updateHeight();

    const observer = new ResizeObserver(updateHeight);
    observer.observe(element);

    return () => observer.disconnect();
  }, [result, templateMode]);

  // Success/info messages are temporary UI feedback.
  // They automatically disappear after a short period so they do not
  // remain over the generated test cases.
  useEffect(() => {
    if (!message) return;

    const timer = window.setTimeout(() => {
      setMessage("");
    }, 3500);

    return () => window.clearTimeout(timer);
  }, [message]);

  useEffect(() => {
    if (!error) return;

    const timer = window.setTimeout(() => {
      setError("");
    }, 5000);

    return () => window.clearTimeout(timer);
  }, [error]);
  const [devopsOpen, setDevopsOpen] = useState(false);
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [devopsOrg, setDevopsOrg] = useState("");
  const [devopsProject, setDevopsProject] = useState("");
  const [devopsToken, setDevopsToken] = useState("");
  const [storyId, setStoryId] = useState("");
  const [module, setModule] = useState("");
  const [testingDevOps, setTestingDevOps] = useState(false);
  const [sendingDevOps, setSendingDevOps] = useState(false);

  const requirementFile = useRef<HTMLInputElement>(null);
  const supportingFile = useRef<HTMLInputElement>(null);
  const templateFile = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let mounted = true;

    async function checkAuth() {
      const {
        data: { session },
      } = await supabase.auth.getSession();

      if (!mounted) return;

      if (!session) {
        router.replace("/login");
        return;
      }

      setUserEmail(session.user.email ?? "");
      setAuthLoading(false);
    }

    checkAuth();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        if (!mounted) return;

        if (!session) {
          router.replace("/login");
          return;
        }

        setUserEmail(session.user.email ?? "");
        setAuthLoading(false);
      }
    );

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, [router]);

  useEffect(() => {
    const saved =
      localStorage.getItem("tcg-project-template") ||
      // Backward compatibility: templates saved by the previous POC version
      // remain available as Project Template.
      localStorage.getItem("tcg-template");

    if (saved) {
      try {
        const t = JSON.parse(saved) as Template;
        if (t && Array.isArray(t.columns) && t.columns.length) {
          setTemplate(t);
          setTemplateMode("project");
          setHasProjectTemplate(true);
        }
      } catch {}
    }

    setProjectName(
      localStorage.getItem("tcg-project") || ""
    );
  }, []);

  useEffect(() => {
    localStorage.setItem("tcg-project", projectName);
  }, [projectName]);

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  const cases = result?.test_cases || [];
  const coverage = (result?.coverage_analysis || {}) as {
    ambiguous_requirements: string[];
    uncovered_requirements: string[];
    [key: string]: unknown;
  };

  const qaInputItems = useMemo(
    () => getQaInputItems(result),
    [result]
  );

  const stats = useMemo(
    () => ({
      total: cases.length,
      ambiguous: Array.isArray(
        coverage.ambiguous_requirements
      )
        ? coverage.ambiguous_requirements.length
        : 0,
      uncovered: Array.isArray(
        coverage.uncovered_requirements
      )
        ? coverage.uncovered_requirements.length
        : 0,
      // Do not count internal generator assumptions/audit messages.
      // QA Input is only the dedicated, genuine QA-question list.
      qa: qaInputItems.length,
    }),
    [cases, coverage, qaInputItems]
  );

  async function uploadRequirement(file: File) {
    setError("");
    setMessage("Reading requirement…");

    const fd = new FormData();
    fd.append("file", file);

    const r = await fetch(
      `${API}/api/requirements/extract`,
      {
        method: "POST",
        body: fd,
      }
    );

    const data = await r.json();

    if (!data.success) {
      throw new Error(
        data.error || "Could not read requirement."
      );
    }

    setDescription(plainTextToHtml(data.text));
    setFileName(file.name);
    setMessage(`Loaded ${file.name}`);
  }

  async function uploadSupportingFiles(files: FileList | File[]) {
    const selected = Array.from(files);
    if (!selected.length) return;

    setError("");
    setMessage(`Analyzing ${selected.length} supporting input${selected.length === 1 ? "" : "s"}…`);

    const fd = new FormData();
    selected.forEach((file) => fd.append("files", file));

    try {
      const r = await fetch(
        `${API}/api/requirements/extract-many`,
        {
          method: "POST",
          body: fd,
        }
      );

      const data = await r.json();

      if (!data.success) {
        throw new Error(
          data.error || "Could not read supporting inputs."
        );
      }

      const extracted = Array.isArray(data.files) ? data.files : [];
      const names = extracted
        .map((item: { filename?: string }) => item.filename || "")
        .filter(Boolean);

      const contextParts = extracted
        .map((item: { filename?: string; text?: string; type?: string }) => {
          const filename = item.filename || "Supporting input";
          const kind = item.type || "document";
          const text = String(item.text || "").trim();

          return [
            `===== SUPPORTING QA INPUT: ${filename} =====`,
            `Source type: ${kind}`,
            text || "[No readable content extracted.]",
            `===== END SUPPORTING QA INPUT: ${filename} =====`,
          ].join("\n");
        })
        .join("\n\n");

      setSupportingFiles((current) => {
        const merged = [...current, ...names];
        return Array.from(new Set(merged));
      });

      setSupportingContextByFile((current) => {
        const next = { ...current };
        extracted.forEach((item: { filename?: string; text?: string; type?: string }) => {
          const filename = item.filename || "Supporting input";
          const kind = item.type || "document";
          const text = String(item.text || "").trim();

          next[filename] = [
            `===== SUPPORTING QA INPUT: ${filename} =====`,
            `Source type: ${kind}`,
            text || "[No readable content extracted.]",
            `===== END SUPPORTING QA INPUT: ${filename} =====`,
          ].join("\n");
        });
        return next;
      });

      setMessage(
        `${names.length} supporting input${names.length === 1 ? "" : "s"} added.`
      );
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Could not read supporting inputs."
      );
    }
  }

  function removeSupportingFile(filename: string) {
    setSupportingFiles((current) =>
      current.filter((name) => name !== filename)
    );

    setSupportingContextByFile((current) => {
      const next = { ...current };
      delete next[filename];
      return next;
    });

    setMessage(`${filename} removed from supporting inputs.`);
  }

  const supportingContext = useMemo(
    () => Object.values(supportingContextByFile).join("\n\n"),
    [supportingContextByFile]
  );

  async function uploadTemplate(file: File) {
    setError("");
    setMessage("Analyzing template…");

    const fd = new FormData();
    fd.append("file", file);

    const r = await fetch(
      `${API}/api/templates/analyze`,
      {
        method: "POST",
        body: fd,
      }
    );

    const data = await r.json();

    if (!data.success) {
      throw new Error(
        data.error || "Could not analyze template."
      );
    }

    // Import Template is intentionally temporary until the user explicitly
    // chooses whether to save it as the Project Template.
    setTemplate(data.template);
    setTemplateMode("import");
    setImportedTemplateReady(true);
    setTemplateSavePromptOpen(true);

    setMessage(
      `Template validated: ${
        data.template.columns?.length || 0
      } columns`
    );
  }

  function saveImportedTemplateAsProject() {
    localStorage.setItem(
      "tcg-project-template",
      JSON.stringify(template)
    );
    setHasProjectTemplate(true);

    // Remove the legacy key so there is only one authoritative saved
    // Project Template going forward.
    localStorage.removeItem("tcg-template");

    setTemplateMode("project");
    setTemplateSavePromptOpen(false);
    setMessage("Template saved as Project Template.");
  }

  function useImportedTemplateOnly() {
    // The template has already been validated by /api/templates/analyze.
    // "No" means do not persist it, not "do not use it".
    setImportedTemplateReady(true);
    setTemplateMode("import");
    setTemplateSavePromptOpen(false);
    setMessage("Imported template will be used only for this generation.");
  }

  function selectTemplateMode(value: string) {
    setTemplateMode(value);

    if (value !== "import") {
      setImportedTemplateReady(false);
    }

    if (PRESETS[value]) {
      setTemplate(PRESETS[value]);
      return;
    }

    if (value === "project") {
      const saved =
        localStorage.getItem("tcg-project-template") ||
        localStorage.getItem("tcg-template");

      if (!saved) {
        setError("No Project Template has been saved yet.");
        setTemplateMode("standard");
        setTemplate(PRESETS.standard);
        return;
      }

      try {
        const savedTemplate = JSON.parse(saved) as Template;

        if (
          !savedTemplate ||
          !Array.isArray(savedTemplate.columns) ||
          !savedTemplate.columns.length
        ) {
          throw new Error("Saved Project Template is invalid.");
        }

        setTemplate(savedTemplate);
        setError("");
        setMessage("Project Template loaded.");
      } catch (e) {
        setError(
          e instanceof Error
            ? e.message
            : "Could not load the saved Project Template."
        );
        setTemplateMode("standard");
        setTemplate(PRESETS.standard);
      }

      return;
    }

    if (value === "import") {
      // Import requires a new file. The upload button becomes visible below.
      setError("");
      return;
    }
  }

  async function generate() {
    if (templateMode === "import" && !importedTemplateReady) {
      setError(
        "Please upload and validate a template before generating test cases."
      );
      return;
    }

    const primaryRequirement = htmlToPlainText(description).trim();

    if (!primaryRequirement) {
      setError(
        "Enter a requirement or upload a requirement file first."
      );
      return;
    }

    setBusy(true);
    setError("");
    setMessage(
      "Analyzing all supplied inputs and building deep coverage…"
    );

    try {
      const sourceSections = [
        title.trim()
          ? `USER STORY TITLE:\n${title.trim()}`
          : "",
        `PRIMARY USER STORY / REQUIREMENT DESIGN:\n${primaryRequirement}`,
        supportingContext
          ? `ADDITIONAL QA INPUTS:\n${supportingContext}`
          : "",
        `GENERATION RULES:
- Use every supplied source that is relevant: requirements, user stories, acceptance criteria, business rules, UI/UX evidence, API specifications and the QA knowledge library.
- Treat explicit supplied requirements as authoritative.
- Use supporting documents to clarify and expand coverage, but never invent an unrelated requirement.
- Identify features, roles, workflows, validations, business rules, expected behavior, errors, permissions, performance-sensitive operations and security risks only when supported by the supplied material.
- Generate deep coverage across applicable positive, negative, boundary, validation, workflow/state, permission, UI/UX, API/integration, performance and security dimensions.
- Every test case must remain traceable to a supplied requirement, behavior, rule, acceptance criterion, UI/API behavior, or a clearly derived behavior.
- If information is missing or ambiguous, do not silently invent it. Flag it for QA input or ambiguity review.
- Use the selected test-case template as the source of truth for output columns, terminology and format.
- Generate Priority as part of test-case creation when the selected template provides a Priority field.`,
      ]
        .filter(Boolean)
        .join("\n\n");

      const r = await fetch(
        `${API}/api/ai/generate`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            requirement: sourceSections,
            template,
            source_context: supportingContext,
          }),
        }
      );

      const data = await r.json();

      if (!data.success) {
        throw new Error(
          data.error || "Generation failed."
        );
      }

      setResult(data);
      setEditingMode(null);
      setEditingCaseIndex(null);

      setMessage(
        `Generated ${
          data.test_cases?.length || 0
        } deep, requirement-bound test cases.`
      );

      setModule(
        moduleOf(data.coverage_analysis || {})
      );
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Generation failed."
      );
    } finally {
      setBusy(false);
    }
  }

  function editCell(
    index: number,
    column: string,
    value: string
  ) {
    if (!result) return;

    const next = [...result.test_cases];

    next[index] = setValue(
      next[index],
      column,
      value
    );

    setResult({
      ...result,
      test_cases: next,
    });
  }

  function removeCase(index: number) {
    if (!result) return;

    setResult({
      ...result,
      test_cases: result.test_cases.filter(
        (_, i) => i !== index
      ),
    });
  }

  function beginEditing(index?: number) {
    if (!result) return;

    setEditSnapshot(
      JSON.parse(JSON.stringify(result)) as Result
    );

    if (typeof index === "number") {
      setEditingMode("single");
      setEditingCaseIndex(index);
    } else {
      setEditingMode("all");
      setEditingCaseIndex(null);
    }
  }

  function cancelEditing() {
    if (editSnapshot) {
      setResult(editSnapshot);
    }

    setEditingMode(null);
    setEditingCaseIndex(null);
    setEditSnapshot(null);
    setMessage("Changes discarded.");
  }

  function saveChanges() {
    setEditingMode(null);
    setEditingCaseIndex(null);
    setEditSnapshot(null);
    setMessage("Changes saved to this current POC session.");
  }

  function saveCaseChanges(index: number) {
    setMessage(
      `Changes saved for ${getCaseLabel(index)} in this current POC session.`
    );

    if (editingMode === "single") {
      setEditingMode(null);
      setEditingCaseIndex(null);
      setEditSnapshot(null);
    }
  }

  function cancelCaseChanges(index: number) {
    setResult((current) => {
      if (!current || !editSnapshot) return current;

      const snapshotRow = editSnapshot.test_cases[index];
      if (!snapshotRow) return current;

      const next = [...current.test_cases];
      next[index] = JSON.parse(
        JSON.stringify(snapshotRow)
      ) as CaseRow;

      return {
        ...current,
        test_cases: next,
      };
    });

    if (editingMode === "single") {
      setEditingMode(null);
      setEditingCaseIndex(null);
      setEditSnapshot(null);
    }

    setMessage(
      `Changes discarded for ${getCaseLabel(index)}.`
    );
  }

  function getCaseLabel(index: number) {
    if (!result?.test_cases[index]) {
      return `test case ${index + 1}`;
    }

    const row = result.test_cases[index];
    const idColumn = getIdColumn();
    const id =
      String(valueFrom(row, idColumn) || "") ||
      `TC${String(index + 1).padStart(3, "0")}`;

    return id;
  }

  function copyCase(row: CaseRow, index: number) {
    const text = template.columns
      .map((column) => `${column}: ${String(valueFrom(row, column) ?? "")}`)
      .join("\n");

    navigator.clipboard
      .writeText(text)
      .then(() => {
        const idColumn =
          template.columns.find(
            (c) =>
              c.toLowerCase() === "tc id" ||
              c.toLowerCase() === "id"
          ) || template.columns[0];

        const id =
          String(valueFrom(row, idColumn) || "") ||
          `TC${String(index + 1).padStart(3, "0")}`;

        setMessage(`Test case ${id} copied to clipboard.`);
      })
      .catch(() => {
        setError("Could not copy the test case to clipboard.");
      });
  }

  function copyAllCases() {
    if (!result || !cases.length) return;

    const text = cases
      .map((row, index) => {
        const heading = `Test Case ${index + 1}`;
        const fields = template.columns
          .map(
            (column) =>
              `${column}: ${String(valueFrom(row, column) ?? "")}`
          )
          .join("\n");

        return `${heading}\n${fields}`;
      })
      .join("\n\n------------------------------\n\n");

    navigator.clipboard
      .writeText(text)
      .then(() =>
        setMessage(`${cases.length} test cases copied to clipboard.`)
      )
      .catch(() =>
        setError("Could not copy the test cases to clipboard.")
      );
  }

  function addCase() {
    if (!result) return;

    const row: CaseRow = {};

    template.columns.forEach((c) => {
      row[c] = "";
    });

    setResult({
      ...result,
      test_cases: [...result.test_cases, row],
    });
  }

  function getSemanticColumn(
    names: string[],
    fallback = ""
  ) {
    return (
      template.columns.find((column) =>
        names.some(
          (name) =>
            column.toLowerCase().replace(/[^a-z0-9]/g, "") ===
            name.toLowerCase().replace(/[^a-z0-9]/g, "")
        )
      ) || fallback
    );
  }

  function isBddTemplate() {
    return (
      templateMode === "bdd" ||
      String(template.format || "").toLowerCase() === "bdd"
    );
  }

  function getBddClause(row: CaseRow, columnName: string) {
    const column = getSemanticColumn([columnName], "");
    return column ? String(valueFrom(row, columnName) ?? "") : "";
  }

  function setBddClause(
    row: CaseRow,
    columnName: string,
    value: string
  ): CaseRow {
    const column = getSemanticColumn([columnName], columnName);
    return setValue(row, column, value);
  }

  function getSteps(row: CaseRow, preserveEmpty = false) {
    const column = getSemanticColumn(
      ["Test Steps", "Steps", "Step", "Actions"],
      ""
    );

    // BDD does not have a native "Steps" column. Treat Given and When as
    // the executable setup/action steps so the common Steps editor remains
    // usable without changing the BDD template itself.
    if (!column && isBddTemplate()) {
      const given = getBddClause(row, "Given");
      const when = getBddClause(row, "When");
      const values = [
        ...given.split(/\r?\n/),
        ...when.split(/\r?\n/),
      ];

      return preserveEmpty
        ? (values.length ? values : [""])
        : values.filter((value) => value.trim());
    }

    const value = column
      ? String(valueFrom(row, column) ?? "")
      : "";

    const values = value
      ? value
          .split(/\r?\n|\s*\|\s*/)
          .map((step) =>
            preserveEmpty
              ? stripLeadingNumberForEditing(step)
              : stripLeadingNumber(step)
          )
      : [];

    return preserveEmpty ? values : values.filter(Boolean);
  }

  function getExpectedResults(row: CaseRow, preserveEmpty = false) {
    const column = getSemanticColumn(
      [
        "Expected Result",
        "Expected Results",
        "Step Expected",
        "Expected",
        "Results",
      ],
      ""
    );

    // BDD uses Then as the expected outcome. Expose it through the common
    // Expected Results editor so add/edit/remove works consistently.
    if (!column && isBddTemplate()) {
      const thenValue = getBddClause(row, "Then");
      const values = thenValue
        .split(/\r?\n/);

      return preserveEmpty
        ? (values.length ? values : [""])
        : values.filter((value) => value.trim());
    }

    const value = column
      ? String(valueFrom(row, column) ?? "")
      : "";

    const values = value
      ? value
          .split(/\r?\n|\s*\|\s*/)
          .map((item) =>
            preserveEmpty
              ? stripLeadingNumberForEditing(item)
              : stripLeadingNumber(item)
          )
      : [];

    return preserveEmpty ? values : values.filter(Boolean);
  }

  function setSteps(index: number, steps: string[]) {
    if (!result) return;

    const column = getSemanticColumn(
      ["Test Steps", "Steps", "Step", "Actions"],
      ""
    );

    const next = [...result.test_cases];

    if (!column && isBddTemplate()) {
      // Keep BDD semantics: first step is Given; any additional executable
      // steps are represented as newline-separated When content.
      let updated = setBddClause(next[index], "Given", steps[0] || "");
      updated = setBddClause(
        updated,
        "When",
        steps.slice(1).join("\n")
      );
      next[index] = updated;
    } else {
      next[index] = setValue(next[index], column || "Test Steps", steps.join("\n"));
    }

    setResult({
      ...result,
      test_cases: next,
    });
  }

  function setExpectedResults(
    index: number,
    expected: string[]
  ) {
    if (!result) return;

    const column = getSemanticColumn(
      [
        "Expected Result",
        "Expected Results",
        "Step Expected",
        "Expected",
        "Results",
      ],
      ""
    );

    const next = [...result.test_cases];

    if (!column && isBddTemplate()) {
      next[index] = setBddClause(
        next[index],
        "Then",
        expected.join("\n")
      );
    } else {
      next[index] = setValue(
        next[index],
        column || "Expected Result",
        expected.join("\n")
      );
    }

    setResult({
      ...result,
      test_cases: next,
    });
  }

  function updateStepAndExpected(
    index: number,
    steps: string[],
    expected: string[]
  ) {
    setResult((current) => {
      if (!current) return current;

      const stepColumn = getSemanticColumn(
        ["Test Steps", "Steps", "Step", "Actions"],
        ""
      );

      const expectedColumn = getSemanticColumn(
        [
          "Expected Result",
          "Expected Results",
          "Step Expected",
          "Expected",
          "Results",
        ],
        ""
      );

      const next = [...current.test_cases];
      let updated = next[index];

      if (!stepColumn && isBddTemplate()) {
        updated = setBddClause(updated, "Given", steps[0] || "");
        updated = setBddClause(
          updated,
          "When",
          steps.slice(1).join("\n")
        );
      } else {
        updated = setValue(
          updated,
          stepColumn || "Test Steps",
          steps.join("\n")
        );
      }

      if (!expectedColumn && isBddTemplate()) {
        updated = setBddClause(updated, "Then", expected.join("\n"));
      } else {
        updated = setValue(
          updated,
          expectedColumn || "Expected Result",
          expected.join("\n")
        );
      }

      next[index] = updated;

      return {
        ...current,
        test_cases: next,
      };
    });
  }

  function addStepAndExpected(index: number) {
    setResult((current) => {
      if (!current) return current;

      const row = current.test_cases[index];
      const steps = getSteps(row, true);
      const expected = getExpectedResults(row, true);

      const stepColumn = getSemanticColumn(
        ["Test Steps", "Steps", "Step", "Actions"],
        ""
      );

      const expectedColumn = getSemanticColumn(
        [
          "Expected Result",
          "Expected Results",
          "Step Expected",
          "Expected",
          "Results",
        ],
        ""
      );

      let updated = row;

      if (!stepColumn && isBddTemplate()) {
        // Preserve Given and append the new editable step to When.
        updated = setBddClause(updated, "Given", steps[0] || "");
        updated = setBddClause(
          updated,
          "When",
          [...steps.slice(1), ""].join("\n")
        );
      } else {
        updated = setValue(
          updated,
          stepColumn || "Test Steps",
          [...steps, ""].join("\n")
        );
      }

      if (!expectedColumn && isBddTemplate()) {
        updated = setBddClause(
          updated,
          "Then",
          [...expected, ""].join("\n")
        );
      } else {
        updated = setValue(
          updated,
          expectedColumn || "Expected Result",
          [...expected, ""].join("\n")
        );
      }

      const next = [...current.test_cases];
      next[index] = updated;

      return {
        ...current,
        test_cases: next,
      };
    });

    setMessage("New step and expected result added.");
  }

  function removeStep(index: number, stepIndex: number) {
    setResult((current) => {
      if (!current) return current;

      const row = current.test_cases[index];
      const steps = getSteps(row, true);

      if (stepIndex >= steps.length) return current;

      steps.splice(stepIndex, 1);

      const stepColumn = getSemanticColumn(
        ["Test Steps", "Steps", "Step", "Actions"],
        ""
      );

      let updated = row;
      if (!stepColumn && isBddTemplate()) {
        updated = setBddClause(updated, "Given", steps[0] || "");
        updated = setBddClause(
          updated,
          "When",
          steps.slice(1).join("\n")
        );
      } else {
        updated = setValue(
          updated,
          stepColumn || "Test Steps",
          steps.join("\n")
        );
      }

      const next = [...current.test_cases];
      next[index] = updated;

      return {
        ...current,
        test_cases: next,
      };
    });

    setMessage("Step removed.");
  }

  function removeExpected(index: number, expectedIndex: number) {
    setResult((current) => {
      if (!current) return current;

      const row = current.test_cases[index];
      const expected = getExpectedResults(row, true);
      expected.splice(expectedIndex, 1);

      const expectedColumn = getSemanticColumn(
        [
          "Expected Result",
          "Expected Results",
          "Step Expected",
          "Expected",
          "Results",
        ],
        ""
      );

      const next = [...current.test_cases];
      next[index] = !expectedColumn && isBddTemplate()
        ? setBddClause(row, "Then", expected.join("\n"))
        : setValue(
            row,
            expectedColumn || "Expected Result",
            expected.join("\n")
          );

      return {
        ...current,
        test_cases: next,
      };
    });

    setMessage("Expected result removed.");
  }

  function updateStep(
    index: number,
    stepIndex: number,
    value: string
  ) {
    setResult((current) => {
      if (!current) return current;

      const row = current.test_cases[index];
      const steps = getSteps(row, true);
      const expected = getExpectedResults(row, true);

      steps[stepIndex] = value;

      const stepColumn = getSemanticColumn(
        ["Test Steps", "Steps", "Step", "Actions"],
        ""
      );

      const expectedColumn = getSemanticColumn(
        [
          "Expected Result",
          "Expected Results",
          "Step Expected",
          "Expected",
          "Results",
        ],
        ""
      );

      let updated = row;

      if (!stepColumn && isBddTemplate()) {
        updated = setBddClause(updated, "Given", steps[0] || "");
        updated = setBddClause(
          updated,
          "When",
          steps.slice(1).join("\n")
        );
      } else {
        updated = setValue(
          updated,
          stepColumn || "Test Steps",
          steps.join("\n")
        );
      }

      if (!expectedColumn && isBddTemplate()) {
        updated = setBddClause(updated, "Then", expected.join("\n"));
      } else {
        updated = setValue(
          updated,
          expectedColumn || "Expected Result",
          expected.join("\n")
        );
      }

      const next = [...current.test_cases];
      next[index] = updated;

      return {
        ...current,
        test_cases: next,
      };
    });
  }

  function updateExpected(
    index: number,
    expectedIndex: number,
    value: string
  ) {
    setResult((current) => {
      if (!current) return current;

      const row = current.test_cases[index];
      const steps = getSteps(row, true);
      const expected = getExpectedResults(row, true);

      expected[expectedIndex] = value;

      const stepColumn = getSemanticColumn(
        ["Test Steps", "Steps", "Step", "Actions"],
        ""
      );

      const expectedColumn = getSemanticColumn(
        [
          "Expected Result",
          "Expected Results",
          "Step Expected",
          "Expected",
          "Results",
        ],
        ""
      );

      let updated = row;

      if (!stepColumn && isBddTemplate()) {
        updated = setBddClause(updated, "Given", steps[0] || "");
        updated = setBddClause(
          updated,
          "When",
          steps.slice(1).join("\n")
        );
      } else {
        updated = setValue(
          updated,
          stepColumn || "Test Steps",
          steps.join("\n")
        );
      }

      if (!expectedColumn && isBddTemplate()) {
        updated = setBddClause(updated, "Then", expected.join("\n"));
      } else {
        updated = setValue(
          updated,
          expectedColumn || "Expected Result",
          expected.join("\n")
        );
      }

      const next = [...current.test_cases];
      next[index] = updated;

      return {
        ...current,
        test_cases: next,
      };
    });
  }

  function getDescriptionColumn() {
    return getSemanticColumn(
      ["Description", "Test Description", "Scenario Description"],
      ""
    );
  }

  function getPreconditionColumn() {
    return getSemanticColumn(
      ["Preconditions", "Precondition", "Prerequisites"],
      ""
    );
  }

  function getTitleColumn() {
    return getSemanticColumn(
      [
        "Test Scenario",
        "Test Case Title",
        "Scenario",
        "Test Case",
        "Title",
      ],
      template.columns[1] || template.columns[0]
    );
  }

  function getIdColumn() {
    return getSemanticColumn(
      ["TC ID", "ID", "Test Case ID"],
      template.columns[0]
    );
  }

  function getDisplayFields(row: CaseRow) {
    const hidden = new Set(
      [
        getIdColumn(),
        getTitleColumn(),
        getDescriptionColumn(),
        getPreconditionColumn(),
        getSemanticColumn(
          ["Test Steps", "Steps", "Step", "Actions"],
          ""
        ),
        getSemanticColumn(
          [
            "Expected Result",
            "Expected Results",
            "Step Expected",
            "Expected",
            "Results",
          ],
          ""
        ),
      ].filter(Boolean)
    );

    return template.columns.filter((column) => !hidden.has(column));
  }

  function displayLabel(column: string) {
    const normalized = column
      .toLowerCase()
      .replace(/[^a-z0-9]/g, "");

    if (normalized === "testscenario") return "Description";
    if (normalized === "teststeps") return "Steps";
    if (normalized === "expectedresult") return "Expected Results";

    return column;
  }

  function getAzureDevOpsImportRows() {
    if (!result) return [];

    const rows: Record<string, unknown>[] = [];

    cases.forEach((row) => {
      const caseTitle = titleOf(row) || "Untitled test case";
      const rawSteps = String(
        valueFrom(row, "Test Steps") ||
          valueFrom(row, "Steps") ||
          ""
      ).trim();

      const rawExpected = String(
        valueFrom(row, "Expected Result") ||
          valueFrom(row, "Step Expected") ||
          ""
      ).trim();

      const stepLines = rawSteps
        ? rawSteps
            .split(/\r?\n|\s*\|\s*/)
            .map((step) =>
              step
                .replace(/^\s*(?:step\s*)?\d+[.)-]?\s*/i, "")
                .trim()
            )
            .filter(Boolean)
        : [""];

      const expectedLines = rawExpected
        ? rawExpected
            .split(/\r?\n|\s*\|\s*/)
            .map((item) =>
              item
                .replace(/^\s*(?:step\s*)?\d+[.)-]?\s*/i, "")
                .trim()
            )
            .filter(Boolean)
        : [""];

      stepLines.forEach((action, index) => {
        rows.push({
          ID: "",
          "Work Item Type": "Test Case",
          Title: caseTitle,
          "Test Step": index + 1,
          "Step Action": action,
          "Step Expected":
            expectedLines.length === stepLines.length
              ? expectedLines[index] || ""
              : expectedLines[0] || "",
          "Area Path": "",
          "Assigned To": "",
          State: "Design",
          "User Story ID": storyId,
          "Business Module": module || "General",
        });
      });
    });

    return rows;
  }

  function exportExcel() {
    if (!result) return;

    const exportRows = result.test_cases.map((r) => {
      const stepColumn = template.columns.find((column) =>
        ["test steps", "steps", "test step", "step", "actions"].includes(
          String(column).trim().toLowerCase()
        )
      );

      const expectedColumn = template.columns.find((column) =>
        [
          "expected result",
          "expected results",
          "expectedresult",
          "step expected",
          "expected",
          "results",
        ].includes(String(column).trim().toLowerCase())
      );

      // IMPORTANT: getSteps/getExpectedResults expect a CaseRow, not a
      // raw cell value. Passing the raw value was the reason Excel had
      // blank Steps and Expected Result cells.
      const steps = getSteps(r);
      const expectedResults = getExpectedResults(r);

      const numberedSteps = steps
        .map((step, stepIndex) => `${stepIndex + 1}. ${step}`)
        .join("\n");

      // Keep the same 1:1 step/result relationship shown in the POC.
      const numberedExpectedResults = steps
        .map((_, stepIndex) => {
          const expected = expectedResults[stepIndex] || "Requires QA Input";
          return `${stepIndex + 1}. ${expected}`;
        })
        .join("\n");

      const row: Record<string, string | number> = {};

      // Export ONLY the selected template columns.
      // No extra Test Case ID / Step Count / Expected Result Count columns.
      template.columns.forEach((column) => {
        if (stepColumn && column === stepColumn) {
          row[column] = numberedSteps;
        } else if (expectedColumn && column === expectedColumn) {
          row[column] = numberedExpectedResults;
        } else {
          row[column] = String(valueFrom(r, column) ?? "");
        }
      });

      return row;
    });

    const ws = XLSX.utils.json_to_sheet(exportRows, {
      header: [...template.columns],
    });

    const range = XLSX.utils.decode_range(ws["!ref"] || "A1:A1");

    for (let rowIndex = range.s.r; rowIndex <= range.e.r; rowIndex++) {
      for (let colIndex = range.s.c; colIndex <= range.e.c; colIndex++) {
        const cellAddress = XLSX.utils.encode_cell({
          r: rowIndex,
          c: colIndex,
        });

        if (!ws[cellAddress]) continue;

        ws[cellAddress].s = {
          alignment: {
            vertical: "top",
            wrapText: true,
          },
        };

        if (rowIndex === 0) {
          ws[cellAddress].s = {
            font: {
              bold: true,
              color: "FFFFFF",
            },
            fill: {
              fgColor: {
                rgb: "6B35E8",
              },
            },
            alignment: {
              vertical: "center",
              horizontal: "center",
              wrapText: true,
            },
          };
        }
      }
    }

    // Size columns according to their semantic purpose while preserving
    // the exact selected template column order.
    ws["!cols"] = template.columns.map((column) => {
      const normalized = String(column).trim().toLowerCase();

      if (
        normalized === "test steps" ||
        normalized === "steps" ||
        normalized === "test step" ||
        normalized === "step" ||
        normalized === "actions" ||
        normalized === "expected result" ||
        normalized === "expected results" ||
        normalized === "expectedresult" ||
        normalized === "step expected" ||
        normalized === "expected" ||
        normalized === "results"
      ) {
        return { wch: 58 };
      }

      if (
        normalized.includes("scenario") ||
        normalized.includes("title")
      ) {
        return { wch: 60 };
      }

      if (normalized.includes("precondition")) {
        return { wch: 32 };
      }

      return { wch: 22 };
    });

    ws["!rows"] = [
      { hpt: 28 },
      ...exportRows.map(() => ({ hpt: 110 })),
    ];
    ws["!autofilter"] = { ref: ws["!ref"] || "A1:A1" };
    ws["!freeze"] = { xSplit: 0, ySplit: 1 };

    const wb = XLSX.utils.book_new();

    XLSX.utils.book_append_sheet(
      wb,
      ws,
      "Test Cases"
    );

    XLSX.writeFile(
      wb,
      `${(
        title || "AI-Test-Cases"
      ).replace(/[^a-z0-9]+/gi, "-")}.xlsx`
    );

    setMessage(
      `Excel exported successfully with ${exportRows.length} test cases.`
    );
  }

  function closeDevOpsModal() {
    setDevopsOpen(false);
    setDevopsOrg("");
    setDevopsProject("");
    setDevopsToken("");
    setStoryId("");
    setModule("");
    setTestingDevOps(false);
    setSendingDevOps(false);
    setError("");
    setMessage("");
  }

  async function testDevOps() {
    setTestingDevOps(true);
    setError("");

    try {
      const r = await fetch(
        `${API}/api/devops/test-connection`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            organization: devopsOrg,
            project: devopsProject,
            personal_access_token: devopsToken,
          }),
        }
      );

      const d = await r.json();

      if (!d.success) {
        throw new Error(d.error || "DevOps connection failed.");
      }

      setMessage("Azure DevOps connection successful.");
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "DevOps connection failed."
      );
    } finally {
      setTestingDevOps(false);
    }
  }

  async function sendDevOps() {
    if (!result || !cases.length) return;

    setSendingDevOps(true);
    setError("");

    try {
      const r = await fetch(
        `${API}/api/devops/create-test-cases`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            config: {
              organization: devopsOrg,
              project: devopsProject,
              personal_access_token: devopsToken,
            },
            test_cases: cases,
            module_name: module,
            user_story_id: storyId,
          }),
        }
      );

      const d = await r.json();

      if (!d.success && !d.created_count) {
        throw new Error(
          d.error ||
            d.failed?.[0]?.error ||
            "Could not create DevOps test cases."
        );
      }

      setMessage(
        `DevOps: created ${d.created_count || 0}, failed ${d.failed_count || 0}.`
      );
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "DevOps publishing failed."
      );
    } finally {
      setSendingDevOps(false);
    }
  }

  if (authLoading) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <div className="auth-brand">
            <div className="auth-brand-mark">
              ✦
            </div>

            <div>
              <div className="auth-brand-main">
                AI Testing
              </div>

              <div className="auth-brand-sub">
                TOOLS
              </div>
            </div>
          </div>

          <div className="auth-heading">
            <h1>Loading...</h1>
            <p>Checking your account</p>
          </div>
        </section>
      </main>
    );
  }

  return (
    <div className="app-shell">
        <style>{`
          /* Keep both workspace cards aligned to the same row height on desktop.
             The generated-results pane keeps its own vertical scroll so a large
             result set does not make the input card a different height. */
          .main {
            overflow-x: hidden !important;
          }

          .sidebar {
            overflow-y: hidden !important;
          }

          .workspace {
            min-height: 0 !important;
            align-items: stretch !important;
          }

          .workspace > .card {
            min-width: 0;
            box-sizing: border-box;
            align-self: stretch;
          }

          .workspace.workspace-generated {
            align-items: stretch !important;
          }

          /* The left card owns the row height. Keep the grid row from being
             enlarged by the generated cases, then give the right card the
             exact measured left-card height. Its test-case list is the only
             scrolling area. */
          .workspace.workspace-generated {
            align-items: start !important;
          }

          .workspace.workspace-generated .input-card,
          .workspace.workspace-generated .result-card {
            box-sizing: border-box;
            min-width: 0;
            min-height: 0;
            align-self: start;
          }

          .workspace.workspace-generated .input-card {
            position: static;
            overflow: visible;
            height: auto;
            max-height: none;
            overscroll-behavior: auto;
            scrollbar-gutter: auto;
          }

          .workspace.workspace-generated .result-card {
            display: flex;
            flex-direction: column;
            height: var(--input-card-height, auto);
            max-height: var(--input-card-height, none);
            overflow: hidden !important;
            overscroll-behavior: auto;
          }

          /* Only the generated test-case list scrolls. The header and metrics
             remain visible at the top of the right card. */
          .workspace.workspace-generated .result-card .tc-list {
            min-height: 0;
            height: 0;
            flex: 1 1 auto;
            overflow-y: auto;
            overflow-x: hidden;
            overscroll-behavior: contain;
            scrollbar-gutter: stable;
          }

          .input-card::before {
            display: none !important;
          }

          /* Keep clickable metric cards visually identical to the two
             non-clickable metric cards. This is UI-only; the click behavior
             remains unchanged. */
          .metrics .coverage-metric-button {
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-width: 0;
            padding: 14px;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            background: #fff !important;
            color: inherit;
            cursor: pointer;
            text-align: left;
            font: inherit;
            appearance: none;
            -webkit-appearance: none;
          }

          .metrics .coverage-metric-button:hover {
            border-color: #d8cffc;
            box-shadow: 0 4px 12px rgba(99, 53, 217, 0.08);
            transform: translateY(-1px);
          }

          .coverage-metric-button:focus-visible {
            outline: 2px solid #7c3aed;
            outline-offset: 2px;
          }

          .coverage-modal-backdrop,
          .devops-modal-backdrop {
            position: fixed;
            inset: 0;
            z-index: 1000;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
            background: rgba(15, 23, 42, 0.48);
            backdrop-filter: blur(3px);
          }

          .coverage-modal,
          .devops-modal {
            width: min(980px, 100%);
            max-height: min(86vh, 860px);
            overflow: hidden;
            border: 1px solid #e4e5ef;
            border-radius: 18px;
            background: #fff;
            box-shadow: 0 24px 70px rgba(15, 23, 42, 0.22);
            display: flex;
            flex-direction: column;
          }

          .coverage-modal-header,
          .devops-modal-header {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            padding: 20px 22px;
            border-bottom: 1px solid #ececf4;
          }

          .coverage-modal-title,
          .devops-modal-title {
            display: grid;
            gap: 5px;
          }

          .coverage-modal-title b,
          .devops-modal-title b {
            color: #17204b;
            font-size: 17px;
          }

          .coverage-modal-title span,
          .devops-modal-title span {
            color: #66708f;
            font-size: 12px;
            line-height: 1.45;
          }

          .modal-close {
            width: 34px;
            height: 34px;
            flex: 0 0 34px;
            border: 1px solid #e3e4ed;
            border-radius: 9px;
            background: #fff;
            color: #475467;
            font-size: 18px;
            cursor: pointer;
          }

          .modal-close:hover {
            background: #f8f7ff;
            color: #6335d9;
          }

          .coverage-modal-body,
          .devops-modal-body {
            min-height: 0;
            overflow-y: auto;
            padding: 18px 22px 22px;
          }

          .coverage-modal-body .coverage-review-v2 {
            margin-top: 0;
            border: 0;
            box-shadow: none;
            background: transparent;
            padding: 0;
          }

          .coverage-modal-body .coverage-review-v2-header {
            margin-bottom: 14px;
          }

          .devops-modal-body {
            background: #fbfbfe;
          }

          .devops-body {
            display: flex;
            flex-direction: column;
            gap: 16px;
          }

          .devops-mode {
            display: flex;
            align-items: flex-start;
            gap: 4px;
            padding: 2px 0 0;
          }

          .devops-mode label {
            flex: 0 0 auto;
            margin-top: 2px;
            color: #24305f;
            font-size: 12px;
            font-weight: 700;
            line-height: 1.4;
            white-space: nowrap;
          }

          .devops-mode small {
            max-width: none;
            color: #66708f;
            font-size: 12px;
            line-height: 1.45;
            white-space: nowrap;
          }

          .devops-fields {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
          }

          .devops-fields input {
            width: 100%;
            min-width: 0;
            box-sizing: border-box;
            min-height: 42px;
          }

          .devops-actions {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
          }

          .devops-actions button {
            margin: 0;
          }

          .devops-import-note {
            margin: -2px 0 0;
            color: #17204b;
            font-size: 12px;
            line-height: 1.5;
          }

          .devops-body > small {
            margin-top: -8px;
            color: #7b84a0;
            font-size: 10px;
            line-height: 1.45;
          }


          @media (max-width: 820px) {
            .coverage-modal-backdrop,
            .devops-modal-backdrop {
              align-items: flex-end;
              padding: 10px;
            }

            .coverage-modal,
            .devops-modal {
              width: 100%;
              max-height: 92vh;
              border-radius: 16px 16px 10px 10px;
            }

            .devops-mode {
              gap: 10px;
            }

            .devops-fields {
              grid-template-columns: 1fr;
            }

            .devops-actions {
              align-items: stretch;
            }

            .devops-actions button {
              width: 100%;
            }

            .workspace.workspace-generated .input-card,
            .workspace.workspace-generated .result-card {
              height: auto;
              max-height: none;
              align-self: stretch;
            }

            .workspace.workspace-generated .input-card {
              position: static;
              overflow: visible;
            }

            .workspace.workspace-generated .result-card {
              display: block;
              overflow: visible !important;
            }

            .workspace.workspace-generated .result-card .tc-list {
              height: auto;
              max-height: none;
              overflow: visible;
            }
          }

          .coverage-review-v2 {
            margin-top: 24px;
            border: 1px solid #e5e7eb;
            border-radius: 16px;
            background: #fbfbfe;
            padding: 20px;
            box-shadow: 0 4px 14px rgba(31, 41, 78, 0.05);
          }

          .coverage-review-v2-header {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 18px;
            margin-bottom: 18px;
          }

          .coverage-review-v2-title {
            display: flex;
            flex-direction: column;
            gap: 5px;
          }

          .coverage-review-v2-title b {
            color: #17204b;
            font-size: 16px;
            line-height: 1.25;
          }

          .coverage-review-v2-title span {
            color: #66708f;
            font-size: 12px;
            line-height: 1.45;
          }

          .coverage-review-v2-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: flex-end;
          }

          .coverage-review-v2-chip {
            display: inline-flex;
            align-items: center;
            min-height: 30px;
            padding: 0 10px;
            border: 1px solid #e1dcff;
            border-radius: 999px;
            background: #f4f1ff;
            color: #6335d9;
            font-size: 11px;
            font-weight: 700;
            white-space: nowrap;
          }

          .coverage-review-v2-chip.warning {
            border-color: #f1dfb8;
            background: #fff9ed;
            color: #9a6800;
          }

          .coverage-review-v2-chip.success {
            border-color: #ccebdd;
            background: #f0fbf6;
            color: #1d8a5d;
          }

          .coverage-review-v2-grid {
            display: grid;
            grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
            gap: 14px;
            align-items: start;
          }

          .coverage-review-v2-card {
            min-width: 0;
            border: 1px solid #e4e5ef;
            border-radius: 12px;
            background: #fff;
            padding: 15px;
          }

          .coverage-review-v2-card.qa-card {
            background: #faf8ff;
            border-color: #ddd4ff;
          }

          .coverage-review-v2-card.full {
            grid-column: 1 / -1;
          }

          .coverage-review-v2-card-title {
            color: #17204b;
            font-size: 13px;
            font-weight: 800;
            margin-bottom: 10px;
          }

          .coverage-review-v2-list {
            display: flex;
            flex-direction: column;
            gap: 0;
            max-height: 300px;
            overflow-y: auto;
            padding-right: 4px;
          }

          .coverage-review-v2-item {
            display: grid;
            grid-template-columns: 24px minmax(0, 1fr);
            gap: 9px;
            padding: 9px 0;
            border-bottom: 1px solid #eeeeF5;
            color: #34405f;
            font-size: 11px;
            line-height: 1.45;
          }

          .coverage-review-v2-item:last-child {
            border-bottom: 0;
          }

          .coverage-review-v2-number {
            width: 22px;
            height: 22px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            background: #f0edff;
            color: #6a3be2;
            font-size: 9px;
            font-weight: 800;
          }

          .coverage-review-v2-empty {
            padding: 12px 0;
            color: #7b849e;
            font-size: 11px;
          }

          @media (max-width: 1100px) {
            .coverage-review-v2-grid {
              grid-template-columns: 1fr;
            }

            .coverage-review-v2-card.full {
              grid-column: auto;
            }

            .coverage-review-v2-header {
              flex-direction: column;
            }

            .coverage-review-v2-chips {
              justify-content: flex-start;
            }
          }
        `}</style>
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            ✦
          </div>

          <div>
            <div className="brand-main">
              AI TESTING
            </div>

            <div className="brand-sub">
              TOOLS
            </div>
          </div>
        </div>

        <div className="top-actions">
          {userEmail && (
            <span className="user-pill">
              {userEmail}
            </span>
          )}

          <button
            type="button"
            className="secondary"
            onClick={handleSignOut}
          >
            Sign Out
          </button>

          <span className="mail">
            ✉
          </span>
        </div>
      </header>

      <aside className="sidebar">
        <div className="side-item active">
          <span>▣</span>
          AI Test Case Generator
        </div>

        <div
          className="side-item"
          onClick={() => setDevopsOpen(true)}
        >
          <span>↗</span>
          DevOps Integration
        </div>

        <div className="side-spacer" />

        <div className="side-note">
          <b>Requirement-bound AI</b>
          <span>
            No invented requirements
          </span>
        </div>
      </aside>

      <main className="main">
        <div className="page-head">
          <div>
            <h1>
              AI Test Case Generator
            </h1>

            <p>
              Generate, review, edit and
              publish requirement-bound test
              cases.
            </p>
          </div>

          <div className="head-badge">
            {result
              ? `${cases.length} test cases`
              : "Ready to generate"}
          </div>
        </div>

        <section
          className={`workspace ${
            result && cases.length > 1 ? "workspace-generated" : ""
          }`}
          style={
            inputCardHeight > 0
              ? ({ "--input-card-height": `${inputCardHeight}px` } as React.CSSProperties)
              : undefined
          }
        >
          <div ref={inputCardRef} className="card input-card">
            <div className="field">
              <label>
                User Story Title
              </label>

              <input
                value={title}
                onChange={(e) =>
                  setTitle(e.target.value)
                }
                placeholder="Enter a descriptive title…"
              />
            </div>

            <div className="field">
              <label>
                User Story / Requirement Design
              </label>

              <RichTextEditor
                value={description}
                onChange={setDescription}
                placeholder="Describe the user story, acceptance criteria, business rules, UI/UX behavior, API behavior or constraints…"
                minHeight={260}
                editorClassName="ckeditor-field--requirement"
              />

              <div className="counter">
                {description.length.toLocaleString()}
                /20,000 characters
              </div>
            </div>

            <div className="field">
              <label>
                Requirement Attachment
              </label>

              <div
                className="dropzone"
                onClick={() =>
                  requirementFile.current?.click()
                }
              >
                <input
                  ref={requirementFile}
                  type="file"
                  hidden
                  accept=".txt,.md,.csv,.xlsx,.docx,.pdf"
                  onChange={(e) =>
                    e.target.files?.[0] &&
                    uploadRequirement(
                      e.target.files[0]
                    ).catch((err) =>
                      setError(err.message)
                    )
                  }
                />

                <div className="upload-icon">
                  ↑
                </div>

                <b>
                  {fileName ||
                    "Upload requirement document"}
                </b>

                <span>
                  TXT, MD, CSV, XLSX, DOCX or
                  PDF
                </span>
              </div>
            </div>

            <div className="field">
              <label>
                Additional QA Inputs <span className="optional-label">(Optional)</span>
              </label>

              <div
                className="dropzone supporting-dropzone"
                onClick={() =>
                  supportingFile.current?.click()
                }
              >
                <input
                  ref={supportingFile}
                  type="file"
                  hidden
                  multiple
                  accept=".txt,.md,.markdown,.csv,.xlsx,.docx,.pdf,.png,.jpg,.jpeg,.webp"
                  onChange={(e) => {
                    if (e.target.files?.length) {
                      uploadSupportingFiles(e.target.files);
                    }
                    e.currentTarget.value = "";
                  }}
                />

                <div className="upload-icon">
                  ＋
                </div>

                <b>
                  Add requirements, acceptance criteria, business rules, UI/UX or API specifications
                </b>

                <span>
                  Multiple files supported · TXT, MD, CSV, XLSX, DOCX, PDF, PNG, JPG or WEBP
                </span>
              </div>

              {supportingFiles.length > 0 && (
                <div className="supporting-file-list">
                  {supportingFiles.map((name) => (
                    <div className="supporting-file-chip" key={name}>
                      <span>{name}</span>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          removeSupportingFile(name);
                        }}
                        aria-label={`Remove ${name}`}
                        title="Remove file"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <small>
                These inputs are optional. When supplied, they are analyzed together with the user story and relevant QA knowledge-library guidance before test cases are generated.
              </small>
            </div>

            <div className="field">
              <label>
                Test Case Table Format
              </label>

              <div className="template-row">
                <select
                  value={templateMode}
                  onChange={(e) =>
                    selectTemplateMode(e.target.value)
                  }
                >
                  <option value="standard">
                    Standard QA
                  </option>

                  <option value="devops">
                    Azure DevOps Test Case
                  </option>

                  <option value="bdd">
                    BDD
                  </option>

                  <option value="minimal">
                    Minimal QA
                  </option>

                  {hasProjectTemplate && (
                    <option value="project">
                      Project Template
                    </option>
                  )}

                  <option value="import">
                    Import Template
                  </option>
                </select>

                {templateMode === "import" && (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() =>
                      templateFile.current?.click()
                    }
                  >
                    Upload Template
                  </button>
                )}

                <input
                  ref={templateFile}
                  type="file"
                  hidden
                  accept=".xlsx,.csv"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.currentTarget.value = "";

                    if (!file) return;

                    uploadTemplate(file).catch((err) =>
                      setError(
                        err instanceof Error
                          ? err.message
                          : "Could not analyze template."
                      )
                    );
                  }}
                />
              </div>

              <small>
                {template.columns.length} output
                columns · {template.filename}
              </small>
            </div>

            <button
              className="primary"
              disabled={busy}
              onClick={generate}
            >
              {busy
                ? "Generating…"
                : "✧ Generate Test Cases"}
            </button>
          </div>

          <div className="card result-card">
            {!result && !busy && (
              <div className="empty">
                <div className="empty-icon">
                  ▤
                </div>

                <b>
                  Generated test cases will
                  appear here
                </b>

                <span>
                  Requirement analysis, coverage
                  and editable cases will be
                  shown after generation.
                </span>
              </div>
            )}

            {busy && (
              <div className="empty">
                <div className="spinner" />

                <b>
                  AI is building
                  coverage-driven cases…
                </b>

                <span>
                  Decomposing requirements,
                  auditing coverage and
                  validating traceability.
                </span>
              </div>
            )}

            {result && (
              <>
                {editingMode ? (
                  <div className="edit-toolbar">
                    <div>
                      <h2>{editingMode === "all" ? "Edit All Test Cases" : "Edit Test Case"}</h2>
                      <span>
                        Review and update the generated cases before publishing.
                      </span>
                    </div>

                    <div className="edit-toolbar-actions">
                      <button
                        type="button"
                        className="secondary"
                        onClick={cancelEditing}
                      >
                        ✕ Cancel
                      </button>

                      <button
                        type="button"
                        className="primary small"
                        onClick={saveChanges}
                      >
                        ▣ Save Changes
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="result-head">
                    <div>
                      <h2>Generated Test Cases</h2>
                      <span>
                        {cases.length} cases · {template.columns.length} columns
                      </span>
                    </div>

                    <div className="result-actions">
                      <button
                        type="button"
                        className="secondary"
                        onClick={copyAllCases}
                        disabled={!cases.length}
                      >
                        ▣ Copy All
                      </button>

                      <button
                        type="button"
                        className="secondary"
                        onClick={() => beginEditing()}
                        disabled={!cases.length}
                      >
                        ✎ Edit All
                      </button>

                      <button
                        type="button"
                        className="primary small"
                        onClick={exportExcel}
                      >
                        ↓ Export
                      </button>
                    </div>
                  </div>
                )}

                {!editingMode && (
                  <div className="metrics">
                    <div>
                      <b>{stats.total}</b>
                      <span>Test cases</span>
                    </div>
                    <div>
                      <b>{stats.uncovered}</b>
                      <span>Uncovered</span>
                    </div>
                    <button
                      type="button"
                      className="coverage-metric-button"
                      onClick={() => setCoverageOpen(true)}
                      aria-label={`Open Coverage and QA Review. ${stats.ambiguous} ambiguous`}
                    >
                      <b>{stats.ambiguous}</b>
                      <span>Ambiguous</span>
                    </button>
                    <button
                      type="button"
                      className="coverage-metric-button"
                      onClick={() => setCoverageOpen(true)}
                      aria-label={`Open Coverage and QA Review. ${stats.qa} QA input`}
                    >
                      <b>{stats.qa}</b>
                      <span>QA input</span>
                    </button>
                  </div>
                )}

                <div className="tc-list">
                  {cases.map((row, i) => {
                    const idColumn = getIdColumn();
                    const titleColumn = getTitleColumn();
                    const descriptionColumn = getDescriptionColumn();
                    const preconditionColumn = getPreconditionColumn();

                    const tcId = displayCaseId(
                      row,
                      i,
                      idColumn
                    );

                    const tcTitle =
                      String(valueFrom(row, titleColumn) || "") ||
                      "Untitled Test Case";

                    const description = descriptionColumn
                      ? String(valueFrom(row, descriptionColumn) ?? "")
                      : "";

                    const preconditions = preconditionColumn
                      ? String(valueFrom(row, preconditionColumn) ?? "")
                      : "";

                    const displayFields = getDisplayFields(row);
                    const isCaseEditing =
                      editingMode === "all" ||
                      (editingMode === "single" &&
                        editingCaseIndex === i);
                    const steps = getSteps(row, isCaseEditing);
                    const expected = getExpectedResults(row, isCaseEditing);

                    return (
                      <article key={`${String(valueFrom(row, getIdColumn()) || "case")}-${i}`} className="tc-card">
                        <div className="tc-header">
                          <div className="tc-title-area">
                            <span className="tc-id">ID: {tcId}</span>

                            {isCaseEditing ? (
                              <input
                                className="tc-title-input"
                                value={tcTitle}
                                onChange={(e) =>
                                  editCell(
                                    i,
                                    titleColumn,
                                    e.target.value
                                  )
                                }
                                placeholder="Test Case Title"
                              />
                            ) : (
                              <h3>{tcTitle}</h3>
                            )}
                          </div>

                          <div className="tc-actions">
                            {!isCaseEditing && (
                              <>
                                <button
                                  type="button"
                                  className="tc-btn"
                                  onClick={() => copyCase(row, i)}
                                >
                                  <svg
                                    width="14"
                                    height="14"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                  >
                                    <rect
                                      x="9"
                                      y="9"
                                      width="13"
                                      height="13"
                                      rx="2"
                                    />
                                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                                  </svg>
                                  Copy
                                </button>

                                <button
                                  type="button"
                                  className="tc-btn"
                                  onClick={() => beginEditing(i)}
                                >
                                  <svg
                                    width="14"
                                    height="14"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                  >
                                    <path d="M12 20h9" />
                                    <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                                  </svg>
                                  Edit
                                </button>
                              </>
                            )}

                            {isCaseEditing && (
                              <button
                                type="button"
                                className="tc-delete-icon"
                                onClick={() => removeCase(i)}
                                title="Delete test case"
                                aria-label={`Delete ${tcId}`}
                              >
                                <svg
                                  width="16"
                                  height="16"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
                                  <polyline points="3 6 5 6 21 6" />
                                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                                  <path d="M10 11v6" />
                                  <path d="M14 11v6" />
                                  <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
                                </svg>
                              </button>
                            )}
                          </div>
                        </div>

                        <div className="tc-body">
                          {descriptionColumn && (
                            <div className="tc-field-group">
                              <label>Description</label>

                              {isCaseEditing ? (
                                <textarea
                                  className="tc-edit-textarea"
                                  value={htmlToPlainTextForEditing(description)}
                                  onChange={(e) =>
                                    editCell(
                                      i,
                                      descriptionColumn,
                                      e.target.value
                                    )
                                  }
                                  placeholder="Enter description..."
                                />
                              ) : (
                                <div className="tc-text-content">
                                  {description ? (
                                    isRichHtml(description) ? (
                                      <div
                                        className="tc-rich-content"
                                        dangerouslySetInnerHTML={{
                                          __html: description,
                                        }}
                                      />
                                    ) : (
                                      description
                                    )
                                  ) : (
                                    <span className="tc-muted">
                                      No description provided.
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          )}

                          {preconditionColumn && (
                            <div className="tc-field-group">
                              <label>Preconditions</label>

                              {isCaseEditing ? (
                                <textarea
                                  className="tc-edit-textarea"
                                  value={htmlToPlainTextForEditing(preconditions)}
                                  onChange={(e) =>
                                    editCell(
                                      i,
                                      preconditionColumn,
                                      e.target.value
                                    )
                                  }
                                  placeholder="Enter preconditions..."
                                />
                              ) : (
                                <div className="tc-text-content">
                                  {preconditions ? (
                                    isRichHtml(preconditions) ? (
                                      <div
                                        className="tc-rich-content"
                                        dangerouslySetInnerHTML={{
                                          __html: preconditions,
                                        }}
                                      />
                                    ) : (
                                      preconditions
                                    )
                                  ) : (
                                    <span className="tc-muted">
                                      No preconditions provided.
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          )}

                          <div className="tc-field-group">
                            <label>Steps</label>

                            {isCaseEditing ? (
                              <div className="tc-repeat-list">
                                {(steps.length ? steps : [""]).map(
                                  (step, stepIndex) => (
                                    <div
                                      className="tc-repeat-row"
                                      key={stepIndex}
                                    >
                                      <span className="tc-step-number">
                                        {stepIndex + 1}.
                                      </span>

                                      <textarea
                                        className="tc-edit-textarea tc-edit-repeat-textarea"
                                        value={isBddTemplate() ? step : htmlToPlainTextForEditing(step)}
                                        onChange={(e) =>
                                          updateStep(
                                            i,
                                            stepIndex,
                                            e.target.value
                                          )
                                        }
                                        placeholder={`Step ${
                                          stepIndex + 1
                                        }`}
                                      />

                                      <button
                                        type="button"
                                        className="tc-row-delete"
                                        onClick={() =>
                                          removeStep(
                                            i,
                                            stepIndex
                                          )
                                        }
                                        title="Delete step"
                                        aria-label={`Delete step ${stepIndex + 1}`}
                                      >
                                        <svg
                                          width="15"
                                          height="15"
                                          viewBox="0 0 24 24"
                                          fill="none"
                                          stroke="currentColor"
                                          strokeWidth="2"
                                          strokeLinecap="round"
                                          strokeLinejoin="round"
                                        >
                                          <polyline points="3 6 5 6 21 6" />
                                          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                                          <path d="M10 11v6" />
                                          <path d="M14 11v6" />
                                        </svg>
                                      </button>
                                    </div>
                                  )
                                )}
                              </div>
                            ) : (
                              <ol className="tc-ordered-list">
                                {(steps.length ? steps : [""]).map(
                                  (step, stepIndex) => (
                                    <li key={stepIndex}>
                                      {isRichHtml(step) ? (
                                        <span
                                          className="tc-rich-inline"
                                          dangerouslySetInnerHTML={{
                                            __html: step,
                                          }}
                                        />
                                      ) : (
                                        step
                                      )}
                                    </li>
                                  )
                                )}
                              </ol>
                            )}
                          </div>

                          <div className="tc-field-group">
                            <label>Expected Results</label>

                            {isCaseEditing ? (
                              <div className="tc-repeat-list">
                                {(expected.length ? expected : [""]).map(
                                  (item, expectedIndex) => (
                                    <div
                                      className="tc-repeat-row"
                                      key={expectedIndex}
                                    >
                                      <span className="tc-step-number">
                                        {expectedIndex + 1}.
                                      </span>

                                      <textarea
                                        className="tc-edit-textarea tc-edit-repeat-textarea"
                                        value={isBddTemplate() ? item : htmlToPlainTextForEditing(item)}
                                        onChange={(e) =>
                                          updateExpected(
                                            i,
                                            expectedIndex,
                                            e.target.value
                                          )
                                        }
                                        placeholder={`Expected result ${
                                          expectedIndex + 1
                                        }`}
                                      />

                                      <button
                                        type="button"
                                        className="tc-row-delete"
                                        onClick={() =>
                                          removeExpected(
                                            i,
                                            expectedIndex
                                          )
                                        }
                                        title="Delete expected result"
                                        aria-label={`Delete expected result ${expectedIndex + 1}`}
                                      >
                                        <svg
                                          width="15"
                                          height="15"
                                          viewBox="0 0 24 24"
                                          fill="none"
                                          stroke="currentColor"
                                          strokeWidth="2"
                                          strokeLinecap="round"
                                          strokeLinejoin="round"
                                        >
                                          <polyline points="3 6 5 6 21 6" />
                                          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                                          <path d="M10 11v6" />
                                          <path d="M14 11v6" />
                                        </svg>
                                      </button>
                                    </div>
                                  )
                                )}
                              </div>
                            ) : (
                              <ol className="tc-ordered-list">
                                {(expected.length ? expected : [""]).map(
                                  (item, expectedIndex) => (
                                    <li key={expectedIndex}>
                                      {isRichHtml(item) ? (
                                        <span
                                          className="tc-rich-inline"
                                          dangerouslySetInnerHTML={{
                                            __html: item,
                                          }}
                                        />
                                      ) : (
                                        item
                                      )}
                                    </li>
                                  )
                                )}
                              </ol>
                            )}
                          </div>

                          {displayFields.map((column) => {
                            const value = String(
                              valueFrom(row, column) ?? ""
                            );

                            return (
                              <div
                                key={column}
                                className="tc-field-group tc-extra-field"
                              >
                                <label>{displayLabel(column)}</label>

                                {isCaseEditing ? (
                                  <textarea
                                    value={value}
                                    onChange={(e) =>
                                      editCell(
                                        i,
                                        column,
                                        e.target.value
                                      )
                                    }
                                    placeholder={`Enter ${displayLabel(
                                      column
                                    ).toLowerCase()}...`}
                                  />
                                ) : (
                                  <div className="tc-text-content">
                                    {value || (
                                      <span className="tc-muted">
                                        No {displayLabel(
                                          column
                                        ).toLowerCase()} provided.
                                      </span>
                                    )}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>

                        {isCaseEditing && (
                          <button
                            type="button"
                            className="add-step-result"
                            onClick={() =>
                              addStepAndExpected(i)
                            }
                          >
                            <span>＋</span>
                            Add Step and Expected Result
                          </button>
                        )}

                        {isCaseEditing && (
                          <div className="tc-edit-footer">
                            <button
                              type="button"
                              className="tc-edit-cancel"
                              onClick={() => cancelCaseChanges(i)}
                            >
                              ✕ Cancel
                            </button>

                            <button
                              type="button"
                              className="tc-edit-save"
                              onClick={() => saveCaseChanges(i)}
                            >
                              ✓ Save Changes
                            </button>
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>

                {editingMode && (
                  <button
                    type="button"
                    className="add-case"
                    onClick={addCase}
                  >
                    ＋ Add test case
                  </button>
                )}
              </>
            )}

          </div>
        </section>

        {coverageOpen && (
          <div
            className="coverage-modal-backdrop"
            role="presentation"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) {
                setCoverageOpen(false);
              }
            }}
          >
            <section
              className="coverage-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="coverage-review-title"
            >
              <div className="coverage-modal-header">
                <div className="coverage-modal-title">
                  <b id="coverage-review-title">Coverage &amp; QA Review</b>
                  <span>
                    AI findings are informational and remain tied to the supplied requirements.
                  </span>
                </div>

                <button
                  type="button"
                  className="modal-close"
                  onClick={() => setCoverageOpen(false)}
                  aria-label="Close Coverage and QA Review"
                >
                  ×
                </button>
              </div>

              <div className="coverage-modal-body">
                <div className="coverage-review-v2">
                  <div className="coverage-review-v2-header">
                    <div className="coverage-review-v2-title">
                      <b>Review findings</b>
                      <span>
                        Use these findings to clarify requirements and review coverage before publishing.
                      </span>
                    </div>

                    <div className="coverage-review-v2-chips">
                      {stats.uncovered > 0 && (
                        <span className="coverage-review-v2-chip warning">
                          ⚠ {stats.uncovered} uncovered
                        </span>
                      )}

                      {stats.ambiguous > 0 && (
                        <span className="coverage-review-v2-chip warning">
                          ⚠ {stats.ambiguous} ambiguous
                        </span>
                      )}

                      {stats.qa > 0 && (
                        <span className="coverage-review-v2-chip">
                          QA Input {stats.qa}
                        </span>
                      )}

                      {stats.uncovered === 0 &&
                        stats.ambiguous === 0 &&
                        stats.qa === 0 && (
                          <span className="coverage-review-v2-chip success">
                            ✓ No outstanding QA flags
                          </span>
                        )}
                    </div>
                  </div>

                  <div className="coverage-review-v2-grid">
                    {stats.ambiguous > 0 && (
                      <div className="coverage-review-v2-card">
                        <div className="coverage-review-v2-card-title">
                          Ambiguous requirements / QA questions
                        </div>

                        <div className="coverage-review-v2-list">
                          {(coverage.ambiguous_requirements || []).map(
                            (item: string, index: number) => (
                              <div
                                className="coverage-review-v2-item"
                                key={`${item}-${index}`}
                              >
                                <span className="coverage-review-v2-number">
                                  {index + 1}
                                </span>
                                <span>{item}</span>
                              </div>
                            )
                          )}
                        </div>
                      </div>
                    )}

                    {stats.qa > 0 && (
                      <div className="coverage-review-v2-card qa-card">
                        <div className="coverage-review-v2-card-title">
                          QA input
                        </div>

                        <div className="coverage-review-v2-list">
                          {qaInputItems.map(
                            (item: string, index: number) => (
                              <div
                                className="coverage-review-v2-item"
                                key={`${item}-${index}`}
                              >
                                <span className="coverage-review-v2-number">
                                  {index + 1}
                                </span>
                                <span>{item}</span>
                              </div>
                            )
                          )}
                        </div>
                      </div>
                    )}

                    {stats.uncovered > 0 && (
                      <div className="coverage-review-v2-card full">
                        <div className="coverage-review-v2-card-title">
                          Uncovered requirements
                        </div>

                        <div className="coverage-review-v2-list">
                          {(coverage.uncovered_requirements || []).map(
                            (item: string, index: number) => (
                              <div
                                className="coverage-review-v2-item"
                                key={`${item}-${index}`}
                              >
                                <span className="coverage-review-v2-number">
                                  {index + 1}
                                </span>
                                <span>{item}</span>
                              </div>
                            )
                          )}
                        </div>
                      </div>
                    )}

                    {stats.uncovered === 0 &&
                      stats.ambiguous === 0 &&
                      stats.qa === 0 && (
                        <div className="coverage-review-v2-card full">
                          <div className="coverage-review-v2-empty">
                            No outstanding coverage or QA review items.
                          </div>
                        </div>
                      )}
                  </div>
                </div>
              </div>
            </section>
          </div>
        )}

        {devopsOpen && (
          <div
            className="devops-modal-backdrop"
            role="presentation"
          >
            <section
              className="devops-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="devops-modal-title"
            >
              <div className="devops-modal-header">
                <div className="devops-modal-title">
                  <b id="devops-modal-title">Azure DevOps Integration</b>
                  <span>
                    Publish reviewed cases module-wise and user-story-wise.
                  </span>
                </div>

                <button
                  type="button"
                  className="modal-close"
                  onClick={closeDevOpsModal}
                  aria-label="Close Azure DevOps Integration"
                >
                  ×
                </button>
              </div>

              <div className="devops-modal-body">
                <div className="devops-body">
                  <div className="devops-mode">
                    <label>Azure DevOps</label>

                    <small>
                      Use your Azure DevOps organization, project and personal access token.
                    </small>
                  </div>

                  <div className="devops-fields">
                    <input
                      placeholder="Organization"
                      value={devopsOrg}
                      onChange={(e) =>
                        setDevopsOrg(e.target.value)
                      }
                    />

                    <input
                      placeholder="Project"
                      value={devopsProject}
                      onChange={(e) =>
                        setDevopsProject(e.target.value)
                      }
                    />

                    <input
                      placeholder="Personal Access Token"
                      type="password"
                      value={devopsToken}
                      onChange={(e) =>
                        setDevopsToken(e.target.value)
                      }
                    />

                    <input
                      placeholder="User Story ID (e.g. 12345)"
                      value={storyId}
                      onChange={(e) =>
                        setStoryId(e.target.value)
                      }
                    />

                    <input
                      placeholder="Business Module (e.g. Login)"
                      value={module}
                      onChange={(e) =>
                        setModule(e.target.value)
                      }
                    />
                  </div>

                  <div className="devops-actions">
                    <button
                      className="secondary"
                      disabled={testingDevOps}
                      onClick={testDevOps}
                    >
                      {testingDevOps
                        ? "Testing…"
                        : "Test Connection"}
                    </button>

                    <button
                      className="primary small"
                      disabled={sendingDevOps || !result}
                      onClick={sendDevOps}
                    >
                      {sendingDevOps
                        ? "Creating…"
                        : "Send Reviewed Cases to DevOps"}
                    </button>
                  </div>

                  <div className="devops-import-note">
                    <b>For this POC:</b> Connect your Azure DevOps project and send the reviewed
                    test cases directly to Azure DevOps. Use <b>Test Connection</b> to verify your
                    details before sending.
                  </div>

                  <small>
                    Credentials are sent only to your local backend for the request and are not stored by this standalone app.
                  </small>
                </div>
              </div>
            </section>
          </div>
        )}

        {templateSavePromptOpen && (
          <div
            className="coverage-modal-backdrop"
            role="presentation"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) {
                useImportedTemplateOnly();
              }
            }}
          >
            <section
              className="coverage-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="template-save-title"
              style={{ maxWidth: 520 }}
            >
              <div className="coverage-modal-header">
                <div className="coverage-modal-title">
                  <b id="template-save-title">
                    Template Imported Successfully
                  </b>
                  <span>
                    Do you want to save this template as Project Template?
                  </span>
                </div>

                <button
                  type="button"
                  className="modal-close"
                  onClick={useImportedTemplateOnly}
                  aria-label="Use imported template without saving"
                >
                  ×
                </button>
              </div>

              <div
                className="coverage-modal-body"
                style={{
                  display: "flex",
                  justifyContent: "flex-end",
                  gap: 10,
                }}
              >
                <button
                  type="button"
                  className="secondary"
                  onClick={useImportedTemplateOnly}
                >
                  No
                </button>

                <button
                  type="button"
                  className="primary small"
                  onClick={saveImportedTemplateAsProject}
                >
                  Yes
                </button>
              </div>
            </section>
          </div>
        )}

        {(message || error) && (
          <div
            className={
              error
                ? "toast error"
                : "toast"
            }
          >
            {error || message}
          </div>
        )}
      </main>
    </div>
  );
}