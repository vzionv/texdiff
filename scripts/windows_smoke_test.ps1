$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

python -m compileall -q src tests scripts
python -m pytest
texdiff --doctor
texdiff tests\fixtures\old.tex tests\fixtures\new.tex --extractor builtin --pdf-engine reportlab -o windows-smoke.pdf

$bytes = [System.IO.File]::ReadAllBytes((Join-Path $Root "windows-smoke.pdf"))
if ($bytes.Length -lt 1000 -or [Text.Encoding]::ASCII.GetString($bytes[0..4]) -ne "%PDF-") {
    throw "Invalid PDF output"
}
if (-not (Test-Path (Join-Path $Root "windows-smoke.html"))) {
    throw "HTML sidecar was not generated"
}
Write-Host "Windows smoke test passed."
