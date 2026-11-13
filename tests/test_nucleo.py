"""Pruebas del núcleo compartido (Linux y Windows): animación, configuración, sprites, sonidos.

Se ejecutan con:  python -m unittest discover -s tests
Solo necesitan Python y pycairo: no abren ventanas ni suenan.
"""
import json
import math
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nucleo as N

_TMP = tempfile.mkdtemp(prefix="luces-pruebas-")


def cfg(**over):
    c = dict(N.DEFAULTS)
    c.update(over)
    return c


class Animacion(unittest.TestCase):
    def test_velocidad_va_de_lento_a_rapido(self):
        self.assertAlmostEqual(N.rate_to_interval(0), 1.57, places=2)
        self.assertAlmostEqual(N.rate_to_interval(1), 0.07, places=2)
        vals = [N.rate_to_interval(r / 10) for r in range(11)]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_modos_de_parpadeo(self):
        self.assertEqual([N.blink_target("chase", i, 0, False) for i in range(6)], [1, 0, 0, 1, 0, 0])
        self.assertEqual([N.blink_target("chase", i, 1, False) for i in range(4)], [0, 1, 0, 0])
        self.assertEqual([N.blink_target("alternate", i, 0, False) for i in range(4)], [1, 0, 1, 0])
        self.assertEqual([N.blink_target("all", 3, s, False) for s in range(4)], [1, 0, 1, 0])
        self.assertTrue(all(N.blink_target("none", i, s, True) == 1 for i in range(9) for s in range(9)))
        # «algunas parpadean»: las que no son intermitentes quedan siempre encendidas
        self.assertTrue(all(N.blink_target("some", i, s, False) == 1 for i in range(9) for s in range(9)))
        self.assertIn(0.0, [N.blink_target("some", 2, s, True) for s in range(40)])

    def test_modos_continuos_en_rango(self):
        for mode, lo in (("wave", 0.12), ("breathe", 0.10)):
            vals = [N.continuous_target(mode, i, t / 10, 0.5) for i in range(5) for t in range(200)]
            self.assertGreaterEqual(min(vals), lo - 1e-9)
            self.assertLessEqual(max(vals), 1.0 + 1e-9)

    def test_desvanecido_llega_sin_pasarse(self):
        v, k = 0.0, 1 - math.exp(-0.016 / N.FADE_TIME)
        for _ in range(200):
            nv = N.fade_toward(v, 1.0, k)
            self.assertGreaterEqual(nv, v)
            self.assertLessEqual(nv, 1.0)
            v = nv
        self.assertEqual(v, 1.0)  # termina exactamente: así la tira puede dormirse

    def test_balanceo_se_mueve_menos_de_un_tercio_de_pixel_por_cuadro(self):
        for size in (0.6, 1.0, 1.8):
            dt = N.sway_redraw_interval(size)
            tip = N.BULB_LENGTH * N.BULB_UNIT * size
            worst = max(abs(N.sway_angle(t + dt, 0) - N.sway_angle(t, 0)) * tip
                        for t in (x / 100 for x in range(500)))
            self.assertLessEqual(worst, N.SWAY_MAX_STEP_PX + 1e-6)
            self.assertGreater(dt, 1 / 60)  # y redibuja bastante menos que 60 veces por segundo
        self.assertLess(N.sway_redraw_interval(1.8), N.sway_redraw_interval(0.6))

    def test_arcoiris_avanza_junto_y_despacio(self):
        changes = sum(N.rainbow_step(t / 1000) != N.rainbow_step((t - 1) / 1000) for t in range(1, 10001))
        self.assertAlmostEqual(changes / 10, N.RAINBOW_SPEED * N.RAINBOW_STEPS, delta=0.2)  # ≈6 por segundo
        for i in range(30):
            d = (N.rainbow_hue(11, i) - N.rainbow_hue(10, i)) % 1.0
            self.assertAlmostEqual(d, 1 / N.RAINBOW_STEPS)
            self.assertTrue(0 <= N.rainbow_hue(10, i) < 1)

    def test_distribucion_determinista(self):
        c = cfg(burnt=True, broken=True, seed=1994)
        sp1, b1 = N.layout_bulbs(1920, c)
        sp2, b2 = N.layout_bulbs(1920, c)
        def key(bulbs):
            return [(b.x, b.angle, b.dead, b.flasher) for b in bulbs]
        self.assertEqual(key(b1), key(b2))
        self.assertAlmostEqual(sp1 * len(b1), 1920)
        self.assertEqual(sp1, sp2)
        _, otra = N.layout_bulbs(1920, cfg(burnt=True, broken=True, seed=7))
        self.assertNotEqual([b.dead for b in b1], [b.dead for b in otra])
        _, sanos = N.layout_bulbs(1920, cfg(burnt=False, broken=False))
        self.assertTrue(all(b.dead is None for b in sanos))

    def test_mas_luces_las_junta(self):
        _, normal = N.layout_bulbs(2560, cfg(more=False))
        _, mas = N.layout_bulbs(2560, cfg(more=True))
        self.assertGreater(len(mas), len(normal))


