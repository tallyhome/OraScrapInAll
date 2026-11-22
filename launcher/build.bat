@echo off
cd /d "%~dp0"
dotnet publish OraScrap.csproj -c Release -r win-x64 --self-contained false -o publish
if errorlevel 1 exit /b 1
copy /Y publish\OraScrap.exe ..\OraScrap.exe >nul
if exist publish\OraScrap.exe.config copy /Y publish\OraScrap.exe.config ..\OraScrap.exe.config >nul
copy /Y publish\Microsoft.Web.WebView2.Core.dll ..\Microsoft.Web.WebView2.Core.dll >nul
copy /Y publish\Microsoft.Web.WebView2.WinForms.dll ..\Microsoft.Web.WebView2.WinForms.dll >nul
if exist publish\WebView2Loader.dll copy /Y publish\WebView2Loader.dll ..\WebView2Loader.dll >nul
if exist publish\runtimes\win-x64\native\WebView2Loader.dll (
  copy /Y publish\runtimes\win-x64\native\WebView2Loader.dll ..\WebView2Loader.dll >nul
)
echo OK: OraScrap.exe et les DLL WebView2 sont a la racine du projet.
