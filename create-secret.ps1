<#
.SYNOPSIS
    Creates the piwatch login Secret directly on your cluster - your password never
    lands on disk (no deploy/secret.yaml file) and never appears in chat/with an AI
    assistant. The signing key ("secret") is generated automatically at random.

.USAGE
    cd path\to\piwatch
    $env:KUBECONFIG = "$HOME\.kube\your-cluster-config"
    .\create-secret.ps1
#>

$ErrorActionPreference = "Stop"

if (-not $env:KUBECONFIG) {
    $env:KUBECONFIG = "$HOME\.kube\config"
}

Write-Host "== Creating piwatch login Secret ==" -ForegroundColor Cyan
$Password = Read-Host "Dashboard login password (choose freely)" -AsSecureString
$PlainPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
)

# Random 64-char hex string for token signing - no manual value needed.
# RNGCryptoServiceProvider instead of RandomNumberGenerator.Fill(), because Windows
# PowerShell 5.1 (.NET Framework) doesn't know the static Fill() method (.NET Core+ only).
$Bytes = New-Object byte[] 32
$Rng = New-Object Security.Cryptography.RNGCryptoServiceProvider
$Rng.GetBytes($Bytes)
$Rng.Dispose()
$SigningSecret = -join ($Bytes | ForEach-Object { $_.ToString("x2") })

kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - | Out-Null

kubectl create secret generic piwatch `
    --namespace monitoring `
    --from-literal=password=$PlainPassword `
    --from-literal=secret=$SigningSecret `
    --dry-run=client -o yaml | kubectl apply -f -

if ($LASTEXITCODE -ne 0) { throw "kubectl apply failed" }

# Delete immediately, don't leave it hanging around in memory/history.
Remove-Variable PlainPassword, Password, SigningSecret, Bytes -ErrorAction SilentlyContinue

Write-Host "== Secret 'piwatch' created/updated in namespace 'monitoring'. ==" -ForegroundColor Green
Write-Host "Next step: roll out piwatch (see README.md)."
