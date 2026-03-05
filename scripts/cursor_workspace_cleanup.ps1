# Cursor workspace storage cleanup for this project only.
# Use when Agent causes high memory / lag and workspace state is suspected.
#
# BEFORE RUNNING:
# 1. Fully quit Cursor (File -> Exit; check tray and Task Manager for cursor.exe).
# 2. Run in PowerShell: .\scripts\cursor_workspace_cleanup.ps1
#
# EFFECT: Removes this project's workspaceStorage folder (state.vscdb, caches).
#        Chat/Composer history for THIS project will be lost; other projects unaffected.

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot + "\.."
$projectPath = (Resolve-Path $projectRoot).Path

# Cursor workspaceStorage on Windows
$cursorRoaming = [System.Environment]::GetFolderPath("ApplicationData")
$wsStorage = Join-Path $cursorRoaming "Cursor\User\workspaceStorage"
if (-not (Test-Path $wsStorage)) {
    Write-Host "Cursor workspaceStorage not found at: $wsStorage"
    exit 1
}

# Normalize path for comparison (drive letter, slashes)
$projectPathNorm = $projectPath -replace "\\", "/"
if ($projectPathNorm -match "^([A-Za-z]):") {
    $projectPathNorm = $projectPathNorm -replace "^([A-Za-z]):", { $_.Groups[1].Value.ToLower() + ":" }
}
$projectUri = "file:///" + $projectPathNorm.Replace("\", "/")

Write-Host "Project path (normalized): $projectPathNorm"
Write-Host "Looking for workspace with folder URI like: $projectUri"
Write-Host ""

$found = $null
foreach ($dir in (Get-ChildItem -Path $wsStorage -Directory)) {
    $wsJson = Join-Path $dir.FullName "workspace.json"
    if (Test-Path $wsJson) {
        $content = Get-Content $wsJson -Raw -ErrorAction SilentlyContinue
        if ($content -match "Tenhou_handreading" -or $content -match [regex]::Escape($projectPathNorm) -or $content -match [regex]::Escape($projectPath)) {
            $found = $dir
            break
        }
    }
}

if (-not $found) {
    Write-Host "No workspaceStorage folder found for this project."
    Write-Host "You can list all workspaces with:"
    Write-Host "  Get-ChildItem '$wsStorage' -Directory | ForEach-Object { Get-Content (Join-Path `$_.FullName 'workspace.json') -ErrorAction SilentlyContinue; Write-Host '' }"
    exit 1
}

$targetDir = $found.FullName
Write-Host "Found workspace folder: $targetDir"
$backupDir = $targetDir + ".backup-" + (Get-Date -Format "yyyyMMdd-HHmmss")
Write-Host "Backup will be created at: $backupDir"
Write-Host ""
$confirm = Read-Host "Proceed? Backup folder then DELETE workspace storage. Type YES to continue"
if ($confirm -ne "YES") {
    Write-Host "Aborted."
    exit 0
}

Copy-Item -Path $targetDir -Destination $backupDir -Recurse -Force
Remove-Item -Path $targetDir -Recurse -Force
Write-Host "Done. Workspace storage removed; backup at: $backupDir"
Write-Host "Restart Cursor and open this project again. Use a new Composer/Agent session."
