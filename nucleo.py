"""Núcleo compartido de Luces Navideñas (Linux y Windows).

Todo lo que no depende del sistema de ventanas: datos (modos, paletas, bombillos), la lógica
de la animación, el dibujo con cairo, la caché de sprites, los motores de la tira y de los
efectos, la síntesis de los sonidos y la validación de la configuración.

Copyright (C) 2026 Wagner. Licencia GPL-3.0-or-later (ver LICENSE).
"""

import copy
import json
import math
import os
import random
import struct
import sys
import threading
import wave
from collections import OrderedDict
from typing import NamedTuple

import cairo

APP_NAME = "Luces Navideñas"
VERSION = "1.6"

BUILTIN_SONG = "__incluido__"
BUILTIN_SONG_TITLE = "Jingle Bells — campanitas (incluido)"


def default_music_dir():
    for d in ("~/Música/Navidad", "~/Music/Navidad", "~/Música", "~/Music"):
        p = os.path.expanduser(d)
        if os.path.isdir(p):
            return p
    return os.path.expanduser("~")


DEFAULTS = {
    "power": True,
    "mode": "some",
    "rate": 0.7,
    "smooth": True,
    "bulb": "c9",
    "size": 1.0,
    "burnt": False,
    "broken": False,
    "palette": "clasico",
    "music": False,
    "music_dir": default_music_dir(),
    "music_skip": [],
    "volume": 0.7,
    "fx_volume": 0.5,
    "effect": "nieve",
    "effect_on": False,
    "effect_density": 0.5,
    "more": False,
    "warm": True,
    "sound": False,
    "tray_leds": True,
    "sway": False,
    "fluidez": "auto",
    "position": "top",
    "reserve": True,
    "monitors": "all",
    "show_options": True,
    "bg_notice": True,
    "autostart": False,
    "seed": 1994,
}

# Llaves que obligan a recrear las ventanas de las luces
LAYOUT_KEYS = ("position", "monitors", "size", "more", "reserve")



# --------------------------------------------------------------------------
# Datos: modos, colores, bombillos, efectos
# --------------------------------------------------------------------------

MODES = [
    ("none", "Encendidas fijas"),
    ("random", "Aleatorio"),
    ("all", "Todas juntas"),
    ("chase", "Persecución"),
    ("alternate", "Alternando"),
    ("some", "Algunas parpadean"),
    ("wave", "Ola suave"),
    ("breathe", "Respirar"),
]
CONTINUOUS_MODES = ("wave", "breathe")


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


PALETTES = [
    ("clasico", "Multicolor", ["#2f6bff", "#ff3b3b", "#2fdc5a", "#ffd23a", "#36e4f0", "#a855f7", "#ff4fc8"]),
    ("retro", "Retro 94", ["#ff2020", "#20d040", "#ffe020", "#2080ff", "#a070ff"]),
    ("navidad", "Navidad", ["#ff3030", "#22d45a"]),
    ("calido", "Cálido", ["#ffcf8a"]),
    ("frio", "Frío", ["#dff1ff"]),
    ("dorado", "Dorado", ["#ffb52e", "#ffd76a"]),
    ("rojo", "Rojo", ["#ff2d3d"]),
    ("verde", "Verde", ["#2fe06a"]),
    ("azul", "Azul", ["#3a7bff", "#5fb6ff"]),
    ("morado", "Morado", ["#b05cff", "#ff5fd2"]),
    ("pastel", "Pastel", ["#ff9ec7", "#9ee7ff", "#c9a3ff", "#a8f0b5", "#ffe59e"]),
    ("arcoiris", "Arcoíris", None),  # animado
]
PALETTE_MAP = {k: (n, [hex_to_rgb(c) for c in cols] if cols else None) for k, n, cols in PALETTES}

BULBS = [
    ("c9", "Clásica (C9)"),
    ("mini", "Mini"),
    ("globo", "Globo LED"),
    ("estrella", "Estrella"),
]

EFFECTS = [
    ("nieve", "Nieve cayendo"),
    ("ventisca", "Ventisca"),
    ("copos", "Copos grandes"),
    ("destellos", "Destellos"),
]

BULB_UNIT = 20.0  # px, tamaño base del bombillo (a tamaño 1.0)
TOP_MARGIN = 3.0
BULB_LENGTH = 1.9  # largo del bombillo (casquillo + vidrio) en unidades de BULB_UNIT

# Animación (ver docs/ARQUITECTURA.md, «Cuándo se redibuja»)
FADE_TIME = 0.075        # s, constante de tiempo del desvanecido
FADE_SNAP = 0.004        # por debajo de esta diferencia el desvanecido se da por terminado
SWAY_AMPLITUDE = math.radians(5)
SWAY_SPEED = 1.4         # rad/s de la oscilación del balanceo
SWAY_MAX_STEP_PX = 0.3   # el balanceo se redibuja cuando la punta se movería más que esto
RAINBOW_STEPS = 96       # tonos del arcoíris (cuantizados para poder reutilizar los sprites)
RAINBOW_SPEED = 0.06     # vueltas de color por segundo
RAINBOW_SPREAD = 0.07    # desfase de tono entre bombillos vecinos


def rate_to_interval(rate):
    """0 = lento (≈1.6 s), 1 = rápido (≈0.07 s)."""
    return 0.07 + 1.5 * (1.0 - rate) ** 1.6


def blink_target(mode, i, step, flasher):
    """Brillo objetivo (0 o 1) del bombillo i en el paso `step` de un modo con parpadeo."""
    if mode == "none":
        return 1.0
    if mode == "random":
        return 1.0 if hash01(step, i) < 0.55 else 0.0
    if mode == "all":
        return 1.0 if step % 2 == 0 else 0.0
    if mode == "chase":
        return 1.0 if (i - step) % 3 == 0 else 0.0
    if mode == "alternate":
        return 1.0 if (i + step) % 2 == 0 else 0.0
    # "some": solo algunos parpadean
    if not flasher:
        return 1.0
    return 1.0 if hash01(step, i) < 0.5 else 0.0


def continuous_target(mode, i, t, rate):
    """Brillo (0-1) del bombillo i en el instante t para los modos sin pasos (ola, respirar)."""
    speed = 0.6 + rate * 5.0
    if mode == "wave":
        return 0.12 + 0.88 * (0.5 + 0.5 * math.sin(t * speed * 1.6 - i * 0.55))
    return 0.10 + 0.90 * (0.5 + 0.5 * math.sin(t * speed))


