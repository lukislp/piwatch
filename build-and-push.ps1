<#
.SYNOPSIS
    Builds piwatch multi-arch (arm64+amd64) and pushes it to your container registry.
    Run this yourself in your own PowerShell (not through an AI coding assistant) - the
    credentials are only passed locally to "docker login", never logged or stored anywhere.

.USAGE
    cd path\to\piwatch
    .\build-and-push.ps1
#>

$ErrorActionPreference = "Stop"

$Registry = "registry.example.com"
$Image    = "$Registry/your-namespace/piwatch:latest"

Write-Host "== Registry login: $Registry ==" -ForegroundColor Cyan
$RegistryUser = Read-Host "Registry username"
$RegistryPass = Read-Host "Registry password/token" -AsSecureString
$PlainPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($RegistryPass)
)

# --password-stdin keeps the password out of the process list/shell history.
$PlainPass | docker login $Registry -u $RegistryUser --password-stdin
if ($LASTEXITCODE -ne 0) { throw "docker login failed" }

# Delete the password variable immediately, don't leave it in memory/history.
Remove-Variable PlainPass, RegistryPass -ErrorAction SilentlyContinue

Write-Host "== Ensuring buildx builder (multiarch) ==" -ForegroundColor Cyan
docker run --privileged --rm tonistiigi/binfmt --install all | Out-Null
$builderExists = docker buildx ls | Select-String "multiarch"
if (-not $builderExists) {
    docker buildx create --use --name multiarch --driver docker-container
} else {
    docker buildx use multiarch
}

Write-Host "== Build + push: $Image ==" -ForegroundColor Cyan
docker buildx build --platform linux/arm64,linux/amd64 -t $Image --push .
if ($LASTEXITCODE -ne 0) { throw "docker buildx build failed" }

# Informational only (no hard failure on findings) - no CI for this repo, so it runs here instead.
Write-Host "== Vulnerability scan (Trivy, informational only) ==" -ForegroundColor Cyan
docker pull $Image | Out-Null
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image --severity HIGH,CRITICAL --format table $Image

Write-Host "== Done. Image is in the registry. ==" -ForegroundColor Green
Write-Host "Next step: create deploy/secret.yaml (see README.md), then roll out with 'kubectl apply -k deploy/'."
