# Diagnóstico: ¿en qué combinación muere el programa al apagar las luces? (temporal)
$ErrorActionPreference = "Continue"
$installer = Get-ChildItem dist\LucesNavidenas-*-Instalador.exe | Select-Object -First 1
Start-Process $installer.FullName -ArgumentList "/S" -Wait
$exe = Join-Path $env:LOCALAPPDATA "Programs\LucesNavidenas\LucesNavidenas.exe"
$cfgDir = Join-Path $env:APPDATA "LucesNavidenas"
New-Item -ItemType Directory -Force $cfgDir | Out-Null
$casos = @(
  @{ n = "A opciones+apagar (todo como antes)"; o = $true;  c = @{} },
  @{ n = "B apagar sin opciones";               o = $false; c = @{} },
  @{ n = "C opciones+apagar, sin LEDs bandeja"; o = $true;  c = @{ tray_leds = $false } },
  @{ n = "D opciones+apagar, sin balanceo";     o = $true;  c = @{ sway = $false } },
  @{ n = "E opciones+apagar, sin transición";   o = $true;  c = @{ smooth = $false } },
  @{ n = "F opciones abiertas, sin apagar";     o = $true;  c = @{}; noapagar = $true }
)
foreach ($k in $casos) {
  $cfg = @{ power = $true; mode = "alternate"; rate = 0.368; palette = "retro"; sway = $true; burnt = $true;
            broken = $true; size = 1.154; show_options = $false; bg_notice = $false; tray_leds = $true; smooth = $true }
  foreach ($x in $k.c.Keys) { $cfg[$x] = $k.c[$x] }
  $cfg | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $cfgDir "config.json")
  $p = Start-Process $exe -ArgumentList "--oculto" -PassThru
  Start-Sleep -Seconds 5
  if ($k.o) { (Start-Process $exe -ArgumentList "--opciones" -PassThru).WaitForExit(15000) | Out-Null; Start-Sleep -Seconds 3 }
  if (-not $k.noapagar) { (Start-Process $exe -ArgumentList "--alternar" -PassThru).WaitForExit(15000) | Out-Null }
  Start-Sleep -Seconds 12
  if ($p.HasExited) { $r = "MURIÓ 0x{0:X8}" -f $p.ExitCode } else { $r = "vivo"; Stop-Process -Id $p.Id -Force; Start-Sleep 2 }
  Write-Host ("{0,-42} {1}" -f $k.n, $r)
}