def fade_toward(v, target, k):
    """Un paso del desvanecido exponencial; llega exactamente al objetivo cuando ya no se nota."""
    nv = v + (target - v) * k
    return target if abs(nv - target) < FADE_SNAP else nv


def sway_redraw_interval(size):
    """Segundos entre redibujos del balanceo: la punta no avanza más de SWAY_MAX_STEP_PX por cuadro.

    El balanceo es lento (≈5 px/s en la punta a tamaño 1): redibujarlo a 120 Hz costaba
    medio núcleo y no se veía distinto que a ≈20 Hz.
    """
    tip_px = BULB_LENGTH * BULB_UNIT * size
    max_speed = SWAY_AMPLITUDE * SWAY_SPEED * tip_px  # px/s en el punto de máxima velocidad
    return SWAY_MAX_STEP_PX / max_speed


def sway_angle(t, phase):
    return SWAY_AMPLITUDE * math.sin(t * SWAY_SPEED + phase)


def rainbow_step(t):
    """Paso de color del arcoíris en el instante t (cambia ≈6 veces por segundo)."""
    return int(t * RAINBOW_SPEED * RAINBOW_STEPS)


def rainbow_hue(step, i):
    """Tono del bombillo i: todos avanzan juntos, así hay un único redibujo por paso."""
    return ((step + round(i * RAINBOW_SPREAD * RAINBOW_STEPS)) % RAINBOW_STEPS) / RAINBOW_STEPS