class Configuracion(unittest.TestCase):
    def test_valores_invalidos_se_ignoran(self):
        c = N.sanitize_config({"mode": "disco", "bulb": "neon", "size": "grande", "palette": 3,
                               "power": 1, "desconocida": True, "music_skip": ["a", 2, "b"]})
        self.assertEqual(c["mode"], N.DEFAULTS["mode"])
        self.assertEqual(c["bulb"], N.DEFAULTS["bulb"])
        self.assertEqual(c["size"], N.DEFAULTS["size"])
        self.assertEqual(c["palette"], N.DEFAULTS["palette"])
        self.assertIs(c["power"], N.DEFAULTS["power"])  # 1 no es un booleano
        self.assertNotIn("desconocida", c)
        self.assertEqual(c["music_skip"], ["a", "b"])

    def test_rangos_y_enteros(self):
        c = N.sanitize_config({"size": 9, "volume": -2, "rate": 1})
        self.assertEqual(c["size"], 1.8)
        self.assertEqual(c["volume"], 0.0)
        self.assertIsInstance(c["rate"], float)
        self.assertEqual(N.sanitize_config({"rate": True})["rate"], N.DEFAULTS["rate"])

    def test_no_es_un_diccionario(self):
        self.assertEqual(N.sanitize_config([1, 2]), N.DEFAULTS)

    def test_valores_buenos_se_conservan(self):
        user = {"mode": "alternate", "palette": "retro", "size": 1.154, "sway": True, "seed": 42}
        c = N.sanitize_config(user)
        for k, v in user.items():
            self.assertEqual(c[k], v)

    def test_escritura_atomica(self):
        path = os.path.join(_TMP, "atomico", "config.json")
        N.write_json_atomic(path, {"a": 1})
        N.write_json_atomic(path, {"a": 2, "texto": "ñandú"})
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 2, "texto": "ñandú"})
        self.assertFalse(os.path.exists(path + ".tmp"))


class Sprites(unittest.TestCase):
    def test_tope_de_memoria_y_lru(self):
        sp = N.BulbSprites(budget=600_000)
        first = sp.glow("c9", 23, (1.0, 0.0, 0.0), 32, 1)
        for level in range(1, 33):
            sp.glow("c9", 23, (1.0, 0.0, 0.0), 32, 1)  # el primero se sigue usando
            sp.body("c9", 23, (0.0, 1.0, 0.0), level, None, 1)
        self.assertLessEqual(sp.bytes, sp.budget)
        self.assertIs(sp.glow("c9", 23, (1.0, 0.0, 0.0), 32, 1), first)  # el más usado sobrevive
        self.assertEqual(sp.bytes, sum(v[0].get_stride() * v[0].get_height() for v in sp.cache.values()))

    def test_resplandor_centrado(self):
        surf, size = N.SPRITES.glow("c9", 23, (1.0, 0.5, 0.0), 32, 1)
        self.assertEqual(surf.get_width(), math.ceil(size))
        data = surf.get_data()
        stride, c = surf.get_stride(), int(size // 2)
        alpha = data[c * stride + c * 4 + 3]
        self.assertGreater(alpha, data[c * stride + 3])  # más brillo en el centro que en el borde


class Sonidos(unittest.TestCase):
    def test_wav_atomico(self):
        path = os.path.join(_TMP, "tono.wav")
        N._write_wav(path, [math.sin(i / 10) for i in range(2205)], 22050)
        with wave.open(path, "rb") as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getnframes()), (1, 2, 2205))
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_generar_sonidos(self):
        cache = os.path.join(_TMP, "sonidos")
        N.SOUNDS_READY.clear()
        N.generate_sounds(cache)
        self.assertTrue(N.SOUNDS_READY.is_set())
        for name in ("jingle-bells.wav", "tic.wav", "encender.wav", "apagar.wav"):
            with wave.open(os.path.join(cache, name), "rb") as w:
                self.assertGreater(w.getnframes(), 0)

    def test_lista_de_canciones(self):
        folder = os.path.join(_TMP, "musica")
        os.makedirs(folder, exist_ok=True)
        for name in ("b.mp3", "A.mid", "nota.txt"):
            Path(folder, name).write_bytes(b"")
        songs = N.list_songs(folder, (".mp3", ".mid"))
        self.assertEqual([t for _p, t in songs], [N.BUILTIN_SONG_TITLE, "A.mid", "b.mp3"])
        self.assertEqual(N.list_songs(os.path.join(_TMP, "no-existe"), (".mp3",))[0][0], N.BUILTIN_SONG)


