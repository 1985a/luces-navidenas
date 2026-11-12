# Luces Navideñas

Bombillitos de Navidad que cuelgan del borde de la pantalla, inspirados en el clásico
«Holiday Lights» de Windows 95/98, con interfaz y luces modernas.

- Solo Wayland con wlr-layer-shell: Sway, Hyprland, river, KDE Plasma…
- Las luces no capturan el ratón: se hace clic «a través» de ellas.
- 8 modos de parpadeo, 4 tipos de bombillo, 12 paletas, nieve / ventisca / copos / destellos,
  música (villancico incluido o tu carpeta: MIDI, MP3, OGG…), icono en la bandeja con LEDs.

## Uso

```
luces-navidenas              inicia (o abre las opciones si ya está corriendo)
luces-navidenas --opciones   abre la ventana de opciones
luces-navidenas --oculto     inicia sin mostrar la ventana de opciones
luces-navidenas --alternar   enciende / apaga las luces (útil para un atajo)
luces-navidenas --salir      cierra el programa
```

Configuración: `~/.config/luces-navidenas/config.json` · Sonidos generados: `~/.cache/luces-navidenas/`

## Desarrollo

```
make test      # pruebas (sin ventanas ni sonido; las fugas de recursos cuentan como error)
make lint      # ruff, con las reglas y sus motivos en pyproject.toml
make paquete   # lint + pruebas + makepkg
makepkg -si    # construir e instalar en Arch
```

Todo el programa es un único script de Python (`luces-navidenas`): así se instala copiando un
archivo. Cómo está organizado por dentro y por qué: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

La versión para Windows (Tkinter, instalador NSIS) es un código aparte y no está en este repositorio.

Licencia MIT.