def layout_bulbs(width, cfg):
    """Reparte los bombillos a lo ancho. Determinista: la misma semilla da la misma tira.

    Devuelve (separación, [Bulb]).
    """
    u = BULB_UNIT * cfg["size"]
    gap = u * (1.55 if cfg["more"] else 2.45)
    n = max(1, int(width // gap))
    spacing = width / n
    rng = random.Random(cfg["seed"])
    bulbs = []
    for i in range(n):
        r_dead = rng.random()
        rng.random()  # reservado (mantiene la secuencia estable entre versiones)
        r_flasher = rng.random()
        angle = math.radians(rng.uniform(-7, 7))
        dead = None
        if cfg["burnt"] and r_dead < 0.09:
            dead = "burnt"
        elif cfg["broken"] and 0.09 <= r_dead < 0.15:
            dead = "broken"
        bulbs.append(Bulb((i + 0.5) * spacing, i, angle, dead, r_flasher < 0.35, rng.uniform(0, 6.28)))
    return spacing, bulbs


def reserved_height(cfg):
    """Espacio que se reserva (cable + bombillo); el resplandor puede sobresalir sin tapar clics."""
    u = BULB_UNIT * cfg["size"]
    return int(TOP_MARGIN + 0.45 * u + 1.65 * u + 2)


def strip_height(cfg):
    u = BULB_UNIT * cfg["size"]
    return int(TOP_MARGIN + 0.45 * u + 1.65 * u + 1.0 * u)


def hash01(a, b):
    x = (a * 73856093) ^ (b * 19349663) ^ 0x5bd1e995
    x = (x ^ (x >> 13)) * 0x27d4eb2d
    return ((x ^ (x >> 15)) & 0xFFFF) / 65535.0


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(lerp(x, y, t) for x, y in zip(c1, c2, strict=True))


def hsv(h, s, v):
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]


# --------------------------------------------------------------------------
# Dibujo de los bombillos
# --------------------------------------------------------------------------

GLOW_SCALE = 4.4  # radio del resplandor respecto al del vidrio
SOCKETS = {"c9": (0.20, 0.46), "mini": (0.13, 0.40), "globo": (0.16, 0.40), "estrella": (0.13, 0.34)}


def glass_path(cr, kind, u):
    """Traza el vidrio del bombillo. Devuelve (centro_y, radio)."""
    if kind == "mini":
        cr.move_to(-0.10 * u, 0.36 * u)
        cr.curve_to(-0.28 * u, 0.75 * u, -0.22 * u, 1.45 * u, 0, 1.64 * u)
        cr.curve_to(0.22 * u, 1.45 * u, 0.28 * u, 0.75 * u, 0.10 * u, 0.36 * u)
        cr.close_path()
        return 1.0 * u, 0.25 * u
    if kind == "globo":
        cr.new_sub_path()
        cr.arc(0, 0.80 * u, 0.40 * u, 0, 2 * math.pi)
        cr.close_path()
        return 0.80 * u, 0.40 * u
    if kind == "estrella":
        cx, cy, ro, ri = 0, 0.88 * u, 0.54 * u, 0.23 * u
        for k in range(10):
            a = -math.pi / 2 + k * math.pi / 5
            r = ro if k % 2 == 0 else ri
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            (cr.move_to if k == 0 else cr.line_to)(x, y)
        cr.close_path()
        return cy, 0.42 * u
    # c9 clásica
    cr.move_to(-0.17 * u, 0.40 * u)
    cr.curve_to(-0.53 * u, 0.85 * u, -0.42 * u, 1.42 * u, 0, 1.62 * u)
    cr.curve_to(0.42 * u, 1.42 * u, 0.53 * u, 0.85 * u, 0.17 * u, 0.40 * u)
    cr.close_path()
    return 1.03 * u, 0.36 * u


def glass_metrics(kind, u):
    s = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
    return glass_path(cairo.Context(s), kind, u)


_METRIC_CACHE = {}


def metrics(kind, u):
    key = (kind, round(u, 2))
    if key not in _METRIC_CACHE:
        _METRIC_CACHE[key] = glass_metrics(kind, u)
    return _METRIC_CACHE[key]


def draw_glow(cr, kind, u, rgb, v):
    if v <= 0.01:
        return
    cy, rad = metrics(kind, u)
    R = rad * GLOW_SCALE
    g = cairo.RadialGradient(0, cy, 0, 0, cy, R)
    g.add_color_stop_rgba(0.0, rgb[0], rgb[1], rgb[2], 0.55 * v)
    g.add_color_stop_rgba(0.22, rgb[0], rgb[1], rgb[2], 0.30 * v)
    g.add_color_stop_rgba(0.55, rgb[0], rgb[1], rgb[2], 0.08 * v)
    g.add_color_stop_rgba(1.0, rgb[0], rgb[1], rgb[2], 0.0)
    cr.save()
    cr.set_operator(cairo.OPERATOR_ADD)
    cr.set_source(g)
    cr.arc(0, cy, R, 0, 2 * math.pi)
    cr.fill()
    cr.restore()


def _rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_socket(cr, kind, u):
    sw, sh = SOCKETS[kind]
    sw, sh = sw * u, sh * u
    g = cairo.LinearGradient(-sw, 0, sw, 0)
    g.add_color_stop_rgb(0.0, 0.07, 0.25, 0.13)
    g.add_color_stop_rgb(0.35, 0.30, 0.62, 0.38)
    g.add_color_stop_rgb(0.65, 0.16, 0.44, 0.24)
    g.add_color_stop_rgb(1.0, 0.05, 0.20, 0.10)
    _rounded_rect(cr, -sw, -0.02 * u, 2 * sw, sh, 0.07 * u)
    cr.set_source(g)
    cr.fill()
    cr.set_source_rgba(0, 0, 0, 0.35)
    cr.set_line_width(max(0.6, 0.035 * u))
    for f in (0.35, 0.62):
        cr.move_to(-sw + 0.02 * u, sh * f)
        cr.line_to(sw - 0.02 * u, sh * f)
    cr.stroke()
    return sh


def draw_bulb(cr, kind, u, rgb, v, dead=None):
    sh = draw_socket(cr, kind, u)
    dim = tuple(c * 0.28 + 0.03 for c in rgb)

    if dead == "broken":
        # Vidrio roto: solo queda un pedazo junto al casquillo, y se ve el filamento
        cr.save()
        glass_path(cr, kind, u)
        cr.clip()
        cr.new_path()
        y0 = sh + 0.10 * u
        pts = [(-u, 0), (u, 0), (u, y0 + 0.10 * u), (0.30 * u, y0 + 0.28 * u), (0.12 * u, y0 + 0.08 * u),
               (-0.02 * u, y0 + 0.34 * u), (-0.16 * u, y0 + 0.12 * u), (-0.30 * u, y0 + 0.26 * u), (-u, y0 + 0.05 * u)]
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.clip()
        glass_path(cr, kind, u)
        cr.set_source_rgba(dim[0], dim[1], dim[2], 0.85)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.18)
        cr.set_line_width(max(0.5, 0.03 * u))
        cr.stroke()
        cr.restore()
        cr.set_source_rgba(0.65, 0.62, 0.55, 0.9)
        cr.set_line_width(max(0.6, 0.03 * u))
        cr.move_to(-0.05 * u, sh)
        cr.curve_to(-0.07 * u, sh + 0.2 * u, -0.04 * u, sh + 0.35 * u, -0.06 * u, sh + 0.5 * u)
        cr.move_to(0.05 * u, sh)
        cr.curve_to(0.07 * u, sh + 0.2 * u, 0.05 * u, sh + 0.33 * u, 0.07 * u, sh + 0.46 * u)
        cr.stroke()
        return

    if dead == "burnt":
        base = mix(rgb, (0.32, 0.30, 0.28), 0.55)
        dim = tuple(c * 0.40 for c in base)
        v = 0.0

    cy, rad = glass_path(cr, kind, u)
    g = cairo.RadialGradient(-0.10 * u, cy - 0.30 * u, 0.02 * u, 0, cy, rad * 1.45)
    mid = mix(dim, rgb, v)
    core = mix(mid, (1, 1, 1), 0.72 * v)
    edge = mix(tuple(c * 0.55 for c in dim), tuple(c * 0.78 for c in rgb), v)
    g.add_color_stop_rgba(0.0, core[0], core[1], core[2], 0.97)
    g.add_color_stop_rgba(0.55, mid[0], mid[1], mid[2], 0.95)
    g.add_color_stop_rgba(1.0, edge[0], edge[1], edge[2], 0.95)
    cr.set_source(g)
    cr.fill_preserve()
    cr.set_source_rgba(0, 0, 0, 0.28)
    cr.set_line_width(max(0.5, 0.04 * u))
    cr.stroke()

    if v > 0.05:  # filamento caliente
        f = cairo.RadialGradient(0, cy - 0.05 * u, 0, 0, cy - 0.05 * u, 0.22 * u)
        f.add_color_stop_rgba(0, 1, 1, 0.95, 0.65 * v)
        f.add_color_stop_rgba(1, 1, 1, 0.95, 0)
        cr.save()
        cr.set_operator(cairo.OPERATOR_ADD)
        cr.set_source(f)
        cr.arc(0, cy - 0.05 * u, 0.22 * u, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

    # reflejo
    cr.save()
    cr.translate(-0.13 * u if kind != "estrella" else -0.08 * u, cy - 0.20 * u)
    cr.rotate(0.18)
    cr.scale(1, 2.4 if kind in ("c9", "mini") else 1.4)
    cr.arc(0, 0, 0.06 * u, 0, 2 * math.pi)
    cr.set_source_rgba(1, 1, 1, 0.22 + 0.28 * v)
    cr.fill()
    cr.restore()

    if dead == "burnt":  # hollín
        s = cairo.RadialGradient(0, sh + 0.12 * u, 0, 0, sh + 0.12 * u, 0.32 * u)
        s.add_color_stop_rgba(0, 0, 0, 0, 0.55)
        s.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.save()
        glass_path(cr, kind, u)
        cr.clip()
        cr.set_source(s)
        cr.paint()
        cr.restore()


# --------------------------------------------------------------------------
# La tira de luces (se usa en las ventanas sobre el escritorio y en la vista previa)
# --------------------------------------------------------------------------

class Bulb:
    __slots__ = ("angle", "dead", "flasher", "i", "phase", "target", "v", "x")

    def __init__(self, x, i, angle, dead, flasher, phase):
        self.x, self.i, self.angle, self.dead = x, i, angle, dead
        self.flasher, self.phase = flasher, phase
        self.v, self.target = 0.0, 0.0


SPRITE_LEVELS = 33  # niveles de brillo prerrenderizados (0 = apagado)
SPRITE_BUDGET = 48 * 1024 * 1024  # bytes de sprites en caché (se descartan los menos usados)


def make_surface(w, h, sf):
    """Superficie ARGB de w×h px lógicos con la escala del monitor (nítida en HiDPI)."""
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, math.ceil(w * sf)), max(1, math.ceil(h * sf)))
    s.set_device_scale(sf, sf)
    return s


