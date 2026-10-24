# Creates the two desktop buttons. Run once: powershell -ExecutionPolicy Bypass -File scripts\make_bot_shortcuts.ps1
$Root    = Split-Path -Parent $PSScriptRoot
$Desktop = [Environment]::GetFolderPath('Desktop')
$Shell   = New-Object -ComObject WScript.Shell

function New-Button($name, $script) {
    $lnk = $Shell.CreateShortcut((Join-Path $Desktop "$name.lnk"))
    $lnk.TargetPath       = 'powershell.exe'
    $lnk.Arguments        = "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
    $lnk.WorkingDirectory = $Root
    $lnk.Description      = $name
    $lnk.Save()
    Write-Host "Created $Desktop\$name.lnk"
}

New-Button 'START Kalshi bot' (Join-Path $Root 'scripts\start_overs_bot.ps1')
New-Button 'STOP Kalshi bot'  (Join-Path $Root 'scripts\stop_overs_bot.ps1')
