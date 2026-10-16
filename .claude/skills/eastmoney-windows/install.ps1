[CmdletBinding()]
param([string]$DestinationRoot)
$ErrorActionPreference='Stop'
$source=[IO.Path]::GetFullPath($PSScriptRoot)
if (-not $DestinationRoot) {
 $codexRoot=if($env:CODEX_HOME){$env:CODEX_HOME}else{Join-Path $env:USERPROFILE '.codex'}
 $DestinationRoot=Join-Path $codexRoot 'skills'
}
$target=Join-Path ([IO.Path]::GetFullPath($DestinationRoot)) 'eastmoney-windows'
if ($source -eq $target -or (Test-Path -LiteralPath $target)) {
 throw 'Target skill already exists. Choose another DestinationRoot or back up the existing skill before installing.'
}
$required=@('SKILL.md','agents\openai.yaml','scripts\eastmoney.py','scripts\windows_core.py','scripts\windows_bridge.py','scripts\windows_host.ps1','scripts\desktop.ps1','scripts\windows_selftest.py','references\windows.md','references\recovery.md','references\simulation-web.md','references\validation.md')
foreach($file in $required){if(-not (Test-Path -LiteralPath (Join-Path $source $file))){throw ('Incomplete skill: '+$file)}}
$files=@(Get-ChildItem -LiteralPath $source -Recurse -File | Where-Object {$_.Extension -in @('.md','.yaml','.py','.ps1') -and $_.FullName -notmatch '[\\/](__pycache__|data)[\\/]'})
$staging=Join-Path $DestinationRoot ('.eastmoney-windows-install-'+[guid]::NewGuid().ToString('N'))
try {
 [void](New-Item -Path $staging -ItemType Directory -Force)
 foreach($file in $files) {
  $relative=$file.FullName.Substring($source.Length).TrimStart([IO.Path]::DirectorySeparatorChar,[IO.Path]::AltDirectorySeparatorChar)
  $destination=Join-Path $staging $relative
  [void](New-Item -ItemType Directory -Force -Path (Split-Path $destination -Parent))
  Copy-Item -LiteralPath $file.FullName -Destination $destination
 }
 Move-Item -LiteralPath $staging -Destination $target
 @{status='installed';skill='eastmoney-windows';path=$target;files=$files.Count} | ConvertTo-Json -Compress
} finally {
 if(Test-Path -LiteralPath $staging){Remove-Item -LiteralPath $staging -Recurse -Force}
}
