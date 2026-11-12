# Cambios

## 1.6 — 2026-11-11 · pase de buenas prácticas

Mismo aspecto (comparado píxel a píxel con la 1.5: diferencia máxima 6/255), mucho menos consumo.

### Rendimiento (% de un núcleo, dos monitores 75 Hz + 120 Hz)

| Configuración | 1.5 | 1.6 |
|---|---:|---:|
| Alternando, retro, balanceo (config. real del usuario) | 55,8 | 6,3 |
| Igual sin balanceo | 16,9 | 4,0 |
| Arcoíris | 58,4 | 14,3 |
| Ola suave | 68,9 | 11,6 |
| Nieve | 42,5 | 25,7 |
| Encendidas fijas / con música | 0,1 / 0,4 | 0,1 / 0,4 |

- Tope de 60 cuadros por segundo (30 en ahorro) también para la tira, no solo para la nieve.
- El balanceo se redibuja cuando se nota (≈20 veces por segundo), no en cada cuadro del monitor.
- El arcoíris cambia de tono en todos los bombillos a la vez (≈6 veces por segundo).
- El resplandor se pega sin girar y en píxel entero; el cable se dibuja una sola vez.
- Caché de sprites con tope de memoria (48 MB) y descarte de lo menos usado, en vez de vaciarla entera.

### Arreglos

- La primera vez, la música podía leer el villancico a medio generar: ahora espera a que esté
  y los WAV se escriben de forma atómica.
- El interruptor de encendido escribía config.json directamente (podía quedar a medias):
  ahora todas las escrituras son atómicas.
- Un config.json con valores inválidos (modo, bombillo, efecto desconocidos o fuera de rango)
  podía hacer fallar el dibujo: ahora se validan al cargar.
- Música: los procesos se comparten entre hilos bajo un candado, se esperan al matarlos (sin
  zombis) y se cierran sus tuberías; ya no hay espera activa al terminar una canción.
- Efectos de sonido: se recogen los procesos terminados.
- Ctrl+C / SIGTERM se atienden con GLib (antes, el manejador de Python esperaba a que algo
  despertara al intérprete).
- La fluidez automática escucha a UPower y power-profiles-daemon por D-Bus en vez de lanzar un
  hilo con dos consultas cada 10 s.
- La línea Exec= del autoarranque se cita si la ruta tiene espacios.
- La lista de canciones ya no se relee de disco con cada paso de la rueda del volumen.
- El paquete declara gtk3 y gtk-layer-shell, que faltaban en las dependencias.

### Calidad

- Lógica de la animación separada en funciones puras con 20 pruebas (`make test`).
- `make lint` (ruff) limpio, con las reglas y sus motivos en pyproject.toml.
- docs/ARQUITECTURA.md: capas, hilos y la regla de cuándo se redibuja.