class Motores(unittest.TestCase):
    def test_tira_quieta_duerme_hasta_el_parpadeo(self):
        e = N.StripEngine()
        c = cfg(mode="none", sway=False, palette="retro", smooth=False)
        first = e.tick(10.0, c, 1200)
        self.assertEqual(first.redraw, "all")
        f = e.tick(10.05, c, 1200)
        self.assertIsNone(f.redraw)
        self.assertFalse(f.busy)
        self.assertGreater(f.wait, 0.5)  # nada que hacer: puede dormir

    def test_parpadeo_pide_franjas_o_todo_y_avisa_del_proximo(self):
        e = N.StripEngine()
        c = cfg(mode="chase", rate=0.5, smooth=False, sway=False, palette="retro")
        iv = N.rate_to_interval(0.5)
        e.tick(0.01, c, 2400)
        f = e.tick(0.02, c, 2400)
        self.assertIsNone(f.redraw)
        self.assertAlmostEqual(f.wait, iv - 0.02 + 0.002, places=3)  # duerme hasta el próximo paso
        f = e.tick(iv + 0.001, c, 2400)  # siguiente paso de la persecución
        self.assertIn(f.redraw, ("all", "spans"))

    def test_desvanecido_mantiene_ocupado(self):
        e = N.StripEngine()
        c = cfg(mode="all", rate=0.0, smooth=True, sway=False, palette="retro")
        e.tick(0.0, c, 1200)
        f = e.tick(0.016, c, 1200)
        self.assertTrue(f.busy)

    def test_balanceo_cada_intervalo(self):
        e = N.StripEngine()
        c = cfg(mode="none", sway=True, smooth=False, palette="retro")
        e.tick(5.0, c, 1200)
        iv = N.sway_redraw_interval(c["size"])
        f = e.tick(5.0 + iv / 2, c, 1200)
        self.assertIsNone(f.redraw)
        self.assertAlmostEqual(f.wait, iv / 2, places=3)
        self.assertEqual(e.tick(5.0 + iv, c, 1200).redraw, "all")

    def test_tope_de_cuadros(self):
        e = N.StripEngine()
        e.tick(1.0, cfg(), 800)
        self.assertFalse(e.frame_due(1.005, 60))
        self.assertTrue(e.frame_due(1.0 + 1 / 60, 60))
        e.force = True
        self.assertTrue(e.frame_due(1.001, 60))

    def test_dibujo_respeta_el_recorte(self):
        import cairo
        e = N.StripEngine()
        c = cfg(mode="none", smooth=False)
        e.tick(1.0, c, 600)
        e.tick(1.1, c, 600)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 600, N.strip_height(c))
        cr = cairo.Context(surf)
        cr.set_source_rgba(1, 0, 0, 1)
        cr.paint()  # fondo rojo: fuera del recorte debe quedar intacto
        cr.rectangle(0, 0, 300, surf.get_height())
        cr.clip()
        e.draw(cr, 600, surf.get_height(), c)
        surf.flush()
        d, stride = surf.get_data(), surf.get_stride()
        self.assertEqual(bytes(d[stride * 2 + 590 * 4: stride * 2 + 591 * 4]), b"\x00\x00\xff\xff")
        self.assertNotEqual(bytes(d[stride * 2 + 10 * 4: stride * 2 + 11 * 4]), b"\x00\x00\xff\xff")

    def test_nieve_primero_todo_luego_bloques(self):
        fx = N.EffectEngine()
        self.assertEqual(fx.step(0.0, 0.016, 1280, 720, "nieve", 0.5), "all")
        dirty = fx.step(0.016, 0.016, 1280, 720, "nieve", 0.5)
        self.assertIsInstance(dirty, set)
        self.assertTrue(dirty)
        self.assertEqual(fx.step(0.03, 0.016, 1280, 720, "copos", 0.5), "all")  # cambio de tipo

    def test_escala_agranda_los_copos(self):
        a, b = N.EffectEngine(), N.EffectEngine()
        a.step(0, 0.016, 1920, 1080, "copos", 0.5, scale=1.0)
        b.step(0, 0.016, 1920, 1080, "copos", 0.5, scale=2.0)
        mean = lambda e: sum(f[2] for f in e.flakes) / len(e.flakes)  # noqa: E731
        self.assertGreater(mean(b), 1.6 * mean(a))
        self.assertLess(len(b.flakes), len(a.flakes))  # misma densidad visual: menos copos por píxel

    def test_bloques_contiguos_se_juntan(self):
        T = N.EffectEngine.TILE
        rects = sorted(N.EffectEngine.tile_rects({(0, 0), (1, 0), (2, 0), (5, 0), (1, 3)}))
        self.assertEqual(rects, [(0, 0, 3 * T, T), (T, 3 * T, T, T), (5 * T, 0, T, T)])

    def test_icono_de_bandeja(self):
        surf = N.render_bulb_icon((1.0, 0.2, 0.2), 1.0, 32)
        self.assertEqual((surf.get_width(), surf.get_height()), (32, 32))
        self.assertTrue(any(surf.get_data()[3::4]))


if __name__ == "__main__":
    unittest.main()