class BulbSprites:
    """Cada bombillo se dibuja una sola vez por color y nivel de brillo; luego solo se copia.

    Dibujar desde cero 40-80 bombillos con degradados en cada cuadro tardaba tanto que
    se perdían cuadros (y la nieve daba tirones al ritmo del parpadeo). La caché tiene
    un tope en bytes y descarta primero lo que hace más tiempo que no se usa.
    """

    def __init__(self, budget=SPRITE_BUDGET):
        self.cache = OrderedDict()
        self.bytes = 0
        self.budget = budget

    def _get(self, key):
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.move_to_end(key)
        return hit

    def _store(self, key, val):
        surf = val[0]
        self.bytes += surf.get_stride() * surf.get_height()
        self.cache[key] = val
        while self.bytes > self.budget and len(self.cache) > 1:
            _key, old = self.cache.popitem(last=False)
            self.bytes -= old[0].get_stride() * old[0].get_height()
        return val

    def glow(self, kind, u, rgb, level, sf):
        """Resplandor centrado en la superficie. Devuelve (superficie, lado en px lógicos)."""
        key = ("g", kind, round(u, 2), rgb, level, sf)
        hit = self._get(key)
        if hit:
            return hit
        cy, rad = metrics(kind, u)
        size = 2 * rad * GLOW_SCALE + 4
        surf = make_surface(size, size, sf)
        cr = cairo.Context(surf)
        cr.translate(size / 2, size / 2 - cy)
        draw_glow(cr, kind, u, rgb, level / (SPRITE_LEVELS - 1))
        surf.flush()
        return self._store(key, (surf, size))

    def body(self, kind, u, rgb, level, dead, sf):
        """Bombillo con el casquillo en (ox, oy). Devuelve (superficie, ox, oy)."""
        key = ("b", kind, round(u, 2), rgb, level, dead, sf)
        hit = self._get(key)
        if hit:
            return hit
        w, h = 1.25 * u + 4, BULB_LENGTH * u + 4
        surf = make_surface(w, h, sf)
        cr = cairo.Context(surf)
        ox, oy = w / 2, 0.1 * u + 2
        cr.translate(ox, oy)
        draw_bulb(cr, kind, u, rgb, level / (SPRITE_LEVELS - 1), dead)
        surf.flush()
        return self._store(key, (surf, ox, oy))


_THREAD = threading.local()


def thread_sprites():
    """La caché de sprites del hilo actual.

    Una por hilo a propósito: cairo no permite usar la misma superficie como origen desde dos
    hilos a la vez (al pintar ajusta el pixman_image de esa superficie). En Linux todo se dibuja
    en un hilo; en Windows la tira y la vista previa se dibujan en hilos distintos, y compartir
    la caché tumbaba el programa (0xC0000409) al apagar las luces con las opciones abiertas.
    """
    sprites = getattr(_THREAD, "sprites", None)
    if sprites is None:
        sprites = _THREAD.sprites = BulbSprites()
    return sprites


def release_thread_sprites():
    """Libera la caché del hilo actual (p. ej. al cerrar la ventana con la vista previa)."""
    _THREAD.sprites = None


def draw_cable(cr, u, spacing, n):
    """El cable verde que cuelga entre bombillo y bombillo (una curva por tramo)."""
    sag, top = 0.45 * u, TOP_MARGIN
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(0, top)
    for k in range(n):
        x0, x1 = k * spacing, (k + 1) * spacing
        cx, cy = (x0 + x1) / 2, top + 2 * sag
        cr.curve_to(x0 + 2 / 3 * (cx - x0), top + 2 / 3 * (cy - top),
                    x1 + 2 / 3 * (cx - x1), top + 2 / 3 * (cy - top), x1, top)
    path = cr.copy_path()
    cr.set_source_rgba(0.04, 0.16, 0.08, 0.95)
    cr.set_line_width(max(1.6, 0.13 * u))
    cr.stroke()
    cr.append_path(path)
    cr.set_source_rgba(0.35, 0.65, 0.40, 0.55)
    cr.set_line_width(max(0.6, 0.04 * u))
    cr.stroke()


PREVIEW_OFFSET = 6  # px: en la vista previa la tira baja un poco para que se vea el cable


# --------------------------------------------------------------------------
# Motor de la tira: decide cuándo y qué redibujar, y dibuja en cualquier cairo
# --------------------------------------------------------------------------

class Frame(NamedTuple):
    """Resultado de StripEngine.tick()."""

    redraw: str | None   # None (nada), "all" (toda la tira) o "spans" (solo esas franjas)
    spans: tuple         # ((x0, x1), …) en px si redraw == "spans"
    busy: bool           # sigue cambiando: hay que llamar de nuevo en el próximo cuadro
    wait: float          # si no está ocupado: segundos hasta el próximo cambio visible


