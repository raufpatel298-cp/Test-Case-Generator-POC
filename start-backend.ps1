Set-Location "$PSScriptRoot\backend"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
& ".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
