param([string]$Config = "Release", [switch]$Clean)

# toolchain jak w Brutgen build.ps1
$vs = 'g:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools'
$cmake = "$vs\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
$ninja = "$vs\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"

Import-Module "$vs\Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Enter-VsDevShell -VsInstallPath $vs -Arch amd64 -SkipAutomaticLocation | Out-Null

$root = $PSScriptRoot
$build = Join-Path $root "build"
if ($Clean -and (Test-Path $build)) { Remove-Item -Recurse -Force $build }

& $cmake -S $root -B $build -G Ninja "-DCMAKE_BUILD_TYPE=$Config" "-DCMAKE_MAKE_PROGRAM=$ninja"
if ($LASTEXITCODE -ne 0) { exit 1 }
& $cmake --build $build
exit $LASTEXITCODE
