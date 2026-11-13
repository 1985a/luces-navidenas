"""Pruebas de la parte de Linux: procesos de audio y autoarranque.

Se saltan si no hay GTK/PyGObject (p. ej. al compilar en Windows).
"""
import importlib.machinery
import importlib.util
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nucleo as N

_TMP = tempfile.mkdtemp(prefix="luces-pruebas-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")  # nunca tocar la configuración real
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP, "cache")

try:
    import gi
except ImportError:
    gi = None


def _load():
    loader = importlib.machinery.SourceFileLoader("luces", str(ROOT / "luces-navidenas"))
    spec = importlib.util.spec_from_loader("luces", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


L = _load() if gi else None


@unittest.skipIf(gi is None, "necesita PyGObject (solo Linux)")
class Autoarranque(unittest.TestCase):
    def test_exec_del_autoarranque(self):
        self.assertEqual(L.desktop_exec_quote("/usr/bin/luces-navidenas"), "/usr/bin/luces-navidenas")
        self.assertEqual(L.desktop_exec_quote("/home/a b/luces"), '"/home/a b/luces"')
        self.assertEqual(L.desktop_exec_quote('/x/$a"b'), '"/x/\\$a\\"b"')


@unittest.skipIf(gi is None, "necesita PyGObject (solo Linux)")
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

    def _children(self):
        out = subprocess.run(["ps", "--ppid", str(os.getpid()), "-o", "pid=,stat="],
                             capture_output=True, text=True, check=False).stdout
        return [line.split() for line in out.splitlines() if line.strip()]

    def test_detener_la_musica_no_deja_procesos(self):
        song = os.path.join(_TMP, "larga.wav")
        N._write_wav(song, [math.sin(i / 7) for i in range(22050 * 30)], 22050)
        orig = L.raw_output_for
        sink = [sys.executable, "-c", "import sys\nwhile sys.stdin.buffer.read(4096): pass"]  # pw-cat falso
        L.raw_output_for = lambda _rate, _ch: sink
        try:
            m = L.MusicPlayer()
            N.SOUNDS_READY.set()
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