class StripEngine:
    """La tira de luces sin ventana: estado de cada bombillo, la regla de redibujo y el dibujo.

    La usan la versión de Linux (dentro de un widget GTK) y la de Windows (en una ventana
    por capas). Ver docs/ARQUITECTURA.md, «Cuándo se redibuja».
    """

    def __init__(self, preview=False):
        self.preview = preview
        self.bulbs = []
        self.layout_sig = None
        self.spacing = 40.0
        self.step = None           # paso de parpadeo ya aplicado
        self.last_t = None         # instante del último tick
        self.sway_t = 0.0          # instante con el que se dibuja el balanceo
        self.rainbow_shown = None  # paso de arcoíris dibujado
        self.force = True          # redibujar todo en el próximo tick
        self.cable = None          # (firma, superficie): el cable se dibuja una vez por distribución

    def frame_due(self, t, fps):
        """¿Ya toca procesar un cuadro? (tope de fps; con margen para el temblor del reloj)."""
        return self.force or self.last_t is None or t - self.last_t >= 1.0 / fps - 0.003

    # ---- distribución
    def ensure_layout(self, width, cfg):
        sig = (int(width), cfg["size"], cfg["more"], cfg["burnt"], cfg["broken"], cfg["seed"])
        if sig == self.layout_sig:
            return
        self.layout_sig = sig
        old = {b.i: b.v for b in self.bulbs}
        self.spacing, self.bulbs = layout_bulbs(width, cfg)
        for b in self.bulbs:
            b.v = old.get(b.i, 0.0)
        self.step = None
        self.force = True
        self.cable = None

    # ---- animación
    def _update_targets(self, cfg, t):
        mode = cfg["mode"]
        if mode in CONTINUOUS_MODES:
            for b in self.bulbs:
                b.target = continuous_target(mode, b.i, t, cfg["rate"])
            return
        step = int(t / rate_to_interval(cfg["rate"]))
        if step != self.step:
            self.step = step
            for b in self.bulbs:
                b.target = blink_target(mode, b.i, step, b.flasher)

    def _advance(self, cfg, dt):
        """Avanza los brillos. Devuelve (bombillos cuyo nivel visible cambió, ¿sigue algún desvanecido?)."""
        on = cfg["power"]
        instant = on and (cfg["mode"] in CONTINUOUS_MODES or not cfg["smooth"])
        k = 1.0 - math.exp(-dt / FADE_TIME)
        top = SPRITE_LEVELS - 1
        moved, fading = [], False
        for b in self.bulbs:
            tg = b.target if (on and not b.dead) else 0.0
            nv = tg if instant else fade_toward(b.v, tg, k)
            if nv != tg:
                fading = True
            if round(nv * top) != round(b.v * top):
                moved.append(b)
            b.v = nv
        return moved, fading

    def _next_change(self, cfg, t):
        """Segundos hasta el próximo cambio visible si nada se está moviendo ahora."""
        waits = [1.0]
        if cfg["power"] and cfg["mode"] != "none":
            iv = rate_to_interval(cfg["rate"])
            waits.append(iv - (t % iv) + 0.002)
        if cfg["sway"]:
            waits.append(self.sway_t + sway_redraw_interval(cfg["size"]) - t)
        if cfg["palette"] == "arcoiris":
            waits.append((rainbow_step(t) + 1) / (RAINBOW_SPEED * RAINBOW_STEPS) - t + 0.002)
        return max(0.0, min(waits))

    def _spans(self, changed, cfg, width):
        """Franjas (x0, x1) que cubren los bombillos que cambiaron y sus vecinos, o None si es casi todo."""
        reach = BULB_LENGTH * BULB_UNIT * cfg["size"] + 4
        spans = []
        for b in sorted(changed, key=lambda b: b.x):
            x0, x1 = max(0.0, b.x - reach), min(float(width), b.x + reach)
            if spans and x0 <= spans[-1][1] + 2 * reach:
                spans[-1][1] = max(spans[-1][1], x1)
            else:
                spans.append([x0, x1])
        if sum(x1 - x0 + 2 * reach for x0, x1 in spans) > 0.5 * width:
            return None  # cambió casi toda la tira
        return tuple((math.floor(x0), math.ceil(x1)) for x0, x1 in spans)

    def tick(self, t, cfg, width):
        """Avanza la animación hasta el instante t (s) y dice qué hay que redibujar."""
        if width <= 1:
            return Frame(None, (), True, 0.0)
        dt = 0.016 if self.last_t is None else max(0.0, min(0.1, t - self.last_t))
        self.last_t = t
        self.ensure_layout(width, cfg)
        self._update_targets(cfg, t)
        moved, fading = self._advance(cfg, dt)

        full = self.force
        self.force = False
        if cfg["sway"] and t - self.sway_t >= sway_redraw_interval(cfg["size"]):
            self.sway_t = t
            full = True
        if cfg["palette"] == "arcoiris" and rainbow_step(t) != self.rainbow_shown:
            self.rainbow_shown = rainbow_step(t)
            full = True

        redraw, spans = None, ()
        if full or (self.preview and moved):
            redraw = "all"
        elif moved:
            spans = self._spans(moved, cfg, width)
            redraw = "all" if spans is None else "spans"
            spans = spans or ()
        busy = bool(fading or moved or (cfg["power"] and cfg["mode"] in CONTINUOUS_MODES))
        return Frame(redraw, spans, busy, 0.0 if busy else self._next_change(cfg, t))

    def bulb_color(self, b, cfg, v):
        _name, cols = PALETTE_MAP.get(cfg["palette"], PALETTE_MAP["clasico"])
        if cols is None:
            rgb = hsv(rainbow_hue(self.rainbow_shown or 0, b.i), 0.85, 1.0)
        else:
            rgb = cols[b.i % len(cols)]
        if cfg.get("warm") and 0.01 < v < 0.98:
            # Como un incandescente: con poca corriente el filamento da luz ámbar
            w = (1.0 - v) ** 1.5 * 0.75 * min(1.0, v / 0.08)
            rgb = mix(rgb, mix(rgb, (1.0, 0.45, 0.12), 0.85), w)
        return tuple(round(c, 3) for c in rgb)

    # ---- dibujo
    def _strip_transform(self, cr, cfg, h):
        """Pasa de coordenadas de la tira (cable arriba) a las de la superficie."""
        if self.preview:
            cr.translate(0, PREVIEW_OFFSET)
        elif cfg["position"] == "bottom":
            cr.translate(0, h)
            cr.scale(1, -1)

    def _to_surface(self, x, y, cfg, h):
        if self.preview:
            return x, y + PREVIEW_OFFSET
        if cfg["position"] == "bottom":
            return x, h - y
        return x, y

    def _angle(self, b, cfg):
        return b.angle + (sway_angle(self.sway_t, b.phase) if cfg["sway"] else 0.0)

    @staticmethod
    def _draw_preview_background(cr, w, h):
        """Cielo nocturno con nieve en el suelo; deja el recorte redondeado puesto."""
        _rounded_rect(cr, 0, 0, w, h, 12)
        g = cairo.LinearGradient(0, 0, 0, h)
        g.add_color_stop_rgb(0, 0.035, 0.06, 0.11)
        g.add_color_stop_rgb(1, 0.07, 0.10, 0.17)
        cr.set_source(g)
        cr.fill_preserve()
        cr.clip()
        rng = random.Random(7)
        for _ in range(int(w / 9)):
            cr.set_source_rgba(1, 1, 1, rng.uniform(0.15, 0.5))
            cr.arc(rng.uniform(0, w), rng.uniform(h * 0.45, h), rng.uniform(0.4, 1.1), 0, 6.3)
            cr.fill()

    def _paint_cable(self, cr, cfg, w, h, u, sf):
        sig = (w, h, u, self.spacing, len(self.bulbs), sf, cfg["position"])
        if self.cable is None or self.cable[0] != sig:
            surf = make_surface(w, h, sf)
            c = cairo.Context(surf)
            self._strip_transform(c, cfg, h)
            draw_cable(c, u, self.spacing, len(self.bulbs))
            surf.flush()
            self.cable = (sig, surf)
        cr.set_source_surface(self.cable[1], 0, 0)
        cr.paint()

    def draw(self, cr, w, h, cfg, sf=1):
        """Dibuja la tira de w×h px en cr. Respeta el recorte de cr: solo se pinta lo que cae dentro."""
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.bulbs:
            if self.preview:
                self._draw_preview_background(cr, w, h)
            self._draw_bulbs(cr, w, h, cfg, sf)
        cr.restore()

    def _draw_bulbs(self, cr, w, h, cfg, sf):
        u = BULB_UNIT * cfg["size"]
        kind = cfg["bulb"]
        self._paint_cable(cr, cfg, w, h, u, sf)

        cx0, _cy0, cx1, _cy1 = cr.clip_extents()
        reach = BULB_LENGTH * u + 4
        top = SPRITE_LEVELS - 1
        items = []
        for b in self.bulbs:
            if b.x < cx0 - reach or b.x > cx1 + reach:
                continue
            level = 0 if b.dead else round(b.v * top)
            items.append((b, level, self.bulb_color(b, cfg, level / top)))

        # 1ª pasada: resplandor (se suma entre vecinos). Es redondo, así que no hace falta
        # girarlo: basta con llevar su centro a donde queda al girar el bombillo y pegarlo
        # en un píxel entero, que es varias veces más barato que una copia girada.
        cy = metrics(kind, u)[0]
        pivot_y = TOP_MARGIN + 0.45 * u
        sprites = thread_sprites()
        cr.set_operator(cairo.OPERATOR_ADD)
        for b, level, rgb in items:
            if level > 0:
                surf, size = sprites.glow(kind, u, rgb, level, sf)
                a = self._angle(b, cfg)
                gx, gy = self._to_surface(b.x - cy * math.sin(a), pivot_y + cy * math.cos(a), cfg, h)
                x0, y0 = round(gx - size / 2), round(gy - size / 2)
                cr.set_source_surface(surf, x0, y0)
                cr.rectangle(x0, y0, size, size)
                cr.fill()

        # 2ª pasada: bombillos (estos sí giran: el vidrio no es simétrico)
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.save()
        self._strip_transform(cr, cfg, h)
        for b, level, rgb in items:
            surf, ox, oy = sprites.body(kind, u, rgb, level, b.dead, sf)
            cr.save()
            cr.translate(b.x, pivot_y)
            cr.rotate(self._angle(b, cfg))
            cr.set_source_surface(surf, -ox, -oy)
            cr.paint()
            cr.restore()
        cr.restore()


