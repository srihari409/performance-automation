$ErrorActionPreference = "Stop"

$JMETER_BIN = "C:\Tools\apache-jmeter-5.6.3\bin"

# Repo root = one folder above "scripts"
$REPO_ROOT = Split-Path -Parent $PSScriptRoot

$JMX = Join-Path $REPO_ROOT "jmeter\Godrej_HomePage_CI.jmx"
$JTL = Join-Path $REPO_ROOT "results.jtl"

$SLA_CHECKER = Join-Path $REPO_ROOT "scripts\check_sla_from_jtl.py"

Write-Host "Starting JMeter..."
Push-Location $JMETER_BIN
.\jmeter.bat -n -t $JMX -l $JTL
if ($LASTEXITCODE -ne 0) { throw "JMeter failed with exit code $LASTEXITCODE" }
Pop-Location
Write-Host "JMeter completed."

if (-not (Test-Path $JTL)) {
  throw "JTL not found after JMeter run: $JTL"
}

Write-Host "Running SLA + Summary (INFO mode)..."
python $SLA_CHECKER $JTL $env:SLACK_BOT_TOKEN $env:SLACK_CHANNEL_ID "GPL HomePage - CI Info" --annotations

Write-Host "Done."
