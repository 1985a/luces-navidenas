"""Pruebas de la lógica sin interfaz: animación, configuración, sprites y audio.

Se ejecutan con:  python -m unittest discover -s tests
No abren ventanas ni suenan: el audio va a procesos falsos y los archivos a carpetas temporales.
"""
import importlib.machinery
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "luces-navidenas"
_TMP = tempfile.mkdtemp(prefix="luces-pruebas-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")  # nunca tocar la configuración real
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP, "cache")


def _load():
    loader = importlib.machinery.SourceFileLoader("luces", str(SCRIPT))
    spec = importlib.util.spec_from_loader("luces", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


L = _load()


def cfg(**over):
    c = dict(L.DEFAULTS)
    c.update(over)
    return c


class Animacion(unittest.TestCase):
    def test_velocidad_va_de_lento_a_rapido(self):
        self.assertAlmostEqual(L.rate_to_interval(0), 1.57, places=2)
        self.assertAlmostEqual(L.rate_to_interval(1), 0.07, places=2)
        vals = [L.rate_to_interval(r / 10) for r in range(11)]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_modos_de_parpadeo(self):
        self.assertEqual([L.blink_target("chase", i, 0, False) for i in range(6)], [1, 0, 0, 1, 0, 0])
        self.assertEqual([L.blink_target("chase", i, 1, False) for i in range(4)], [0, 1, 0, 0])
        self.assertEqual([L.blink_target("alternate", i, 0, False) for i in range(4)], [1, 0, 1, 0])
        self.assertEqual([L.blink_target("all", 3, s, False) for s in range(4)], [1, 0, 1, 0])
        self.assertTrue(all(L.blink_target("none", i, s, True) == 1 for i in range(9) for s in range(9)))
        # «algunas parpadean»: las que no son intermitentes quedan siempre encendidas
        self.assertTrue(all(L.blink_target("some", i, s, False) == 1 for i in range(9) for s in range(9)))
        self.assertIn(0.0, [L.blink_target("some", 2, s, True) for s in range(40)])

    def test_modos_continuos_en_rango(self):
        for mode, lo in (("wave", 0.12), ("breathe", 0.10)):
            vals = [L.continuous_target(mode, i, t / 10, 0.5) for i in range(5) for t in range(200)]
            self.assertGreaterEqual(min(vals), lo - 1e-9)
            self.assertLessEqual(max(vals), 1.0 + 1e-9)

    def test_desvanecido_llega_sin_pasarse(self):
        v, k = 0.0, 1 - math.exp(-0.016 / L.FADE_TIME)
        for _ in range(200):
            nv = L.fade_toward(v, 1.0, k)
            self.assertGreaterEqual(nv, v)
            self.assertLessEqual(nv, 1.0)
            v = nv
        self.assertEqual(v, 1.0)  # termina exactamente: así la tira puede dormirse

    def test_balanceo_se_mueve_menos_de_un_tercio_de_pixel_por_cuadro(self):
        for size in (0.6, 1.0, 1.8):
            dt = L.sway_redraw_interval(size)
            tip = L.BULB_LENGTH * L.BULB_UNIT * size
            worst = max(abs(L.sway_angle(t + dt, 0) - L.sway_angle(t, 0)) * tip
                        for t in (x / 100 for x in range(500)))
            self.assertLessEqual(worst, L.SWAY_MAX_STEP_PX + 1e-6)
            self.assertGreater(dt, 1 / 60)  # y redibuja bastante menos que 60 veces por segundo
        self.assertLess(L.sway_redraw_interval(1.8), L.sway_redraw_interval(0.6))

    def test_arcoiris_avanza_junto_y_despacio(self):
        changes = sum(L.rainbow_step(t / 1000) != L.rainbow_step((t - 1) / 1000) for t in range(1, 10001))
        self.assertAlmostEqual(changes / 10, L.RAINBOW_SPEED * L.RAINBOW_STEPS, delta=0.2)  # ≈6 por segundo
        for i in range(30):
            d = (L.rainbow_hue(11, i) - L.rainbow_hue(10, i)) % 1.0
            self.assertAlmostEqual(d, 1 / L.RAINBOW_STEPS)
            self.assertTrue(0 <= L.rainbow_hue(10, i) < 1)

    def test_distribucion_determinista(self):
        c = cfg(burnt=True, broken=True, seed=1994)
        sp1, b1 = L.layout_bulbs(1920, c)
        sp2, b2 = L.layout_bulbs(1920, c)
        def key(bulbs):
            return [(b.x, b.angle, b.dead, b.flasher) for b in bulbs]
        self.assertEqual(key(b1), key(b2))
        self.assertAlmostEqual(sp1 * len(b1), 1920)
        self.assertEqual(sp1, sp2)
        _, otra = L.layout_bulbs(1920, cfg(burnt=True, broken=True, seed=7))
        self.assertNotEqual([b.dead for b in b1], [b.dead for b in otra])
        _, sanos = L.layout_bulbs(1920, cfg(burnt=False, broken=False))
        self.assertTrue(all(b.dead is None for b in sanos))

    def test_mas_luces_las_junta(self):
        _, normal = L.layout_bulbs(2560, cfg(more=False))
        _, mas = L.layout_bulbs(2560, cfg(more=True))
        self.assertGreater(len(mas), len(normal))


class Configuracion(unittest.TestCase):
    def test_valores_invalidos_se_ignoran(self):
        c = L.sanitize_config({"mode": "disco", "bulb": "neon", "size": "grande", "palette": 3,
                               "power": 1, "desconocida": True, "music_skip": ["a", 2, "b"]})
        self.assertEqual(c["mode"], L.DEFAULTS["mode"])
        self.assertEqual(c["bulb"], L.DEFAULTS["bulb"])
        self.assertEqual(c["size"], L.DEFAULTS["size"])
        self.assertEqual(c["palette"], L.DEFAULTS["palette"])
        self.assertIs(c["power"], L.DEFAULTS["power"])  # 1 no es un booleano
        self.assertNotIn("desconocida", c)
        self.assertEqual(c["music_skip"], ["a", "b"])

    def test_rangos_y_enteros(self):
        c = L.sanitize_config({"size": 9, "volume": -2, "rate": 1})
        self.assertEqual(c["size"], 1.8)
        self.assertEqual(c["volume"], 0.0)
        self.assertIsInstance(c["rate"], float)
        self.assertEqual(L.sanitize_config({"rate": True})["rate"], L.DEFAULTS["rate"])

    def test_no_es_un_diccionario(self):
        self.assertEqual(L.sanitize_config([1, 2]), L.DEFAULTS)

    def test_valores_buenos_se_conservan(self):
        user = {"mode": "alternate", "palette": "retro", "size": 1.154, "sway": True, "seed": 42}
        c = L.sanitize_config(user)
        for k, v in user.items():
            self.assertEqual(c[k], v)

    def test_escritura_atomica(self):
        path = os.path.join(_TMP, "atomico", "config.json")
        L.write_json_atomic(path, {"a": 1})
        L.write_json_atomic(path, {"a": 2, "texto": "ñandú"})
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 2, "texto": "ñandú"})
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_exec_del_autoarranque(self):
        self.assertEqual(L.desktop_exec_quote("/usr/bin/luces-navidenas"), "/usr/bin/luces-navidenas")
        self.assertEqual(L.desktop_exec_quote("/home/a b/luces"), '"/home/a b/luces"')
        self.assertEqual(L.desktop_exec_quote('/x/$a"b'), '"/x/\\$a\\"b"')


