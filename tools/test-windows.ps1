<#
.SYNOPSIS
Run the test suite on Windows, module by module, under a build's Python.

.DESCRIPTION
    powershell -ExecutionPolicy Bypass -File tools\test-windows.ps1 -Work C:\ohsatv-build\<v> [-Pattern "test_[a-m]*.py"]

-Work is a work folder tools\build-windows.ps1 has filled (its venv and
vendor\). The suite runs from this checkout, from source, finding the
bundled ffmpeg and libiio on PATH and iio.py on PYTHONPATH, as a source run
on Windows does - so it tests the program, not the frozen bundle (the build
does that). One module per Python process, each printed with its time and
result, the output of any that fails in full: the whole suite takes about
half an hour on VM 130, longer than one foreground SSH session can be
trusted with, so run it in parts with -Pattern there (HANDOVER.md,
Windows). Windows PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory = $true)][string]$Work,
    [string]$Pattern = "test_*.py"
)
$Repo = Split-Path -Parent $PSScriptRoot
$VPy = Join-Path $Work "venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) { throw "no venv in ${Work}: run tools\build-windows.ps1 -Work $Work first" }
$env:PATH = "$Work\vendor\ffmpeg;$Work\vendor\libiio;$Work\vendor\hackrf;$env:PATH"
$env:PYTHONPATH = "$Work\vendor\py"
$env:QT_QPA_PLATFORM = "offscreen"
Set-Location $Repo
$failed = 0
$summary = @()
# -like, not -Filter: the file system's wildcards have no [a-m] ranges, and
# the parts are given as ranges.
foreach ($f in Get-ChildItem (Join-Path $Repo "tests") -Filter "test_*.py" | Where-Object { $_.Name -like $Pattern } | Sort-Object Name) {
    $t0 = Get-Date
    # cmd merges stderr so 5.1 does not turn unittest's output into error records.
    $out = (cmd /c "`"$VPy`" -m unittest discover -s tests -p $($f.Name) 2>&1") | Out-String
    $last = ($out.Trim() -split "`n" | Select-Object -Last 1).Trim()
    $line = "$($f.BaseName) ($([int]((Get-Date) - $t0).TotalSeconds) s): $last"
    $summary += $line
    Write-Host "== $line"
    if ($last -notmatch "^OK") { $failed++; Write-Host $out }
}
Write-Host ""
$summary | ForEach-Object { Write-Host $_ }
exit $failed