# --------------------------------------------------------------------------
# Motor de los efectos de pantalla (nieve, ventisca, copos, destellos)
# --------------------------------------------------------------------------

# px² de pantalla por copo con intensidad media: la cantidad se adapta al tamaño de la pantalla
EFFECT_BASE_AREA = {"nieve": 7000, "ventisca": 3200, "copos": 26000, "destellos": 9000}


class EffectEngine:
    """Copos y destellos sin ventana. Cada paso dice qué bloques de la pantalla cambiaron,
    para repintar solo esos (en vez de toda la pantalla en cada cuadro).
    """

    TILE = 64       # px: la pantalla se divide en bloques; solo se repintan los que tocan un copo
    ROT_STEPS = 30  # el copo grande tiene simetría de 60°: se prerrenderiza en 30 pasos de 2°

    def __init__(self):
        self.flakes = []
        self.prev = set()   # bloques ocupados en el cuadro anterior
        self.sig = None
        self.t = 0.0
        self.sprites = {}

    def reset(self, w, h, kind, density, scale=1.0):
        rng = random.Random()
        n = int(min(650, max(15, w * h / (scale * scale) / EFFECT_BASE_AREA[kind] * (0.25 + 1.5 * density))))
        self.flakes = []
        for _ in range(n):
            if kind == "copos":
                r = rng.uniform(5, 13)
                vy = rng.uniform(25, 55)
            elif kind == "destellos":
                r = rng.uniform(1.2, 3.2)
                vy = 0
            else:
                r = rng.uniform(1.0, 3.6)
                vy = (18 + r * 16) * (1.9 if kind == "ventisca" else 1.0)
            self.flakes.append([rng.uniform(0, w), rng.uniform(-h, h), r * scale, vy * scale,
                                rng.uniform(0, 6.28), rng.uniform(0.4, 1.6), rng.uniform(0.3, 1.0)])
        self.prev = set()

    @staticmethod
    def _extent(f, kind):
        if kind == "copos":
            return f[2] + 2
        if kind == "destellos":
            return f[2] * 5 + 2
        return (f[2] * 1.6 if f[2] > 2.4 else f[2]) + 2

    @staticmethod
    def _twinkle(f, t):
        return max(0.0, math.sin(t * f[5] * 1.7 + f[4])) ** 6

    def _tiles(self, kind, h, t):
        T = self.TILE
        out = set()
        for f in self.flakes:
            if kind == "destellos":
                if self._twinkle(f, t) < 0.02:
                    continue
                x, y = f[0], f[1] % h
            else:
                x, y = f[0], f[1]
            e = self._extent(f, kind)
            for ty in range(int((y - e) // T), int((y + e) // T) + 1):
                for tx in range(int((x - e) // T), int((x + e) // T) + 1):
                    out.add((tx, ty))
        return out

    def _fall(self, kind, w, h, t, dt, scale):
        """Mueve los copos y, en la misma pasada, junta los bloques que ocupan.

        Es el bucle más caliente del programa (cientos de copos, 60 veces por segundo):
        por eso usa variables locales y evita crear rangos cuando el copo cabe en un bloque.
        """
        if kind == "ventisca":
            wind = (110 + 60 * math.sin(t * 0.35)) * scale
        else:
            wind = 14 * math.sin(t * 0.2) * scale
        drift = 18 * scale
        T = self.TILE
        tiles = set()
        add = tiles.add
        sin = math.sin
        big = kind == "copos"
        for f in self.flakes:
            r = f[2]
            y = f[1] + f[3] * dt
            x = f[0] + (wind * (r / scale / 3.0) + drift * sin(t * f[5] + f[4])) * dt
            if y > h + 15 * scale:
                y = -15 * scale
                x = random.uniform(-w * 0.2, w)
            if x > w + 20 * scale:
                x -= w + 40 * scale
            elif x < -20 * scale:
                x += w + 40 * scale
            f[0], f[1] = x, y
            e = r + 2 if big else (r * 1.6 if r > 2.4 else r) + 2
            x0, x1 = int((x - e) // T), int((x + e) // T)
            y0, y1 = int((y - e) // T), int((y + e) // T)
            if x0 == x1 and y0 == y1:
                add((x0, y0))
            else:
                for ty in range(y0, y1 + 1):
                    for tx in range(x0, x1 + 1):
                        add((tx, ty))
        return tiles

    def step(self, t, dt, w, h, kind, density, scale=1.0):
        """Avanza hasta t. Devuelve "all" (repintar todo) o el conjunto de bloques (tx, ty) a repintar."""
        self.t = t
        sig = (w, h, kind, round(density, 2), scale)
        if sig != self.sig:
            self.sig = sig
            self.reset(w, h, kind, density, scale)
            return "all"
        now = self._tiles(kind, h, t) if kind == "destellos" else self._fall(kind, w, h, t, dt, scale)
        dirty = now | self.prev
        self.prev = now
        return dirty

    @classmethod
    def tile_rects(cls, tiles):
        """Agrupa los bloques contiguos de cada fila: [(x, y, ancho, alto), …] en px."""
        T = cls.TILE
        rows = {}
        for tx, ty in tiles:
            rows.setdefault(ty, []).append(tx)
        rects = []
        for ty, xs in rows.items():
            xs.sort()
            start = prev = xs[0]
            for tx in [*xs[1:], None]:
                if tx is not None and tx == prev + 1:
                    prev = tx
                    continue
                rects.append((start * T, ty * T, (prev - start + 1) * T, T))
                if tx is not None:
                    start = prev = tx
        return rects

    def _sprite(self, r, alpha):
        """Copo suave prerrenderizado (más barato que un degradado por copo y cuadro)."""
        key = (round(r * 4), round(alpha * 10))
        sp = self.sprites.get(key)
        if sp is None:
            R = r * 1.6
            size = math.ceil(R * 2) + 2
            sp = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
            c = cairo.Context(sp)
            cx = size / 2
            g = cairo.RadialGradient(cx, cx, 0, cx, cx, R)
            g.add_color_stop_rgba(0, 1, 1, 1, 0.9 * alpha + 0.1)
            g.add_color_stop_rgba(0.6, 1, 1, 1, 0.5 * alpha)
            g.add_color_stop_rgba(1, 1, 1, 1, 0)
            c.set_source(g)
            c.arc(cx, cx, R, 0, 6.3)
            c.fill()
            self.sprites[key] = sp
        return sp

    def _copo(self, r, alpha, ang):
        """Copo de seis puntas prerrenderizado en 30 ángulos (antes: 18 trazos por copo y cuadro)."""
        rq, aq = round(r), round(alpha * 10) / 10
        step = round((ang % (math.pi / 3)) / (math.pi / 3) * self.ROT_STEPS) % self.ROT_STEPS
        key = ("c", rq, aq, step)
        sp = self.sprites.get(key)
        if sp is None:
            if len(self.sprites) > 3000:
                self.sprites.clear()
            size = 2 * rq + 5
            sp = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
            c = cairo.Context(sp)
            c.translate(size / 2, size / 2)
            c.rotate(step * (math.pi / 3) / self.ROT_STEPS)
            c.set_line_cap(cairo.LINE_CAP_ROUND)
            c.set_source_rgba(1, 1, 1, 0.55 + 0.35 * aq)
            c.set_line_width(max(1.0, rq * 0.13))
            for _k in range(6):
                c.move_to(0, 0)
                c.line_to(0, -rq)
                c.move_to(0, -rq * 0.55)
                c.line_to(rq * 0.25, -rq * 0.78)
                c.move_to(0, -rq * 0.55)
                c.line_to(-rq * 0.25, -rq * 0.78)
                c.rotate(math.pi / 3)
            c.stroke()
            self.sprites[key] = sp
        return sp

    def draw(self, cr, h, kind):
        """Dibuja los copos en cr. Solo limpia y pinta lo que cae dentro del recorte de cr."""
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cx0, cy0, cx1, cy1 = cr.clip_extents()
        t = self.t
        if kind == "destellos":
            cr.set_line_width(0.8)
            for f in self.flakes:
                a = self._twinkle(f, t)
                if a < 0.02:
                    continue
                x, y, r = f[0], (f[1] % h), f[2] * (0.6 + a)
                if x + r * 3 < cx0 or x - r * 3 > cx1 or y + r * 3 < cy0 or y - r * 3 > cy1:
                    continue
                cr.set_source_rgba(1, 0.96, 0.85, 0.9 * a * f[6])
                cr.move_to(x - r * 3, y)
                cr.line_to(x + r * 3, y)
                cr.move_to(x, y - r * 3)
                cr.line_to(x, y + r * 3)
                cr.stroke()
                cr.arc(x, y, r * 0.7, 0, 6.3)
                cr.fill()
            return
        if kind == "copos":
            for f in self.flakes:
                x, y, r = f[0], f[1], f[2]
                if x + r < cx0 or x - r > cx1 or y + r < cy0 or y - r > cy1:
                    continue
                sp = self._copo(r, f[6], t * 0.4 * (f[5] - 1) + f[4])
                off = sp.get_width() / 2
                cr.set_source_surface(sp, x - off, y - off)
                cr.paint()
            return
        # nieve / ventisca: los grandes desde imágenes en memoria; los pequeños,
        # agrupados por brillo en un solo trazo (un relleno por grupo, no uno por copo)
        groups = {}
        for f in self.flakes:
            x, y, r = f[0], f[1], f[2]
            e = r * 1.6
            if x + e < cx0 or x - e > cx1 or y + e < cy0 or y - e > cy1:
                continue
            if r > 2.4:
                sp = self._sprite(r, f[6])
                off = sp.get_width() / 2
                cr.set_source_surface(sp, x - off, y - off)
                cr.paint()
            else:
                groups.setdefault(round(0.55 + 0.4 * f[6], 1), []).append((x, y, r))
        for alpha, pts in groups.items():
            cr.set_source_rgba(1, 1, 1, alpha)
            for x, y, r in pts:
                cr.new_sub_path()
                cr.arc(x, y, r, 0, 6.3)
            cr.fill()


def render_bulb_icon(rgb, v, size):
    """Bombillo C9 para el icono de la bandeja (superficie ARGB32 de size×size)."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    u = size * 0.40
    cr.translate(size / 2, size * 0.10)
    cr.rotate(math.radians(-8))
    draw_glow(cr, "c9", u, rgb, v * 0.8)
    draw_bulb(cr, "c9", u, rgb, v)
    surf.flush()
    return surf


# --------------------------------------------------------------------------
# Sonidos sintetizados (villancico incluido y efectos)
# --------------------------------------------------------------------------

def _write_wav(path, samples, rate):
    """Normaliza y guarda en WAV de 16 bits. Escribe a un temporal y lo renombra: quien
    lo lea (la música arranca mientras esto aún corre) nunca ve un archivo a medias."""
    peak = max(1e-6, *(abs(s) for s in samples))
    norm = 0.85 / peak
    data = struct.pack(f"<{len(samples)}h", *(int(max(-1, min(1, s * norm)) * 32767) for s in samples))
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    os.replace(tmp, path)


def _bell(buf, rate, start, freq, dur, amp, decay=3.2, partials=((1, 1), (2, 0.45), (3, 0.2), (4.2, 0.12))):
    n0 = int(start * rate)
    n = int(dur * rate)
    two_pi = 2 * math.pi
    for k in range(n):
        i = n0 + k
        if i >= len(buf):
            break
        tt = k / rate
        env = math.exp(-tt * decay) * min(1.0, tt / 0.004)
        s = 0.0
        for mult, a in partials:
            s += a * math.sin(two_pi * freq * mult * tt) * math.exp(-tt * decay * (mult - 1) * 0.6)
        buf[i] += amp * env * s


SOUNDS_READY = threading.Event()  # se marca cuando generate_sounds() terminó (bien o mal)


def generate_sounds(cache_dir):
    """Sintetiza el villancico y los efectos la primera vez (≈4 s de CPU) y los deja en caché."""
    try:
        _generate_sounds(cache_dir)
    except OSError as e:
        print(f"Luces Navideñas: no se pudieron crear los sonidos: {e}", file=sys.stderr)
    finally:
        SOUNDS_READY.set()


def _generate_sounds(cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    rate = 22050
    song = os.path.join(cache_dir, "jingle-bells.wav")
    if not os.path.exists(song):
        N = {"C": 523.25, "D": 587.33, "E": 659.25, "F": 698.46, "G": 783.99}
        mel = ("E1 E1 E2 E1 E1 E2 E1 G1 C1.5 D.5 E4 F1 F1 F1.5 F.5 F1 E1 E1 E.5 E.5 E1 D1 D1 E1 D2 G2 "  # noqa: SIM905
               "E1 E1 E2 E1 E1 E2 E1 G1 C1.5 D.5 E4 F1 F1 F1.5 F.5 F1 E1 E1 E.5 E.5 G1 G1 F1 D1 C4").split()
        bass = [130.81, 130.81, 130.81, 130.81, 174.61, 130.81, 146.83, 196.0,
                130.81, 130.81, 130.81, 130.81, 174.61, 130.81, 196.0, 130.81]
        beat = 0.30
        total = 64 * beat + 2.0
        buf = [0.0] * int(total * rate)
        t = 0.2
        for tok in mel:
            note, d = tok[0], float(tok[1:])
            _bell(buf, rate, t, N[note], 1.3, 0.55)
            t += d * beat
        for m, f in enumerate(bass):
            for b in (0, 2):
                _bell(buf, rate, 0.2 + (m * 4 + b) * beat, f, 0.9, 0.28, decay=4.5,
                      partials=((1, 1), (2, 0.3)))
        _write_wav(song, buf, rate)
    tick = os.path.join(cache_dir, "tic.wav")
    if not os.path.exists(tick):
        buf = [0.0] * int(0.06 * rate)
        _bell(buf, rate, 0, 2600, 0.06, 0.4, decay=70, partials=((1, 1), (1.5, 0.5)))
        _write_wav(tick, buf, rate)
    for name, notes in (("encender.wav", (1046.5, 1568.0)), ("apagar.wav", (1568.0, 1046.5))):
        p = os.path.join(cache_dir, name)
        if not os.path.exists(p):
            buf = [0.0] * int(0.9 * rate)
            _bell(buf, rate, 0.0, notes[0], 0.6, 0.5, decay=6)
            _bell(buf, rate, 0.12, notes[1], 0.75, 0.5, decay=5)
            _write_wav(p, buf, rate)


def list_songs(folder, extensions):
    """[(ruta, título)] del villancico incluido y de los archivos de música de la carpeta."""
    songs = [(BUILTIN_SONG, BUILTIN_SONG_TITLE)]
    try:
        for f in sorted(os.listdir(folder), key=str.lower):
            if f.lower().endswith(extensions):
                songs.append((os.path.join(folder, f), f))
    except OSError:
        pass
    return songs


# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------

# Valores admitidos: un config.json editado a mano (o de otra versión) no debe tumbar el programa
CHOICES = {
    "mode": {k for k, _ in MODES},
    "bulb": {k for k, _ in BULBS},
    "palette": set(PALETTE_MAP),
    "effect": {k for k, _ in EFFECTS},
    "fluidez": {"auto", "max", "eco"},
    "position": {"top", "bottom"},
    "monitors": {"all", "primary"},
}
RANGES = {"rate": (0.0, 1.0), "size": (0.6, 1.8), "volume": (0.0, 1.0), "fx_volume": (0.0, 1.0),
          "effect_density": (0.0, 1.0)}


def sanitize_config(data):
    """Mezcla lo leído con DEFAULTS: ignora claves desconocidas, tipos o valores inválidos."""
    cfg = copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict):
        return cfg
    for k, raw in data.items():
        if k not in cfg:
            continue
        default = cfg[k]
        val = raw
        if isinstance(default, float) and isinstance(val, int) and not isinstance(val, bool):
            val = float(val)
        if type(val) is not type(default):
            continue
        if k in CHOICES and val not in CHOICES[k]:
            continue
        if k in RANGES:
            lo, hi = RANGES[k]
            val = min(hi, max(lo, val))
        if k == "music_skip":
            val = [x for x in val if isinstance(x, str)]
        cfg[k] = val
    return cfg


def write_json_atomic(path, data):
    """Escribe a un temporal y lo renombra: un corte o un error nunca deja el archivo a medias."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
