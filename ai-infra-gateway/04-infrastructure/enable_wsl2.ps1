# ============================================================
# AI Infra - WSL2 Enable Script (RUN AS ADMINISTRATOR)
# ============================================================
# Diagnosis summary:
#   - HypervisorPresent=TRUE (VT-x is actually running)
#   - WSL binaries already installed (v2.7.3.0, kernel 6.6.114.1-1)
#   - Missing: Virtual Machine Platform Windows feature
#
# How to run:
#   Right-click PowerShell -> Run as Administrator
#   cd C:\Users\admin\Desktop\ai-infra-gateway\scripts
#   .\enable_wsl2.ps1
# ============================================================

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  AI Infra - WSL2 Enable Script" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Enable Virtual Machine Platform
Write-Host "[1/4] Enabling Virtual Machine Platform..." -ForegroundColor Yellow
try {
    $result = Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
    Write-Host "  VirtualMachinePlatform: $($result.RestartNeeded -eq 'Yes' ? 'Requires Reboot' : 'Enabled')" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
    Write-Host "  Trying DISM fallback..." -ForegroundColor Yellow
    dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
}

# Step 2: Enable WSL
Write-Host "[2/4] Enabling Windows Subsystem for Linux..." -ForegroundColor Yellow
try {
    $result = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart
    Write-Host "  WSL: $($result.RestartNeeded -eq 'Yes' ? 'Requires Reboot' : 'Enabled')" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
    dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
}

# Step 3: Set WSL2 as default
Write-Host "[3/4] Setting WSL2 as default version..." -ForegroundColor Yellow
wsl --set-default-version 2 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  WSL2 set as default" -ForegroundColor Green
} else {
    Write-Host "  WARNING: Could not set WSL2 default (may need reboot first)" -ForegroundColor Yellow
}

# Step 4: Check status
Write-Host "[4/4] Checking WSL status..." -ForegroundColor Yellow
wsl --status 2>&1

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  DONE. Reboot required for changes to take effect." -ForegroundColor Cyan
Write-Host "  After reboot, run: wsl --install -d Ubuntu" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan