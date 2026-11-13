# Arquitectura

Tres archivos de código, en capas:

| Archivo | Qué hay | Depende de |
|---|---|---|
| `nucleo.py` | Datos (modos, paletas, bombillos), lógica pura (`blink_target`, `fade_toward`, `layout_bulbs`, `sway_redraw_interval`, `rainbow_*`, `sanitize_config`…), dibujo con cairo, `BulbSprites`, y los motores sin ventana `StripEngine` (tira) y `EffectEngine` (nieve), sonidos sintetizados | Python + pycairo |
| `luces-navidenas` | Linux: `LightsStrip`/`EffectArea` (widgets GTK que envuelven los motores), ventanas layer-shell, música con `pw-cat`, bandeja StatusNotifierItem propia, `PowerWatcher`, opciones en GTK | GTK 3, gtk-layer-shell, D-Bus |
| `windows/luces_navidenas_win.py` | Windows: ventanas por capas de Win32 (`UpdateLayeredWindow`), AppBar para reservar espacio, `Renderer` (hilo de dibujo), música con MCI, efectos con winsound, bandeja con pystray, opciones en Tkinter | ctypes/Win32, Tkinter, pystray |

Lo que se ve es idéntico en los dos sistemas porque lo dibuja el mismo código (`StripEngine.draw`,
`EffectEngine.draw`); cada sistema solo decide cuándo despertar al motor y dónde presentar el resultado.

### Linux

| Capa | Qué hay |
|---|---|
| Widgets | `LightsStrip` (la tira; también la vista previa), `EffectArea` (nieve): conectan el reloj de cuadros de GTK con los motores |
| Ventanas | `StripWindow`, `EffectWindow` (capa layer-shell, sin entrada), `SettingsWindow` |
| Servicios | `MusicPlayer` (hilo), `SoundFx`, `Tray` (StatusNotifierItem propio por D-Bus), `PowerWatcher` |
| Aplicación | `App`: configuración, latido global (sonido y LEDs de la bandeja), línea de órdenes |

### Windows

| Pieza | Qué hace |
|---|---|
| `LayeredWindow` | Ventana transparente por píxel, siempre encima, que deja pasar los clics. Su mapa de bits (DIB) es la memoria de una superficie de cairo: se dibuja y se presenta sin copias intermedias |
| `StripWindow` | Una tira por monitor; si «No tapar las ventanas» está activo, se registra como barra (AppBar) |
| `EffectWindow` | Nieve sobre un monitor; repinta solo los bloques que cambian |
| `Renderer` | Hilo de dibujo. Con nieve, al ritmo de la pantalla (`DwmFlush`) con el tope de fps; sin nieve, duerme hasta el próximo cambio que pidan las tiras o hasta que una opción lo despierte |
| `MessageWindow` | Ventana invisible del hilo de Tk. Bandeja, otra copia del programa (`CommandServer`) y MCI la despiertan con `PostMessage`: nada sondea |
| `WinMusic` | Lista en bucle con MCI; el fin de cada canción llega como `MM_MCINOTIFY` |
| `CommandServer` + mutex | Una sola copia: la segunda le pasa sus argumentos a la primera por un socket local con contraseña |
| `Settings` | Ventana de opciones (Tkinter + sv_ttk); la vista previa solo se anima mientras se ve |

## Hilos

Linux:

- **Principal (GTK)**: todo el dibujo y la interfaz. Nada bloqueante aquí.
- **Música** (`MusicPlayer._loop`): decodifica, aplica el volumen y escribe a `pw-cat`.
  Comparte con el principal solo `procs` (bajo `lock`), `stop_ev`, `volume` y `status`.
- **Sonidos** (`generate_sounds`, al arrancar): sintetiza los WAV la primera vez (≈4 s de CPU).
  Avisa con `SOUNDS_READY`; los WAV se escriben a un temporal y se renombran, así nadie lee uno a medias.

Windows:

- **Tk**: interfaz, ventanas Win32 (se crean y destruyen aquí, que es donde se atienden sus mensajes),
  música MCI y latido de la bandeja.
- **Dibujo** (`Renderer`): pinta y presenta las ventanas por capas. Comparte con Tk las listas de
  ventanas bajo `lock`; Tk solo destruye una ventana después de sacarla de la lista.
- **Bandeja** (pystray) y **órdenes** (`CommandServer`): solo encolan y avisan con `PostMessage`.
- **Sonidos**: igual que en Linux.

## Cuándo se redibuja (la regla que manda en el consumo)

La tira es un adorno: debe costar casi nada. `StripEngine.tick` decide en cada cuadro (en los dos sistemas):

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

- Lógica nueva → en `nucleo.py`, como función pura con prueba en `tests/test_nucleo.py` (corre en Linux y en Windows).
- Nada de sondeos periódicos si hay eventos: la energía se sigue por señales de UPower y
  power-profiles-daemon (sondeo de sysfs cada 30 s solo si UPower no existe).
- Todo proceso lanzado se recoge (`wait`) y sus tuberías se cierran; `make test` convierte las
  fugas de recursos en errores.
- Los archivos de configuración se escriben de forma atómica (`write_json_atomic`).
- Un `config.json` raro nunca debe tumbar el programa (`sanitize_config`).
- Cambios que toquen el dibujo: comparar píxeles con la versión anterior y medir CPU.
