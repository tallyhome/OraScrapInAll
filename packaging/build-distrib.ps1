# Construit un seul fichier OraScrapInAll-<version>.exe
# (installeur + application portable complète).
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $Root "VERSION"))) { throw "VERSION introuvable depuis $Root" }
Set-Location $Root

$Version = (Get-Content VERSION -Raw).Trim()
$Stage = Join-Path $Root "archives\stage\OraScrapInAll"
$OutDir = Join-Path $Root "archives"
$Zip = Join-Path $OutDir "payload-$Version.zip"
$BootstrapExe = Join-Path $OutDir "bootstrap-$Version.exe"
$Final = Join-Path $OutDir "OraScrapInAll-$Version.exe"
$Marker = Join-Path $OutDir "marker.bin"
$Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"

Write-Host "Version $Version"
Write-Host "Racine  $Root"

# Arrête une instance qui verrouillerait les fichiers.
Get-Process OraScrap -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

if (-not (Test-Path "OraScrap.exe")) { throw "OraScrap.exe manquant. Lancez launcher\build.bat d'abord." }
if (-not (Test-Path "runtime\python\pythonw.exe")) { throw "runtime\python manquant." }
if (-not (Test-Path $Csc)) { throw "csc.exe introuvable." }

Write-Host "Preparation du dossier portable..."
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

$copy = {
    param($src, $dst)
    New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
}

& $copy (Join-Path $Root "OraScrap.exe") (Join-Path $Stage "OraScrap.exe")
& $copy (Join-Path $Root "OraScrap.exe.config") (Join-Path $Stage "OraScrap.exe.config")
& $copy (Join-Path $Root "Microsoft.Web.WebView2.Core.dll") (Join-Path $Stage "Microsoft.Web.WebView2.Core.dll")
& $copy (Join-Path $Root "Microsoft.Web.WebView2.WinForms.dll") (Join-Path $Stage "Microsoft.Web.WebView2.WinForms.dll")
& $copy (Join-Path $Root "WebView2Loader.dll") (Join-Path $Stage "WebView2Loader.dll")
& $copy (Join-Path $Root "Lancer.bat") (Join-Path $Stage "Lancer.bat")
& $copy (Join-Path $Root "requirements.txt") (Join-Path $Stage "requirements.txt")
& $copy (Join-Path $Root "VERSION") (Join-Path $Stage "VERSION")
& $copy (Join-Path $Root "app") (Join-Path $Stage "app")
& $copy (Join-Path $Root "scrapers") (Join-Path $Stage "scrapers")
& $copy (Join-Path $Root "runtime\python") (Join-Path $Stage "runtime\python")

# Nettoyage du stage.
Get-ChildItem $Stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $Stage -Recurse -File -Include "*.pyc","*.pyo","*.pdb" | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $Stage "scrapers") -Recurse -Directory -Filter "bin" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Compression..."
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($Stage, $Zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
$zipMb = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
Write-Host "Archive : $zipMb Mo"

Write-Host "Compilation de l'installeur..."
$VersionCs = Join-Path $PSScriptRoot "PackagedVersion.cs"
Set-Content -Path $VersionCs -Value ("static class PackagedVersion { public const string Value = `"" + $Version + "`"; }") -Encoding ASCII
& $Csc /nologo /target:winexe /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll /out:$BootstrapExe (Join-Path $PSScriptRoot "Bootstrap.cs") $VersionCs
if ($LASTEXITCODE -ne 0) { throw "Compilation bootstrap echouee." }

[System.IO.File]::WriteAllBytes($Marker, [System.Text.Encoding]::ASCII.GetBytes("###ORASCRAP_ZIP_PAYLOAD###"))

Write-Host "Assemblage du fichier unique..."
cmd /c "copy /b `"$BootstrapExe`" + `"$Marker`" + `"$Zip`" `"$Final`" >nul"
if (-not (Test-Path $Final)) { throw "Assemblage echoue." }

Remove-Item $BootstrapExe, $Marker, $Zip -Force -ErrorAction SilentlyContinue
# Garde le stage pour debug ; on peut le supprimer :
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue

$finalMb = [math]::Round((Get-Item $Final).Length / 1MB, 1)
Write-Host ""
Write-Host "Pret : $Final"
Write-Host "Taille : $finalMb Mo"
Write-Host "Donnez CE fichier. Un double-clic installe dans %LOCALAPPDATA%\OraScrapInAll et lance l'app."