class Sprites(unittest.TestCase):
    def test_tope_de_memoria_y_lru(self):
        sp = L.BulbSprites(budget=600_000)
        first = sp.glow("c9", 23, (1.0, 0.0, 0.0), 32, 1)
        for level in range(1, 33):
            sp.glow("c9", 23, (1.0, 0.0, 0.0), 32, 1)  # el primero se sigue usando
            sp.body("c9", 23, (0.0, 1.0, 0.0), level, None, 1)
        self.assertLessEqual(sp.bytes, sp.budget)
        self.assertIs(sp.glow("c9", 23, (1.0, 0.0, 0.0), 32, 1), first)  # el más usado sobrevive
        self.assertEqual(sp.bytes, sum(v[0].get_stride() * v[0].get_height() for v in sp.cache.values()))

    def test_resplandor_centrado(self):
        surf, size = L.SPRITES.glow("c9", 23, (1.0, 0.5, 0.0), 32, 1)
        self.assertEqual(surf.get_width(), math.ceil(size))
        data = surf.get_data()
        stride, c = surf.get_stride(), int(size // 2)
        alpha = data[c * stride + c * 4 + 3]
        self.assertGreater(alpha, data[c * stride + 3])  # más brillo en el centro que en el borde


class Audio(unittest.TestCase):
    def test_volumen(self):
        import array
        a = array.array("h", [1000, -1000, 32767, -32768])
        raw = a.tobytes()
        self.assertEqual(L.MusicPlayer._scale(raw, 1.0), raw)
        self.assertEqual(L.MusicPlayer._scale(raw, 0.0), bytes(len(raw)))
        half = array.array("h")
        half.frombytes(L.MusicPlayer._scale(raw, 0.5))
        self.assertEqual(list(half), [500, -500, 16383, -16384])
        self.assertEqual(len(L.MusicPlayer._scale(raw + b"\x01", 0.5)), len(raw))  # byte suelto fuera

    def test_wav_atomico(self):
        path = os.path.join(_TMP, "tono.wav")
        L._write_wav(path, [math.sin(i / 10) for i in range(2205)], 22050)
        with wave.open(path, "rb") as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getnframes()), (1, 2, 2205))
        self.assertFalse(os.path.exists(path + ".tmp"))

    def _children(self):
        out = subprocess.run(["ps", "--ppid", str(os.getpid()), "-o", "pid=,stat="],
                             capture_output=True, text=True, check=False).stdout
        return [line.split() for line in out.splitlines() if line.strip()]

    def test_detener_la_musica_no_deja_procesos(self):
        song = os.path.join(_TMP, "larga.wav")
        L._write_wav(song, [math.sin(i / 7) for i in range(22050 * 30)], 22050)
        orig = L.raw_output_for
        sink = [sys.executable, "-c", "import sys\nwhile sys.stdin.buffer.read(4096): pass"]  # pw-cat falso
        L.raw_output_for = lambda _rate, _ch: sink
        try:
            m = L.MusicPlayer()
            L.SOUNDS_READY.set()
            m.set_tracks([song])
            m.start()
            time.sleep(0.6)
            self.assertTrue(m.running())
            self.assertTrue(m.procs)
            t0 = time.monotonic()
            m.stop()
            self.assertLess(time.monotonic() - t0, 0.5)  # la interfaz no se queda esperando
            self.assertFalse(m.running())
            self.assertEqual(m.procs, [])
            time.sleep(0.1)
            self.assertEqual([c for c in self._children() if c[0] != str(os.getpid())
                              and not c[1].startswith("R")], [])  # ni vivos ni zombis
        finally:
            L.raw_output_for = orig

    def test_efectos_recogen_sus_procesos(self):
        fx = L.SoundFx()
        orig = L.wav_command
        L.wav_command = lambda _path, _vol: ["true"]
        try:
            os.makedirs(L.CACHE_DIR, exist_ok=True)
            Path(L.CACHE_DIR, "tic.wav").write_bytes(b"x")
            for _ in range(5):
                fx.play("tic.wav")
                time.sleep(0.05)
            fx.play("tic.wav")
            self.assertLessEqual(len(fx.procs), 2)  # los terminados se recogieron
            for p in fx.procs:
                p.wait()
        finally:
            L.wav_command = orig


if __name__ == "__main__":
    unittest.main()
