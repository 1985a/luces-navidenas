# Prueba de humo en un Windows real (la usa el flujo de GitHub Actions).
# Instala en silencio, arranca, saca capturas, mide CPU, prueba las órdenes y desinstala.
# Deja capturas y medidas en artefactos\ y falla si algo no responde o queda un error registrado.
$ErrorActionPreference = "Stop"

$out = Join-Path $PWD "artefactos"
New-Item -ItemType Directory -Force $out | Out-Null
$installer = Get-ChildItem dist\LucesNavidenas-*-Instalador.exe | Select-Object -First 1
$dir = Join-Path $env:LOCALAPPDATA "Programs\LucesNavidenas"
$exe = Join-Path $dir "LucesNavidenas.exe"
$cfgDir = Join-Path $env:APPDATA "LucesNavidenas"
$log = Join-Path $env:LOCALAPPDATA "LucesNavidenas\registro.txt"
$report = @()

function Captura($nombre) {
    # Pillow captura con SRCCOPY|CAPTUREBLT: incluye las ventanas por capas (las luces y la nieve)
    python -c "from PIL import ImageGrab; ImageGrab.grab().save(r'$(Join-Path $out "$nombre.png")')"
    if ($LASTEXITCODE -ne 0) { Write-Host "No se pudo capturar $nombre" }
}

function Proceso() { Get-Process LucesNavidenas -ErrorAction SilentlyContinue | Select-Object -First 1 }

function MedirCpu($segundos, $etiqueta) {
    $p = Proceso
    if (-not $p) { throw "El programa no está corriendo ($etiqueta)" }
    $c0 = $p.TotalProcessorTime.TotalSeconds; $t0 = Get-Date
    Start-Sleep -Seconds $segundos
    $p.Refresh()
    $c1 = $p.TotalProcessorTime.TotalSeconds; $t1 = Get-Date
    $pct = 100 * ($c1 - $c0) / ($t1 - $t0).TotalSeconds
    $mem = [math]::Round($p.WorkingSet64 / 1MB)
    $linea = "{0,-45} CPU {1,5:N1} % de un núcleo · memoria {2} MB" -f $etiqueta, $pct, $mem
    Write-Host $linea
    $script:report += $linea
}

function Config($cambios) {
    $base = @{ power = $true; mode = "alternate"; rate = 0.368; palette = "retro"; sway = $true;
               burnt = $true; broken = $true; size = 1.154; show_options = $false; bg_notice = $false;
               effect_on = $false; effect = "nieve"; effect_density = 0.45; tray_leds = $true }
    foreach ($k in $cambios.Keys) { $base[$k] = $cambios[$k] }
    New-Item -ItemType Directory -Force $cfgDir | Out-Null
    $base | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $cfgDir "config.json")
}

function Arrancar($argumentos) {
    Start-Process $exe -ArgumentList $argumentos
    Start-Sleep -Seconds 6
    if (-not (Proceso)) { throw "No arrancó con: $argumentos" }
}

function Salir() {
    Start-Process $exe -ArgumentList "--salir" -Wait
    for ($i = 0; $i -lt 40 -and (Proceso); $i++) { Start-Sleep -Milliseconds 250 }
    if (Proceso) { throw "No se cerró con --salir" }
}

Write-Host "== Instalar $($installer.Name)"
Start-Process $installer.FullName -ArgumentList "/S" -Wait
if (-not (Test-Path $exe)) { throw "No se instaló en $dir" }

Config @{}
Arrancar "--oculto"
Captura "1-luces-usuario"
MedirCpu 20 "Alternando, retro, balanceo (como en Linux)"

Start-Process $exe -ArgumentList "--opciones" -Wait
Start-Sleep -Seconds 4
Captura "2-opciones"
MedirCpu 10 "Con la ventana de opciones abierta"

Start-Process $exe -ArgumentList "--alternar" -Wait
Start-Sleep -Seconds 2
MedirCpu 10 "Luces apagadas"
Start-Process $exe -ArgumentList "--alternar" -Wait
Salir

Config @{ sway = $false }
Arrancar "--oculto"
MedirCpu 20 "Alternando, sin balanceo"
Salir

Config @{ sway = $false; mode = "none" }
Arrancar "--oculto"
MedirCpu 20 "Encendidas fijas (debería dormir)"
Salir

Config @{ sway = $false; palette = "arcoiris" }
Arrancar "--oculto"
Captura "3-arcoiris"
MedirCpu 20 "Arcoíris"
Salir

Config @{ sway = $false; mode = "none"; effect_on = $true; position = "bottom"; bulb = "estrella"; palette = "navidad" }
Arrancar "--oculto"
Start-Sleep -Seconds 3
Captura "4-nieve-abajo"
MedirCpu 20 "Nieve, luces abajo"
Salir

Write-Host "== Una sola copia: dos arranques a la vez"
Config @{}
Start-Process $exe -ArgumentList "--oculto"; Start-Process $exe -ArgumentList "--oculto"
Start-Sleep -Seconds 8
$n = @(Get-Process LucesNavidenas -ErrorAction SilentlyContinue).Count
$report += "Copias tras dos arranques simultáneos: $n"
if ($n -ne 1) { throw "Hay $n copias del programa (debería haber 1)" }
Salir

Write-Host "== Desinstalar"
Start-Process (Join-Path $dir "Desinstalar.exe") -ArgumentList "/S", "_?=$dir" -Wait
Remove-Item -Force (Join-Path $dir "Desinstalar.exe") -ErrorAction SilentlyContinue
$restos = @(Get-ChildItem $dir -Recurse -ErrorAction SilentlyContinue).Count
$report += "Archivos que quedan tras desinstalar: $restos"

$report | Set-Content -Encoding UTF8 (Join-Path $out "medidas.txt")
if (Test-Path $log) {
    Copy-Item $log (Join-Path $out "registro.txt")
    Get-Content $log
    throw "El programa registró errores (ver registro.txt)"
}
Write-Host "Prueba de humo superada"
