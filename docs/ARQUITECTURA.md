# Arquitectura

Un solo script, `luces-navidenas`, en capas de abajo arriba:

| Capa | Qué hay | Depende de |
|---|---|---|
| Lógica pura | `rate_to_interval`, `blink_target`, `continuous_target`, `fade_toward`, `layout_bulbs`, `sway_redraw_interval`, `rainbow_*`, `sanitize_config`, `desktop_exec_quote` | nada (se prueba en `tests/`) |
| Dibujo | `draw_bulb`, `draw_glow`, `draw_cable`, `BulbSprites` (caché de sprites) | cairo |
| Widgets | `LightsStrip` (la tira; también la vista previa), `EffectArea` (nieve) | GTK 3 |
| Ventanas | `StripWindow`, `EffectWindow` (capa layer-shell, sin entrada), `SettingsWindow` | gtk-layer-shell |
| Servicios | `MusicPlayer` (hilo), `SoundFx`, `Tray` (StatusNotifierItem propio por D-Bus), `PowerWatcher` | procesos externos, D-Bus |
| Aplicación | `App`: configuración, latido global (sonido y LEDs de la bandeja), línea de órdenes | todo lo anterior |

## Hilos

- **Principal (GTK)**: todo el dibujo y la interfaz. Nada bloqueante aquí.
- **Música** (`MusicPlayer._loop`): decodifica, aplica el volumen y escribe a `pw-cat`.
  Comparte con el principal solo `procs` (bajo `lock`), `stop_ev`, `volume` y `status`.
- **Sonidos** (`generate_sounds`, al arrancar): sintetiza los WAV la primera vez (≈4 s de CPU).
  Avisa con `SOUNDS_READY`; los WAV se escriben a un temporal y se renombran, así nadie lee uno a medias.

## Cuándo se redibuja (la regla que manda en el consumo)

La tira es un adorno: debe costar casi nada. `LightsStrip.on_tick` decide en cada cuadro:

1. **Tope de cuadros**: como mucho 60 por segundo (30 con batería o en «Ahorro»), aunque el
   monitor sea de 120/144 Hz.
2. **Solo lo que cambia de forma visible**: el brillo se cuantiza en 33 niveles (`SPRITE_LEVELS`);
   si un bombillo no cambia de nivel, no se repinta. Si cambian pocos, solo sus franjas.
3. **Balanceo**: se redibuja cuando la punta del bombillo se movería más de 0,3 px
   (`sway_redraw_interval`, ≈20 veces por segundo a tamaño 1). Antes era en cada cuadro del monitor.
4. **Arcoíris**: todos los bombillos cambian de tono a la vez, ≈6 veces por segundo
   (`rainbow_step`), en vez de cada uno en su momento (que obligaba a repintar en todos los cuadros).
5. **Dormir**: si nada se está moviendo, suelta el reloj de cuadros y pone un temporizador hasta el
   próximo cambio (parpadeo, paso de color o de balanceo).

Y cada repintado es barato:

- Los bombillos son **sprites** prerrenderizados por (tipo, tamaño, color, nivel); la caché tiene
  un tope de 48 MB y descarta lo menos usado (LRU).
- El **resplandor** es redondo: no se gira, se coloca en su centro girado y en píxel entero
  (medido: 21 µs frente a 78 µs por bombillo con la copia girada).
- El **cable** se dibuja una vez por distribución y luego solo se copia.

### Medidas (sway sin pantalla, 1920×1080@75 Hz + 2560×1440@120 Hz, % de un núcleo)

Ver CHANGELOG.md, versión 1.6, para la tabla antes/después. Se miden con el arnés de la sesión de
la 1.6: sway `WLR_BACKENDS=headless`, D-Bus privado (`dbus-run-session`) y un `pw-cat` falso,
para no tocar la sesión real ni sonar.

## Reglas de la casa

- Lógica nueva → función pura con prueba en `tests/`.
- Nada de sondeos periódicos si hay eventos: la energía se sigue por señales de UPower y
  power-profiles-daemon (sondeo de sysfs cada 30 s solo si UPower no existe).
- Todo proceso lanzado se recoge (`wait`) y sus tuberías se cierran; `make test` convierte las
  fugas de recursos en errores.
- Los archivos de configuración se escriben de forma atómica (`write_json_atomic`).
- Un `config.json` raro nunca debe tumbar el programa (`sanitize_config`).
- Cambios que toquen el dibujo: comparar píxeles con la versión anterior y medir CPU.
