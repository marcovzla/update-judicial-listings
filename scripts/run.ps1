#Requires -Version 5.1

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectCommand = 'judicial-listings'

# This script lives under <skill-root>\scripts.
$ScriptDir = $PSScriptRoot
$SkillDir = Split-Path -LiteralPath $ScriptDir -Parent

$UvCommand = Get-Command uv `
    -CommandType Application `
    -ErrorAction SilentlyContinue

if ($null -eq $UvCommand) {
    [Console]::Error.WriteLine(
        'error: this skill requires uv.exe on PATH'
    )
    exit 127
}

if ($env:SKILL_RUNTIME_ROOT) {
    $RuntimeRoot = $env:SKILL_RUNTIME_ROOT
}
elseif ($env:LOCALAPPDATA) {
    $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'codex-skills'
}
else {
    $RuntimeRoot = Join-Path ([IO.Path]::GetTempPath()) 'codex-skills'
}

$SkillRuntimeDir = Join-Path $RuntimeRoot $ProjectCommand
New-Item -ItemType Directory -Force -Path $SkillRuntimeDir | Out-Null

$env:UV_PROJECT_ENVIRONMENT = Join-Path $SkillRuntimeDir 'venv'
$env:UV_CACHE_DIR = Join-Path $SkillRuntimeDir 'uv-cache'

& $UvCommand.Source run `
    --project $SkillDir `
    --locked `
    --no-dev `
    $ProjectCommand `
    @args

exit $LASTEXITCODE
