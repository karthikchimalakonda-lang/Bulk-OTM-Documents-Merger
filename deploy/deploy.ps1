# Build, push and (re)deploy the otm-pod-merge function.
#   $p = @{ AppId = "<application-ocid>"; CompartmentId = "<compartment-ocid>"; Region = "<region-key>" }
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 @p              # code only - keeps the function's configuration
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 @p -SyncConfig  # also merge .env values into the configuration
#
# The function's Configuration (OCI console > Functions > otm-pod-merge > Configuration) is the source of truth.
# A normal deploy never touches it, so values edited in the console are kept. .env is only used:
#   * when the function is created for the first time, and
#   * with -SyncConfig: keys present in .env are added/updated; keys that exist only in the console are kept.
param(
    [string]$Profile = "DEFAULT",
    [Parameter(Mandatory = $true)][string]$AppId,          # Functions application OCID
    [Parameter(Mandatory = $true)][string]$CompartmentId,  # compartment for the container repository
    [Parameter(Mandatory = $true)][string]$Region,         # OCIR region key, e.g. iad, fra, lhr
    [string]$Repo = "otm-pod-merge/otm-pod-merge",
    [string]$FunctionName = "otm-pod-merge",
    [int]$MemoryMb = 2048,
    [int]$TimeoutSeconds = 300,
    [int]$DetachedTimeoutSeconds = 1800,
    [switch]$SyncConfig
)
$ErrorActionPreference = "Stop"
$env:OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING = "True"
$env:SUPPRESS_LABEL_WARNING = "True"
$root = Split-Path -Parent $PSScriptRoot
$fnDir = Join-Path $root "sanitized-source\otm-pod-merge"

function Invoke-Oci { $out = & oci.exe @args --profile $Profile; if ($LASTEXITCODE) { throw "oci $($args[0..2] -join ' ') failed" }; $out }

# Every setting the function reads (see functions/otm-pod-merge/config.py).
$keys = "OTM_BASE_URL","OTM_USERNAME","OTM_PASSWORD",
        "DOC_DEF","OWNER_TYPES","CUSTOMER_ID_FIELD","CUSTOMER_NAME_FIELD","CUSTOMER_FALLBACK_PARTY","LOCAL_TZ","START_FROM",
        "OS_BUCKET","OS_PREFIX",
        "TIME_BUDGET_SECONDS","DETACHED_TIME_BUDGET_SECONDS","MAX_DOCS_PER_RUN","DETACHED_MAX_DOCS","MAX_CHAIN_BATCHES","MAX_DOC_FAILURES",
        "FN_INVOKE_ENDPOINT",
        "GDRIVE_FOLDER_ID","GDRIVE_CREDENTIALS_OBJECT","GDRIVE_IMPERSONATE","GDRIVE_SHARE_ANYONE"

function Read-EnvConfig {
    $values = [ordered]@{}
    foreach ($line in Get-Content (Join-Path $root ".env")) {
        if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)$' -and $keys -contains $Matches[1]) {
            $value = $Matches[2].Trim().Trim("'").Trim('"')
            if ($value) { $values[$Matches[1]] = $value }  # empty = use the code default
        }
    }
    $values
}

$ns = Invoke-Oci os ns get --query data --raw-output
$tag = Get-Date -Format "yyyyMMdd-HHmmss"
$image = "$Region.ocir.io/$ns/${Repo}:$tag"

# 1. Registry repository (private) in the AI compartment
$existing = Invoke-Oci artifacts container repository list --compartment-id $CompartmentId --display-name $Repo --query "data.items[0].id" --raw-output
if (-not $existing) {
    Write-Host "Creating repository $Repo"
    Invoke-Oci artifacts container repository create --compartment-id $CompartmentId --display-name $Repo --is-public false | Out-Null
}

# 2. Build and push
Write-Host "Building $image"
docker build --platform linux/amd64 -t $image $fnDir
if ($LASTEXITCODE) { throw "docker build failed" }
docker push $image
if ($LASTEXITCODE) { throw "docker push failed (run: docker login $Region.ocir.io -u '$ns/<user>' with an auth token)" }

# 3. Create or update the function
$fnId = Invoke-Oci fn function list --application-id $AppId --display-name $FunctionName --query "data[0].id" --raw-output
$configFile = New-TemporaryFile
try {
    if ($fnId) {
        $common = @("fn", "function", "update", "--function-id", $fnId, "--image", $image, "--memory-in-mbs", $MemoryMb,
                    "--timeout-in-seconds", $TimeoutSeconds, "--detached-mode-timeout-in-seconds", $DetachedTimeoutSeconds,
                    "--force", "--wait-for-state", "ACTIVE")
        if ($SyncConfig) {
            # Merge: start from the live configuration, overlay .env
            $live = Invoke-Oci fn function get --function-id $fnId --query "data.config" | ConvertFrom-Json
            $merged = [ordered]@{}
            if ($live) { $live.PSObject.Properties | ForEach-Object { $merged[$_.Name] = $_.Value } }
            $fromEnv = Read-EnvConfig
            foreach ($k in $fromEnv.Keys) { $merged[$k] = $fromEnv[$k] }
            $merged["OS_NAMESPACE"] = $ns
            [IO.File]::WriteAllText($configFile, ($merged | ConvertTo-Json -Compress))
            Write-Host "Updating function $fnId (code + configuration: $($fromEnv.Count) keys from .env merged)"
            Invoke-Oci @common --config "file://$configFile" | Out-Null
        } else {
            Write-Host "Updating function $fnId (code only - configuration unchanged)"
            Invoke-Oci @common | Out-Null
        }
    } else {
        $seed = Read-EnvConfig
        $seed["OS_NAMESPACE"] = $ns
        [IO.File]::WriteAllText($configFile, ($seed | ConvertTo-Json -Compress))
        Write-Host "Creating function $FunctionName (configuration seeded from .env)"
        $fnId = Invoke-Oci fn function create --application-id $AppId --display-name $FunctionName --image $image `
            --memory-in-mbs $MemoryMb --timeout-in-seconds $TimeoutSeconds --detached-mode-timeout-in-seconds $DetachedTimeoutSeconds `
            --config "file://$configFile" --wait-for-state ACTIVE --query "data.id" --raw-output
    }
} finally {
    Remove-Item $configFile -ErrorAction SilentlyContinue
}
Write-Host "Deployed $image"
Write-Host "Function OCID: $fnId"
