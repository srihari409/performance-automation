$ErrorActionPreference = "Stop"

$JMETER_BIN = "C:\Tools\apache-jmeter-5.6.3\bin"

# Repo root = one folder above "scripts"
$REPO_ROOT = Split-Path -Parent $PSScriptRoot

# Full paths (so it works even after Push-Location)
$JMX = Join-Path $REPO_ROOT "jmeter\Godrej_HomePage_CI.jmx"
$JTL = Join-Path $REPO_ROOT "results.jtl"

$SLA_CHECKER = "scripts\check_sla_from_jtl.py"

$SLACK_BOT_TOKEN  = $env:SLACK_BOT_TOKEN
$SLACK_CHANNEL_ID = $env:SLACK_CHANNEL_ID

Write-Host "Starting JMeter..."
Push-Location $JMETER_BIN
.\jmeter.bat -n -t $JMX -l $JTL
if ($LASTEXITCODE -ne 0) { throw "JMeter failed with exit code $LASTEXITCODE" }

Pop-Location
Write-Host "JMeter completed."

Write-Host "Running SLA + Summary from JTL..."
python $SLA_CHECKER $JTL $SLACK_BOT_TOKEN $SLACK_CHANNEL_ID "GPL HomePage - CI Run"

Write-Host "Done."
