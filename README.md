# AI Test Case Generator — Standalone

A focused standalone version of the AI Testing Tools project. It contains only the AI Test Case Generator workflow plus Azure DevOps publishing.

## Included

- Requirement / user story input and document upload
- Requirement analysis and decomposition
- Requirement-bound, coverage-driven test-case generation
- Functional, negative/error, boundary/input, state, security, performance, API/integration and accessibility coverage when supported by the supplied requirement
- Knowledge library + applicability rules
- Coverage planner, inventory and mapping
- Uploaded test-case template analysis
- Preset output formats for new projects
- `Requires QA Input` for missing authoritative information
- No invented requirements
- Coverage audit and traceability
- Duplicate / overlap analysis
- Editable generated test cases
- Scrollable results table for large sets
- Excel export
- Regenerate
- Azure DevOps connection and publishing module-wise / user-story-wise

## Not included

- Dashboard
- Saved Test Cases module
- Local/Supabase test-case persistence
- Documents management page
- Templates management page
- Reports page
- Coverage management page
- Version/history UI
- Save Test Cases button

## Requirements

- Windows 10/11
- Python 3.12+
- Node.js 20+
- A Gemini API key (or configure Ollama)
- Azure DevOps PAT only if DevOps publishing is needed

## 1. Backend setup (PowerShell)

```powershell
cd .\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
code .env
```

Put your Gemini key in `.env`:

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=YOUR_KEY_HERE
GEMINI_MODEL=gemini-3.5-flash-lite
```

Start the API:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Health check: http://127.0.0.1:8000/

## 2. Frontend setup (new PowerShell window)

```powershell
cd .\frontend
npm install
npm run dev
```

Open: http://localhost:3000

Optional frontend API override:

```env
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

## Template behavior

For a new project, upload the customer's Excel/CSV template once. The analyzer extracts its columns, field metadata and allowed values. The selected structure is kept in the browser for that project/device.

For subsequent generations, use the **Test Case Table Format** dropdown to choose a preset or the previously uploaded project template. The AI does not impose a fixed format when a custom template is selected.

## Azure DevOps

Open **DevOps Integration** below the generated results and enter:

- Organization
- Project
- Personal Access Token
- User Story ID
- Business Module

Use **Test Connection**, then **Send Reviewed Cases to DevOps**.

The PAT is sent to the local backend for the request and is not saved by this application.

## Important security note

Do not commit `.env` or Azure DevOps PATs to source control.
