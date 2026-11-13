# Avisos de terceros

Luces Navideñas es software libre bajo la licencia GPL-3.0-or-later (ver LICENSE).

El paquete de Linux no incluye código de terceros: usa Python, PyGObject, pycairo, GTK 3 y
gtk-layer-shell del sistema.

El instalador de Windows lleva dentro, sin modificar, estos componentes:

| Componente | Licencia | Código fuente |
|---|---|---|
| Python 3.12 (intérprete y biblioteca estándar) | PSF-2.0 | https://www.python.org/ |
| Tcl/Tk 8.6 (tkinter) | Licencia de Tcl/Tk (tipo BSD) | https://www.tcl.tk/ |
| pycairo | LGPL-2.1-only OR MPL-1.1 | https://github.com/pygobject/pycairo |
| cairo, pixman, freetype y zlib (dentro de pycairo) | LGPL-2.1/MPL-1.1, MIT, FTL, zlib | https://cairographics.org/ |
| Pillow (y sus bibliotecas de imagen) | MIT-CMU | https://github.com/python-pillow/Pillow |
| pystray | LGPL-3.0 | https://github.com/moses-palmer/pystray |
| six (dependencia de pystray) | MIT | https://github.com/benjaminp/six |
| sv-ttk (tema Sun Valley) | MIT | https://github.com/rdbende/Sun-Valley-ttk-theme |
| Cargador de PyInstaller | GPL-2.0-or-later con excepción de enlace | https://github.com/pyinstaller/pyinstaller |

Las bibliotecas LGPL (pycairo, cairo y pystray) se pueden reemplazar: el código de este programa
y el script que arma el instalador (`windows/build.py`) están en
https://github.com/1985a/luces-navidenas, así que cualquiera puede volver a compilarlo con otras
versiones de esas bibliotecas.

«Holiday Lights» es la idea original de Don Metzler / Barefoot Productions (1994-95). Este programa
es una obra nueva inspirada en ella; no contiene código, imágenes ni sonidos de aquel.
