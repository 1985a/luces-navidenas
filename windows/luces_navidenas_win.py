"""
Luces Navideñas para Windows
============================
Bombillitos de Navidad que cuelgan del borde de la pantalla, inspirados en el
clásico "Holiday Lights" de Windows 95/98, con interfaz y luces modernas.

Copyright (C) 2026 Wagner. Licencia GPL-3.0-or-later (ver LICENSE).

Uso:
  LucesNavidenas.exe              inicia (o abre las opciones si ya está corriendo)
  LucesNavidenas.exe --opciones   abre la ventana de opciones
  LucesNavidenas.exe --oculto     inicia sin mostrar la ventana de opciones
  LucesNavidenas.exe --alternar   enciende / apaga las luces
  LucesNavidenas.exe --salir      cierra el programa

Cómo está hecho (detalle en docs/ARQUITECTURA.md):
- Las luces y la nieve son ventanas por capas (WS_EX_LAYERED) que dejan pasar los clics;
  el dibujo lo hacen nucleo.StripEngine y nucleo.EffectEngine en un hilo propio que duerme
  hasta el próximo cambio visible.
- La ventana de opciones es Tkinter (tema sv_ttk); el icono de la bandeja, pystray.
- Los avisos entre hilos (bandeja, otra instancia, fin de canción) llegan como mensajes de
  Windows a una ventana invisible del hilo de Tk: nada consulta nada periódicamente.
"""

import array
import copy
import ctypes
import faulthandler
import json
import os
import queue
import random
import secrets
import socket
import sys
import threading
import time
import tkinter as tk
import traceback
import wave
import winreg
import winsound
from ctypes import wintypes as wt
from tkinter import filedialog, ttk

import cairo
import pystray
import sv_ttk
from PIL import Image, ImageTk
from pystray._win32 import Icon as _Win32TrayIcon

if not getattr(sys, "frozen", False):  # ejecutando desde el repositorio: nucleo.py está un nivel arriba
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nucleo as N

APP_NAME = N.APP_NAME
VERSION = N.VERSION
BUILD_DATE = "@BUILD_DATE@"  # windows/build.py lo reemplaza al compilar

APPDATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "LucesNavidenas")
LOCALDATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LucesNavidenas")
CONFIG_FILE = os.path.join(APPDATA, "config.json")
PORT_FILE = os.path.join(APPDATA, "instancia.txt")
CACHE_DIR = os.path.join(LOCALDATA, "cache")
LOG_FILE = os.path.join(LOCALDATA, "registro.txt")
CRASH_FILE = os.path.join(LOCALDATA, "fallos.txt")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "LucesNavidenas"
MUTEX_NAME = "Local\\LucesNavidenas-instancia"
MUSIC_EXT = (".mid", ".midi", ".rmi", ".mp3", ".wma", ".wav", ".m4a", ".aac", ".flac", ".ogg")
ACCENT = "#f4d58d"
KNOWN_ARGS = {"--opciones", "--oculto", "--alternar", "--salir"}


def resource(name):
    """Ruta de un archivo que viaja con el programa (junto al .exe o al script)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def build_date():
    if not BUILD_DATE.startswith("@"):
        return BUILD_DATE
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(os.path.abspath(__file__))))
    except OSError:
        return "desconocida"


def log(msg):
    """Registro de errores: sin consola, es la única pista si algo falla en un equipo ajeno."""
    try:
        os.makedirs(LOCALDATA, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg.rstrip() + "\n")
    except OSError:
        pass


TRACE = bool(os.environ.get("LUCES_TRAZA"))  # traza detallada para diagnosticar (la usa la prueba de humo)


def trace(msg):
    if TRACE:
        log("[traza] " + msg)


def _log_exception(exc_type, exc, tb):
    log("".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = _log_exception
threading.excepthook = lambda a: _log_exception(a.exc_type, a.exc_value, a.exc_traceback)


def enable_crash_log():
    """Si el proceso cae en código nativo (ctypes, Tk, cairo), deja la traza de cada hilo en fallos.txt."""
    try:
        os.makedirs(LOCALDATA, exist_ok=True)
        enable_crash_log.file = open(CRASH_FILE, "a", encoding="utf-8")  # noqa: SIM115 (abierto toda la vida)
        faulthandler.enable(file=enable_crash_log.file, all_threads=True)
    except OSError:
        pass


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return N.sanitize_config(json.load(f))
    except (OSError, ValueError):
        return copy.deepcopy(N.DEFAULTS)


def save_config(cfg):
    try:
        N.write_json_atomic(CONFIG_FILE, cfg)
    except OSError as e:
        log(f"No se pudo guardar la configuración: {e}")


# --------------------------------------------------------------------------
# Win32
# --------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
shell32 = ctypes.WinDLL("shell32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:
    dwmapi = None

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x80000, 0x20, 0x80
WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x8, 0x8000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
ULW_ALPHA = 2
WM_USER, WM_APP = 0x400, 0x8000
WM_APPBAR = WM_USER + 257
WM_COMMANDS = WM_APP + 1         # hay órdenes en la cola de la aplicación
MM_MCINOTIFY = 0x3B9             # MCI: aviso de reproducción
MCI_NOTIFY_SUCCESSFUL = 1        # … la canción terminó sola (no la detuvimos nosotros)
WM_DISPLAYCHANGE = 0x7E
WM_POWERBROADCAST, PBT_APMPOWERSTATUSCHANGE = 0x218, 0xA
WM_NCHITTEST, HTTRANSPARENT = 0x84, -1
ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS = 0, 1, 2, 3
ABN_POSCHANGED, ABN_FULLSCREENAPP = 1, 2
ABE_TOP, ABE_BOTTOM = 1, 3
HWND_TOPMOST, HWND_MESSAGE = -1, -3
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
ERROR_ALREADY_EXISTS = 183
VREFRESH = 116


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wt.DWORD), ("BatteryFullLifeTime", wt.DWORD)]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wt.ATOM
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
                                       wt.HDC, ctypes.POINTER(wt.POINT), wt.COLORREF,
                                       ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), MONITORENUMPROC, wt.LPARAM]
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
shell32.SHAppBarMessage.argtypes = [wt.DWORD, ctypes.POINTER(APPBARDATA)]
shell32.SHAppBarMessage.restype = ctypes.c_size_t
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.GetSystemPowerStatus.argtypes = [ctypes.POINTER(SYSTEM_POWER_STATUS)]
winmm.mciSendStringW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.UINT, wt.HANDLE]
winmm.mciSendStringW.restype = wt.DWORD


def refresh_period():
    """Duración de un cuadro del monitor principal (s)."""
    hz = 60
    try:
        dc = user32.GetDC(None)
        hz = gdi32.GetDeviceCaps(dc, VREFRESH) or 60
        user32.ReleaseDC(None, dc)
    except OSError:
        pass
    if hz < 30 or hz > 360:
        hz = 60
    return 1.0 / hz


def foreign_fullscreen(mon_rect):
    """¿Hay una ventana de OTRO programa ocupando toda esta pantalla (video, juego…)?

    Windows avisa a las barras (AppBar) cuando algo pasa a pantalla completa, pero la
    capa de nieve de este mismo programa también podía disparar ese aviso y esconder las luces.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return False
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    left, top, right, bottom = mon_rect
    return r.left <= left and r.top <= top and r.right >= right and r.bottom >= bottom


def on_battery_or_saver():
    """True con batería o con el «ahorro de batería» de Windows activado."""
    st = SYSTEM_POWER_STATUS()
    if not kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return False
    return st.ACLineStatus == 0 or st.SystemStatusFlag == 1


CLASS_NAME = "LucesNavidenasCapa"
_windows = {}        # hwnd → objeto, para el procedimiento de ventana
_app_ref = [None]


@WNDPROC
def _wndproc(hwnd, msg, wparam, lparam):
    try:
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT  # los clics pasan a la ventana de abajo
        app = _app_ref[0]
        if msg == WM_COMMANDS and app is not None:
            app.drain_commands()
            return 0
        if msg == MM_MCINOTIFY and app is not None:
            if wparam == MCI_NOTIFY_SUCCESSFUL:  # los avisos de «abortada» llegan al cambiar de canción
                app.music.on_notify()
            return 0
        obj = _windows.get(hwnd)
        if msg == WM_APPBAR and obj is not None:
            if wparam == ABN_POSCHANGED:
                obj.appbar_setpos()
            elif wparam == ABN_FULLSCREENAPP:
                mon = getattr(obj, "mon", None)
                obj.set_fullscreen_hidden(bool(lparam) and mon is not None and foreign_fullscreen(mon["rect"]))
            return 0
        if msg == WM_DISPLAYCHANGE and app is not None:
            app.schedule_rebuild()
        elif msg == WM_POWERBROADCAST and wparam == PBT_APMPOWERSTATUSCHANGE and app is not None:
            app.power_changed()
    except Exception:  # una excepción aquí cruzaría a código C: se registra y se sigue
        log(traceback.format_exc())
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _register_class():
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _wndproc
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = CLASS_NAME
    user32.RegisterClassExW(ctypes.byref(wc))


def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # por monitor
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def list_monitors():
    mons = []

    def cb(hmon, _hdc, _rect, _data):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        dpi = 96
        try:
            x, y = wt.UINT(), wt.UINT()
            ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(x), ctypes.byref(y))
            dpi = x.value or 96
        except (AttributeError, OSError):
            pass
        r = mi.rcMonitor
        mons.append({"rect": (r.left, r.top, r.right, r.bottom), "primary": bool(mi.dwFlags & 1),
                     "scale": dpi / 96.0})
        return True
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    if not mons:
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        mons.append({"rect": (0, 0, w, h), "primary": True, "scale": 1.0})
    return mons


class MessageWindow:
    """Ventana invisible (solo mensajes) del hilo de Tk: otros hilos la despiertan con PostMessage."""

    def __init__(self):
        self.hwnd = user32.CreateWindowExW(0, CLASS_NAME, APP_NAME, 0, 0, 0, 0, 0, wt.HWND(HWND_MESSAGE),
                                           None, kernel32.GetModuleHandleW(None), None)

    def post(self, msg):
        user32.PostMessageW(self.hwnd, msg, 0, 0)

    def destroy(self):
        user32.DestroyWindow(self.hwnd)


# --------------------------------------------------------------------------
# Ventanas por capas (luces y nieve)
# --------------------------------------------------------------------------

class LayeredWindow:
    """Ventana transparente por píxel, siempre encima y que deja pasar los clics.

    Se crea y se destruye en el hilo de Tk (que atiende sus mensajes); el hilo de dibujo solo
    pinta en su superficie y la presenta con UpdateLayeredWindow, que admite otro hilo.
    """

    def __init__(self, x, y, w, h, title):
        self.x, self.y, self.w, self.h = x, y, max(1, w), max(1, h)
        self.hidden = False
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        self.hwnd = user32.CreateWindowExW(ex, CLASS_NAME, title, WS_POPUP, x, y, self.w, self.h,
                                           None, None, kernel32.GetModuleHandleW(None), None)
        _windows[self.hwnd] = self
        self._alloc()
        self.blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER con alfa por píxel
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)

    def _alloc(self):
        """Mapa de bits DIB de 32 bits compartido con cairo (mismo formato: BGRA premultiplicado)."""
        screen = user32.GetDC(None)
        self.memdc = gdi32.CreateCompatibleDC(screen)
        user32.ReleaseDC(None, screen)
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = self.w, -self.h  # negativo: filas de arriba abajo, como cairo
        bmi.biPlanes, bmi.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        self.hbmp = gdi32.CreateDIBSection(self.memdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        self.old = gdi32.SelectObject(self.memdc, self.hbmp)
        buf = (ctypes.c_ubyte * (self.w * self.h * 4)).from_address(bits.value)
        self.surface = cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_ARGB32, self.w, self.h, self.w * 4)

    def present(self):
        if self.hidden:
            return
        self.surface.flush()
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(wt.POINT(self.x, self.y)),
                                   ctypes.byref(wt.SIZE(self.w, self.h)), self.memdc,
                                   ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(self.blend), ULW_ALPHA)

    def move(self, x, y):
        self.x, self.y = x, y
        user32.SetWindowPos(self.hwnd, wt.HWND(HWND_TOPMOST), x, y, 0, 0, SWP_NOACTIVATE | SWP_NOSIZE)

    def raise_top(self):
        user32.SetWindowPos(self.hwnd, wt.HWND(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)

    def set_fullscreen_hidden(self, hide):
        self.hidden = hide
        user32.ShowWindow(self.hwnd, SW_HIDE if hide else SW_SHOWNOACTIVATE)

    def appbar_setpos(self):
        """Solo las tiras que reservan espacio son barras (AppBar)."""

    def destroy(self):
        _windows.pop(self.hwnd, None)
        self.surface.finish()
        gdi32.SelectObject(self.memdc, self.old)
        gdi32.DeleteObject(self.hbmp)
        gdi32.DeleteDC(self.memdc)
        user32.DestroyWindow(self.hwnd)


class StripWindow(LayeredWindow):
    """Una tira de luces en el borde de un monitor."""

    def __init__(self, app, mon):
        self.app = app
        self.mon = mon
        self.scale = mon["scale"]
        cfg = self.scaled_cfg()
        self.bottom = app.cfg["position"] == "bottom"
        left, top, right, bottom = mon["rect"]
        h = N.strip_height(cfg)
        self.reserve = N.reserved_height(cfg) if app.cfg["reserve"] else 0
        super().__init__(left, bottom - h if self.bottom else top, right - left, h, APP_NAME)
        self.engine = N.StripEngine()
        self.next_at = 0.0  # instante del próximo tick (lo maneja el hilo de dibujo)
        self.abd = None
        if self.reserve:
            # Barra del sistema: las ventanas maximizadas empiezan debajo de las luces
            self.abd = APPBARDATA()
            self.abd.cbSize = ctypes.sizeof(APPBARDATA)
            self.abd.hWnd = self.hwnd
            self.abd.uCallbackMessage = WM_APPBAR
            shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(self.abd))
            self.appbar_setpos()

    def scaled_cfg(self):
        """La configuración con el tamaño en píxeles físicos de este monitor (DPI)."""
        c = dict(self.app.cfg)
        c["size"] = c["size"] * self.scale
        return c

    def appbar_setpos(self):
        if not self.abd:
            return
        left, top, right, bottom = self.mon["rect"]
        self.abd.uEdge = ABE_BOTTOM if self.bottom else ABE_TOP
        self.abd.rc = wt.RECT(left, top, right, bottom)
        for msg in (ABM_QUERYPOS, ABM_SETPOS):
            if self.bottom:
                self.abd.rc.top = self.abd.rc.bottom - self.reserve
            else:
                self.abd.rc.bottom = self.abd.rc.top + self.reserve
            shell32.SHAppBarMessage(msg, ctypes.byref(self.abd))
        y = self.abd.rc.bottom - self.h if self.bottom else self.abd.rc.top
        self.move(self.abd.rc.left, y)

    def frame(self, t):
        """Un tick del motor; repinta solo lo que cambió. Devuelve el Frame del motor."""
        cfg = self.scaled_cfg()
        f = self.engine.tick(t, cfg, self.w)
        if f.redraw:
            cr = cairo.Context(self.surface)
            if f.redraw == "spans":
                for x0, x1 in f.spans:
                    cr.rectangle(x0, 0, x1 - x0, self.h)
                cr.clip()
            self.engine.draw(cr, self.w, self.h, cfg)
            del cr
            self.present()
        return f

    def destroy(self):
        if self.abd:
            shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(self.abd))
        super().destroy()


class EffectWindow(LayeredWindow):
    """Nieve o destellos sobre todo un monitor."""

    def __init__(self, app, mon):
        self.app = app
        left, top, right, bottom = mon["rect"]
        self.scale = mon["scale"]
        # 1 px menos de alto: una ventana que cubre toda la pantalla hace creer a Windows que hay
        # un programa en pantalla completa (y escondería la barra de tareas)
        super().__init__(left, top, right - left, bottom - top - 1, APP_NAME + " — efecto")
        self.engine = N.EffectEngine()

    def frame(self, t, dt):
        kind, density = self.app.cfg["effect"], self.app.cfg["effect_density"]
        dirty = self.engine.step(t, dt, self.w, self.h, kind, density, self.scale)
        if not dirty:
            return
        cr = cairo.Context(self.surface)
        if dirty != "all":  # limpiar y pintar solo los bloques que cambiaron
            for x, y, w, h in N.EffectEngine.tile_rects(dirty):
                cr.rectangle(x, y, w, h)
            cr.clip()
        self.engine.draw(cr, self.h, kind)
        del cr
        self.present()


class Renderer:
    """Dibuja las tiras y los efectos en un hilo propio.

    Con nieve, va al ritmo de la pantalla (DwmFlush) con el tope de fps de la «Fluidez».
    Sin nieve, duerme hasta el próximo cambio que pida alguna tira (parpadeo, balanceo,
    color) o hasta que la aplicación lo despierte (poke) porque cambió una opción.
    """

    SHORT_WAIT = 0.05  # s: por debajo, se duerme con time.sleep (preciso) en vez de esperar avisos

    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.strips = []
        self.effects = []
        self.force = False
        self.raise_strips = False
        self.last_fx = None
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="dibujo", daemon=True)
        self.thread.start()

    def set_strips(self, strips):
        with self.lock:
            old, self.strips = self.strips, strips
        self.poke(force=True)
        return old

    def set_effects(self, effects):
        with self.lock:
            old, self.effects = self.effects, effects
            self.raise_strips = bool(effects)
            self.last_fx = None
        self.poke()
        return old

    def poke(self, force=False):
        """Despierta al hilo (cambió una opción); force: repintar todo."""
        if force:
            self.force = True
        self.wake.set()

    def _wait_vsync(self, frame_start):
        if dwmapi is not None:
            try:
                dwmapi.DwmFlush()
                return
            except OSError:
                pass
        left = 1.0 / 60 - (time.perf_counter() - frame_start)
        if left > 0.002:
            time.sleep(left - 0.001)

    def _loop(self):
        while self.running:
            frame_start = time.perf_counter()
            t = time.monotonic()
            fps = self.app.effect_fps()
            with self.lock:
                if self.force:
                    self.force = False
                    for s in self.strips:
                        s.engine.force = True
                        s.next_at = 0.0
                effects = self.effects
                if effects and (self.last_fx is None or t - self.last_fx >= 1.0 / fps - 0.003):
                    dt = 0.016 if self.last_fx is None else min(0.1, t - self.last_fx)
                    self.last_fx = t
                    for w in effects:
                        w.frame(t, dt)
                if effects and self.raise_strips:  # las luces por encima de la nieve
                    self.raise_strips = False
                    for s in self.strips:
                        s.raise_top()
                next_wait = 1.0
                for s in self.strips:
                    if t >= s.next_at:
                        if s.engine.frame_due(t, fps):
                            f = s.frame(t)
                            s.next_at = t + (1.0 / fps if f.busy else f.wait)
                        else:  # tope de fps: esperar a que toque el cuadro
                            s.next_at = s.engine.last_t + 1.0 / fps - 0.003
                    next_wait = min(next_wait, s.next_at - t)
            if effects:
                self._wait_vsync(frame_start)
            elif next_wait >= self.SHORT_WAIT:
                if self.wake.wait(next_wait):  # alguien cambió una opción
                    self.wake.clear()
            elif next_wait > 0:
                # Espera corta (desvanecidos a 60 fps): time.sleep es preciso en Windows desde
                # Python 3.11; Event.wait va a saltos de 15,6 ms (el reloj del sistema)
                time.sleep(next_wait)

    def stop(self):
        self.running = False
        self.wake.set()
        self.thread.join(timeout=1.0)


# --------------------------------------------------------------------------
# Sonido: música (MCI) y efectos (winsound)
# --------------------------------------------------------------------------

def mci(cmd, callback=None):
    buf = ctypes.create_unicode_buffer(256)
    err = winmm.mciSendStringW(cmd, buf, 256, callback)
    return err, buf.value


def list_songs(folder):
    return N.list_songs(folder, MUSIC_EXT)


class WinMusic:
    """Lista en bucle con MCI (MP3, WMA, WAV… por DirectShow; MIDI por el secuenciador).

    El fin de cada canción llega como mensaje MM_MCINOTIFY a la ventana de mensajes: no hay
    que preguntar el estado de la reproducción cada poco.
    """

    ALIAS = "lucesmusica"
    GAP_MS = 800  # pausa entre canciones

    def __init__(self, app):
        self.app = app
        self.tracks = []
        self.idx = 0
        self.open = False
        self.enabled = False
        self.volume = 0.7
        self.has_volume = True
        self.pending = None  # after() de la próxima canción

    def set_tracks(self, tracks):
        if tracks != self.tracks:
            self.tracks = list(tracks)
            self.idx = 0
            self.close()
            self._schedule(0)

    def set_enabled(self, on):
        self.enabled = on
        if on:
            self._schedule(0)
        else:
            self.close()

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        if self.open and self.has_volume:
            mci(f"setaudio {self.ALIAS} volume to {int(self.volume * 1000)}")

    def close(self):
        if self.pending:
            self.app.root.after_cancel(self.pending)
            self.pending = None
        if self.open:
            mci("stop " + self.ALIAS)
            mci("close " + self.ALIAS)
            self.open = False

    def _schedule(self, ms):
        if self.pending or self.open or not (self.enabled and self.tracks):
            return
        self.pending = self.app.root.after(ms, self._start)

    def _start(self):
        """Abre y reproduce la próxima canción que se pueda (la lista da la vuelta)."""
        self.pending = None
        if self.open or not (self.enabled and self.tracks):
            return
        if not N.SOUNDS_READY.is_set():  # el villancico incluido aún se está generando
            self._schedule(500)
            return
        n = len(self.tracks)
        for _ in range(n):
            path = self.tracks[self.idx % n]
            self.idx += 1
            real = os.path.join(CACHE_DIR, "jingle-bells.wav") if path == N.BUILTIN_SONG else path
            if not os.path.exists(real):
                continue
            err, _ = mci(f'open "{real}" type mpegvideo alias {self.ALIAS}')
            self.has_volume = True
            if err:
                err, _ = mci(f'open "{real}" type sequencer alias {self.ALIAS}')
                self.has_volume = False
            if err:
                continue
            self.open = True
            self.set_volume(self.volume)
            mci(f"play {self.ALIAS} notify", self.app.msgwin.hwnd)
            return
        log("Música: ninguna canción de la lista se pudo reproducir.")

    def on_notify(self):
        """MCI avisó que terminó (o se detuvo) la canción: pasar a la siguiente."""
        if self.open:
            mci("close " + self.ALIAS)
            self.open = False
        self._schedule(self.GAP_MS)


class WinSfx:
    """Efectos cortos con winsound. El volumen se aplica en una copia del WAV (una por nivel)."""

    def __init__(self):
        self.volume = 0.5
        self.last = 0.0
        self._made = {}

    def _file(self, name):
        key = (name, round(self.volume, 2))
        if key in self._made:
            return self._made[key]
        src = os.path.join(CACHE_DIR, name)
        dst = os.path.join(CACHE_DIR, f"vol_{int(self.volume * 100)}_{name}")
        try:
            if not os.path.exists(dst):
                with wave.open(src, "rb") as w:
                    params, frames = w.getparams(), w.readframes(w.getnframes())
                a = array.array("h")
                a.frombytes(frames)
                gain = self.volume ** 2
                a = array.array("h", [int(x * gain) for x in a])
                tmp = dst + ".tmp"
                with wave.open(tmp, "wb") as w:
                    w.setparams(params)
                    w.writeframes(a.tobytes())
                os.replace(tmp, dst)
        except (OSError, wave.Error, EOFError):
            return None
        self._made[key] = dst
        return dst

    def play(self, name, min_gap=0.0):
        now = time.monotonic()
        if now - self.last < min_gap or self.volume <= 0.01 or not N.SOUNDS_READY.is_set():
            return
        self.last = now
        f = self._file(name)
        if f:
            try:
                winsound.PlaySound(f, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except RuntimeError:
                pass


# --------------------------------------------------------------------------
# Icono de la bandeja
# --------------------------------------------------------------------------

def surface_to_pil(surf):
    w, h = surf.get_width(), surf.get_height()
    return Image.frombuffer("RGBA", (w, h), bytes(surf.get_data()), "raw", "BGRa", surf.get_stride(), 1)


class CachedTrayIcon(_Win32TrayIcon):
    """pystray guarda la imagen en un .ico temporal y la recarga en cada cambio (~8 ms).
    Aquí cada imagen se carga una sola vez y se reutiliza al parpadear (pystray 0.19)."""

    def __init__(self, *a, **kw):
        self._cache = {}
        super().__init__(*a, **kw)

    def _release_icon(self):
        self._icon_handle = None  # el handle queda en la caché

    def _assert_icon_handle(self):
        if self._icon_handle:
            return
        key = id(self.icon)
        h = self._cache.get(key)
        if h is None:
            super()._assert_icon_handle()
            h = self._cache[key] = self._icon_handle
        self._icon_handle = h


class Tray:
    """Bombillo junto al reloj. Clic: opciones. Menú: encender/apagar, opciones, salir."""

    BLINK_MIN_GAP = 0.6  # s entre cambios del icono, aunque las luces parpadeen más rápido

    def __init__(self, app):
        self.app = app
        self.frame = 0
        self.last = 0.0
        cols = [N.hex_to_rgb("#ff3b3b"), N.hex_to_rgb("#2fdc5a"), N.hex_to_rgb("#ffd23a")]
        self.images = [surface_to_pil(N.render_bulb_icon(c, 1.0, 64)) for c in cols]
        self.off = surface_to_pil(N.render_bulb_icon(cols[0], 0.0, 64))
        menu = pystray.Menu(
            pystray.MenuItem("Opciones…", lambda _i, _it: app.post("opciones"), default=True),
            pystray.MenuItem("Luces encendidas", lambda _i, _it: app.post("alternar"),
                             checked=lambda _it: self.app.cfg["power"]),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir", lambda _i, _it: app.post("salir")),
        )
        self.icon = CachedTrayIcon("LucesNavidenas", self.images[0], APP_NAME, menu)
        self.thread = threading.Thread(target=self.icon.run, name="bandeja", daemon=True)
        self.thread.start()

    def sync(self):
        try:
            self.icon.icon = self.images[self.frame % 3] if self.app.cfg["power"] else self.off
            self.icon.title = APP_NAME + (" — encendidas" if self.app.cfg["power"] else " — apagadas")
            self.icon.update_menu()
        except Exception as e:  # la bandeja puede no estar lista todavía
            log(f"Bandeja: {e!r}")

    def blink(self):
        now = time.monotonic()
        if now - self.last < self.BLINK_MIN_GAP:
            return
        self.last = now
        self.frame += 1
        try:
            self.icon.icon = self.images[self.frame % 3]
        except Exception as e:
            log(f"Bandeja: {e!r}")

    def stop(self):
        try:
            self.icon.stop()
        except Exception:  # cerrando: no importa si ya no estaba
            pass


# --------------------------------------------------------------------------
# Ventana de opciones
# --------------------------------------------------------------------------

class Settings:
    def __init__(self, app, root):
        self.app = app
        self.root = root
        self.updating = False
        self.refreshers = []
        self.snapshot = copy.deepcopy(app.cfg)
        self.preview_engine = N.StripEngine(preview=True)
        self.preview_photo = None
        self.preview_job = None
        self.songs_key = None
        self.swatches = {}
        self.built = False

    def build(self):
        root = self.root
        root.title(APP_NAME)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        W, H = min(1100, int(sw * 0.95)), min(940, int(sh * 0.88))
        root.geometry(f"{W}x{H}+{(sw - W) // 2}+{max(0, (sh - H) // 3)}")
        root.minsize(860, 520)
        try:
            root.iconbitmap(default=resource("luces-navidenas.ico"))
        except tk.TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        sv_ttk.set_theme("dark")
        st = ttk.Style()
        st.configure("Title.TLabel", font=("Segoe UI Semibold", 17))
        st.configure("Sub.TLabel", foreground="#9aa9ad")
        st.configure("CardTitle.TLabel", font=("Segoe UI Semibold", 11), foreground=ACCENT)
        st.configure("Hint.TLabel", foreground="#8a9a9e", font=("Segoe UI", 9))
        st.map("CardTitle.TLabel", foreground=[("!disabled", ACCENT)])
        st.map("Hint.TLabel", foreground=[("!disabled", "#8a9a9e")])
        st.map("Sub.TLabel", foreground=[("!disabled", "#9aa9ad")])

        outer = ttk.Frame(root, padding=(16, 12, 16, 8))
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x")
        tbox = ttk.Frame(head)
        tbox.pack(side="left")
        ttk.Label(tbox, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(tbox, text="Bombillitos de Navidad para tu escritorio", style="Sub.TLabel").pack(anchor="w")
        self.power_var = tk.BooleanVar()
        self.power_btn = ttk.Checkbutton(head, variable=self.power_var, style="Toggle.TButton",
                                         command=self.on_power, width=16)
        self.power_btn.pack(side="right", pady=4)
        self.refreshers.append(lambda c: (self.power_var.set(c["power"]), self._power_label()))

        self.preview_lbl = tk.Label(outer, bg="#1c1c1c", bd=0, highlightthickness=0)
        self.preview_lbl.pack(fill="x", pady=(10, 6))

        foot = ttk.Frame(outer, padding=(0, 8, 0, 0))
        foot.pack(side="bottom", fill="x")
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0, bd=0, bg="#1c1c1c")
        vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        grid = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=grid, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)

        def on_conf(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            need = grid.winfo_reqheight() > canvas.winfo_height()
            if need and not vsb.winfo_ismapped():
                vsb.pack(side="right", fill="y")
            elif not need and vsb.winfo_ismapped():
                vsb.pack_forget()
        grid.bind("<Configure>", on_conf)
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(win, width=e.width), on_conf()))
        canvas.pack(side="left", fill="both", expand=True)

        def wheel(e):
            if grid.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(int(-e.delta / 120), "units")
        root.bind_all("<MouseWheel>", wheel)

        # tres columnas en pantallas anchas; en las de 1024-1280 px no caben y se cortaban
        ncols = 3 if W >= 1060 else 2
        for c in range(ncols):
            grid.columnconfigure(c, weight=1, uniform="col")
        cols = [ttk.Frame(grid) for _ in range(ncols)]
        for i, f in enumerate(cols):
            f.grid(row=0, column=i, sticky="nsew", padx=6)
        layout = ([self.card_modes, self.card_speed, self.card_misc], [self.card_bulbs, self.card_colors],
                  [self.card_music, self.card_effect, self.card_place])
        if ncols == 2:
            layout = ([self.card_modes, self.card_speed, self.card_bulbs, self.card_misc],
                      [self.card_colors, self.card_music, self.card_effect, self.card_place])
        for col, cards in zip(cols, layout, strict=True):
            for make in cards:
                make(col)
        for f in cols:
            f.grid_configure(sticky="new")

        ttk.Button(foot, text="Ayuda", command=self.on_help).pack(side="left")
        ttk.Button(foot, text="Salir", command=self.app.quit_app).pack(side="left", padx=6)
        ttk.Label(foot, text=f"v{VERSION} · compilado {build_date()}", style="Hint.TLabel").pack(side="left", padx=8)
        ttk.Button(foot, text="Aceptar", style="Accent.TButton", command=self.on_ok).pack(side="right")
        self.apply_btn = ttk.Button(foot, text="Aplicar", command=self.on_apply)
        self.apply_btn.pack(side="right", padx=6)
        ttk.Button(foot, text="Cancelar", command=self.on_cancel).pack(side="right")
        self.built = True

    # ---------- ayudantes de construcción
    def card(self, parent, title):
        f = ttk.Frame(parent, style="Card.TFrame", padding=(14, 10, 14, 12))
        f.pack(fill="x", pady=6)
        ttk.Label(f, text=title, style="CardTitle.TLabel", foreground=ACCENT).pack(anchor="w", pady=(0, 6))
        return f

    def set(self, key, value):
        if self.updating:
            return
        new = copy.deepcopy(self.app.cfg)
        new[key] = value
        self.app.apply(new)
        self.update_apply()

    def switch(self, parent, key, label):
        var = tk.BooleanVar()
        cb = ttk.Checkbutton(parent, text=label, variable=var, style="Switch.TCheckbutton",
                             command=lambda: self.set(key, var.get()))
        cb.pack(anchor="w", pady=2, fill="x")
        self.refreshers.append(lambda c: var.set(bool(c[key])))
        return cb

    def combo(self, parent, key, label, items):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label).pack(side="left")
        names = [n for _, n in items]
        ids = [k for k, _ in items]
        var = tk.StringVar()
        cb = ttk.Combobox(row, textvariable=var, values=names, state="readonly", width=18)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda _e: self.set(key, ids[names.index(var.get())]))
        self.refreshers.append(lambda c: var.set(names[ids.index(c[key])] if c[key] in ids else names[0]))
        return cb

    def scale(self, parent, key, lo, hi, left=None, right=None):
        var = tk.DoubleVar()
        sc = ttk.Scale(parent, from_=lo, to=hi, variable=var, command=lambda v: self.set(key, round(float(v), 3)))
        sc.pack(fill="x", pady=(2, 0))
        if left or right:
            lab = ttk.Frame(parent)
            lab.pack(fill="x")
            ttk.Label(lab, text=left or "", style="Hint.TLabel").pack(side="left")
            ttk.Label(lab, text=right or "", style="Hint.TLabel").pack(side="right")
        self.refreshers.append(lambda c: var.set(c[key]))
        return sc

    def volume(self, parent, key, label):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label).pack(side="left")
        pct = ttk.Label(row, text="", width=5, anchor="e", style="Hint.TLabel")
        pct.pack(side="right")
        var = tk.DoubleVar()

        def changed(v):
            v = round(float(v), 2)
            pct.configure(text=f"{round(v * 100)}%")
            self.set(key, v)
        ttk.Scale(row, from_=0, to=1, variable=var, command=changed).pack(side="right", fill="x", expand=True, padx=8)
        self.refreshers.append(lambda c: (var.set(c[key]), pct.configure(text=f"{round(c[key] * 100)}%")))

    # ---------- tarjetas
    def card_modes(self, parent):
        f = self.card(parent, "Modo de parpadeo")
        g = ttk.Frame(f)
        g.pack(fill="x")
        g.columnconfigure(0, weight=1)
        g.columnconfigure(1, weight=1)
        var = tk.StringVar()
        for n, (key, name) in enumerate(N.MODES):
            rb = ttk.Radiobutton(g, text=name, value=key, variable=var, style="Toggle.TButton",
                                 command=lambda: self.set("mode", var.get()))
            rb.grid(row=n // 2, column=n % 2, sticky="ew", padx=3, pady=3)
        self.refreshers.append(lambda c: var.set(c["mode"]))

    def card_speed(self, parent):
        f = self.card(parent, "Velocidad")
        self.scale(f, "rate", 0, 1, "Lento", "Rápido")
        self.switch(f, "smooth", "Transición suave")

    def card_misc(self, parent):
        f = self.card(parent, "Varios")
        self.switch(f, "more", "Más luces")
        self.switch(f, "warm", "Tono incandescente")
        self.switch(f, "sound", "Efectos de sonido")
        self.volume(f, "fx_volume", "   Volumen")
        self.switch(f, "tray_leds", "LEDs en la bandeja")
        self.switch(f, "sway", "Balanceo suave")
        self.combo(f, "fluidez", "Fluidez", [("auto", "Automática"), ("max", "Máxima (60 fps)"),
                                             ("eco", "Ahorro (30 fps)")])
        ttk.Label(f, text="Automática: 60 fps enchufado, 30 con batería o ahorro de batería.",
                  style="Hint.TLabel", wraplength=300, justify="left").pack(anchor="w", pady=(2, 0))

    def card_bulbs(self, parent):
        f = self.card(parent, "Bombillos")
        self.combo(f, "bulb", "Tipo", N.BULBS)
        ttk.Label(f, text="Tamaño").pack(anchor="w", pady=(6, 0))
        self.scale(f, "size", 0.6, 1.8, "Pequeño", "Grande")
        self.switch(f, "burnt", "Algunos fundidos")
        self.switch(f, "broken", "Algunos rotos")
        ttk.Button(f, text="Reacomodar al azar",
                   command=lambda: self.set("seed", random.randint(1, 1000000))).pack(fill="x", pady=(6, 0))

    def card_colors(self, parent):
        f = self.card(parent, "Color")
        g = ttk.Frame(f)
        g.pack(fill="x")
        for c in range(3):
            g.columnconfigure(c, weight=1, uniform="chip")
        var = tk.StringVar()
        for n, (key, name, _cols) in enumerate(N.PALETTES):
            rb = ttk.Radiobutton(g, text=name, image=self.swatch(key), compound="top", value=key, variable=var,
                                 style="Toggle.TButton", command=lambda: self.set("palette", var.get()))
            rb.grid(row=n // 3, column=n % 3, sticky="ew", padx=3, pady=3)
        self.refreshers.append(lambda c: var.set(c["palette"]))

    def swatch(self, key):
        w, h = 66, 16
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(s)
        cols = N.PALETTE_MAP[key][1]
        for i in range(5):
            rgb = cols[i % len(cols)] if cols else N.hsv(i / 5, 0.85, 1.0)
            x = w / 2 + (i - 2) * 12
            g = cairo.RadialGradient(x - 1.5, h / 2 - 1.5, 0, x, h / 2, 6)
            g.add_color_stop_rgb(0, *N.mix(rgb, (1, 1, 1), 0.6))
            g.add_color_stop_rgb(1, *rgb)
            cr.set_source(g)
            cr.arc(x, h / 2, 5, 0, 6.3)
            cr.fill()
        s.flush()
        photo = ImageTk.PhotoImage(surface_to_pil(s))
        self.swatches[key] = photo  # Tk no guarda la referencia: sin esto la imagen desaparece
        return photo

    def card_music(self, parent):
        f = self.card(parent, "Música")
        self.switch(f, "music", "Reproducir música")
        self.volume(f, "volume", "Volumen")
        box = ttk.Frame(f, style="Card.TFrame")
        box.pack(fill="x", pady=4)
        canvas = tk.Canvas(box, height=84, bg="#2b2b2b", highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        self.song_frame = ttk.Frame(canvas)
        self.song_frame.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.song_frame, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(4, 0))
        self.folder_lbl = ttk.Label(row, text="", style="Hint.TLabel")
        self.folder_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Carpeta…", command=self.on_folder).pack(side="right")
        self.refreshers.append(self.refresh_songs)

    def refresh_songs(self, c):
        key = (c["music_dir"], tuple(c.get("music_skip", [])))
        if key == self.songs_key:
            return  # p. ej. al mover un deslizador: no releer la carpeta
        self.songs_key = key
        for ch in self.song_frame.winfo_children():
            ch.destroy()
        skip = set(c.get("music_skip", []))
        for path, title in list_songs(c["music_dir"]):
            var = tk.BooleanVar(value=path not in skip)
            ttk.Checkbutton(self.song_frame, text=title, variable=var,
                            command=lambda p=path, v=var: self.on_song(p, v.get())).pack(anchor="w", padx=4)
        d = c["music_dir"]
        self.folder_lbl.configure(text=d if len(d) < 38 else "…" + d[-36:])

    def on_song(self, path, on):
        skip = set(self.app.cfg.get("music_skip", []))
        if on:
            skip.discard(path)
        else:
            skip.add(path)
        self.set("music_skip", sorted(skip))

    def on_folder(self):
        d = filedialog.askdirectory(parent=self.root, initialdir=self.app.cfg["music_dir"],
                                    title="Carpeta de música navideña")
        if d:
            self.set("music_dir", os.path.normpath(d))
            self.updating = True
            self.refresh_songs(self.app.cfg)
            self.updating = False

    def card_effect(self, parent):
        f = self.card(parent, "Efecto de pantalla")
        self.combo(f, "effect", "Tipo", N.EFFECTS)
        ttk.Label(f, text="Intensidad").pack(anchor="w", pady=(6, 0))
        self.scale(f, "effect_density", 0, 1, "Poca", "Mucha")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(4, 0))
        var = tk.BooleanVar()
        ttk.Checkbutton(row, text="Activar", variable=var, style="Switch.TCheckbutton",
                        command=lambda: self.set("effect_on", var.get())).pack(side="left")
        self.refreshers.append(lambda c: var.set(c["effect_on"]))
        ttk.Button(row, text="Probar", command=self.app.test_effect).pack(side="right")

    def card_place(self, parent):
        f = self.card(parent, "Ubicación e inicio")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Colgar en").pack(side="left")
        var = tk.StringVar()
        for key, name in (("bottom", "Abajo"), ("top", "Arriba")):
            ttk.Radiobutton(row, text=name, value=key, variable=var, style="Toggle.TButton",
                            command=lambda: self.set("position", var.get())).pack(side="right", padx=2)
        self.refreshers.append(lambda c: var.set(c["position"]))
        self.combo(f, "monitors", "Pantallas", [("all", "Todas"), ("primary", "Solo la principal")])
        self.switch(f, "reserve", "No tapar las ventanas")
        self.switch(f, "autostart", "Iniciar con Windows")
        self.switch(f, "show_options", "Mostrar opciones al iniciar")
        self.switch(f, "bg_notice", "Avisar al cerrar la ventana")

    # ---------- estado
    def refresh(self):
        self.updating = True
        for fn in self.refreshers:
            fn(self.app.cfg)
        self.updating = False
        self.update_apply()

    def update_apply(self):
        if self.built:
            self.apply_btn.state(["!disabled"] if self.app.cfg != self.app.saved_cfg else ["disabled"])

    def _power_label(self):
        # ● y ○ existen en Segoe UI de todos los Windows (⏻ no: salía un cuadrado en Windows 10/Server)
        self.power_btn.configure(text="●  Encendidas" if self.power_var.get() else "○  Apagadas")

    def on_power(self):
        self._power_label()
        if not self.updating:
            self.app.set_power(self.power_var.get())

    def visible(self):
        try:
            return self.built and self.root.state() != "withdrawn"
        except tk.TclError:
            return False

    def open(self):
        if not self.built:
            self.build()
        self.snapshot = copy.deepcopy(self.app.cfg)
        self.songs_key = None  # la carpeta pudo cambiar mientras la ventana estaba cerrada
        self.refresh()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.wake_preview(force=True)

    # ---------- vista previa (solo mientras la ventana está abierta)
    def wake_preview(self, force=False):
        if force:
            self.preview_engine.force = True
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
            self.preview_job = None
        if self.visible():
            self.preview_job = self.root.after(0, self._preview_frame)

    def _preview_frame(self):
        self.preview_job = None
        if not self.visible():
            return  # oculta: no se anima (se vuelve a encender al abrir)
        w = max(200, self.preview_lbl.winfo_width())
        cfg = self.app.cfg
        h = N.strip_height(cfg) + 26
        size_changed = self.preview_photo is None or (self.preview_photo.width(), self.preview_photo.height()) != (w, h)
        f = self.preview_engine.tick(time.monotonic(), cfg, w)
        if f.redraw or size_changed:
            s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            cr = cairo.Context(s)
            self.preview_engine.draw(cr, w, h, cfg)
            # sobre el fondo de la ventana (Tk no mezcla el alfa de la imagen)
            cr.set_operator(cairo.OPERATOR_DEST_OVER)
            cr.set_source_rgb(0.11, 0.11, 0.11)
            cr.paint()
            del cr
            s.flush()
            self.preview_photo = ImageTk.PhotoImage(surface_to_pil(s))
            self.preview_lbl.configure(image=self.preview_photo, height=h)
        delay = 1.0 / 30 if f.busy else max(1.0 / 30, f.wait)  # la vista previa no necesita más de 30 fps
        self.preview_job = self.root.after(int(delay * 1000), self._preview_frame)

    # ---------- botones
    def on_apply(self):
        self.app.save()
        self.snapshot = copy.deepcopy(self.app.cfg)
        self.update_apply()

    def on_ok(self):
        self.app.save()
        self.close_to_background()

    def on_cancel(self):
        self.app.apply(copy.deepcopy(self.snapshot), save=True)
        self.refresh()
        self.close_to_background()

    def on_close(self):
        self.app.save()
        self.close_to_background()

    def _hide(self):
        self.root.withdraw()
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
            self.preview_job = None

    def close_to_background(self):
        """Oculta la ventana; las luces siguen. Avisa cómo volver (salvo que pidan no hacerlo)."""
        if not self.app.cfg.get("bg_notice", True):
            self._hide()
            return
        top = tk.Toplevel(self.root)
        top.title(APP_NAME)
        top.transient(self.root)
        top.resizable(False, False)
        try:
            top.iconbitmap(resource("luces-navidenas.ico"))
        except tk.TclError:
            pass
        f = ttk.Frame(top, padding=20)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Luces Navideñas sigue funcionando en segundo plano",
                  font=("Segoe UI Semibold", 13)).pack(anchor="w")
        txt = ("Las luces se quedan encendidas aunque cierres esta ventana.\n\n"
               "Para volver a abrir las opciones:\n"
               "  •  haz clic en el bombillo junto al reloj (área de notificación), o\n"
               "  •  abre «Luces Navideñas» desde el menú Inicio.\n\n"
               "Para cerrarlo del todo: botón «Salir» o clic derecho en el bombillo → Salir.")
        ttk.Label(f, text=txt, justify="left").pack(anchor="w", pady=(10, 8))
        dont = tk.BooleanVar()
        ttk.Checkbutton(f, text="No volver a mostrar este aviso", variable=dont).pack(anchor="w")
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(14, 0))
        result = {"quit": False}

        def finish(quit_):
            result["quit"] = quit_
            top.grab_release()
            top.destroy()
        ttk.Button(btns, text="Entendido", style="Accent.TButton", command=lambda: finish(False)).pack(side="right")
        ttk.Button(btns, text="Salir del programa", command=lambda: finish(True)).pack(side="right", padx=8)
        top.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        top.update_idletasks()
        x = (top.winfo_screenwidth() - top.winfo_reqwidth()) // 2
        y = (top.winfo_screenheight() - top.winfo_reqheight()) // 3
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.grab_set()
        self.root.wait_window(top)
        if dont.get():
            new = copy.deepcopy(self.app.cfg)
            new["bg_notice"] = False
            self.app.apply(new, save=True)
        if result["quit"]:
            self.app.quit_app()
        else:
            self._hide()

    def on_help(self):
        top = tk.Toplevel(self.root)
        top.title("Acerca de " + APP_NAME)
        top.transient(self.root)
        top.resizable(False, False)
        f = ttk.Frame(top, padding=22)
        f.pack()
        ttk.Label(f, text=APP_NAME, font=("Segoe UI Semibold", 16)).pack()
        ttk.Label(f, text=f"Versión {VERSION} · compilado el {build_date()}", style="Hint.TLabel").pack()
        ttk.Label(f, justify="left", text=(
            "\nBombillitos de Navidad que cuelgan del borde de tu pantalla.\n\n"
            "•  Las luces no estorban: los clics pasan a través de ellas.\n"
            "•  Clic en el bombillo de la bandeja: abre estas opciones.\n"
            "•  Pon tus villancicos (MP3, WMA, MIDI, WAV…) en una carpeta\n"
            "    y elígela en «Música».\n\n"
            "Inspirado en el clásico «Holiday Lights» (1994-95)\n"
            "de Don Metzler / Barefoot Productions.\n\n"
            "Software libre (GPL-3.0-or-later):\n"
            "github.com/1985a/luces-navidenas")).pack(anchor="w")
        ttk.Button(f, text="Cerrar", style="Accent.TButton", command=top.destroy).pack(pady=(14, 0))


# --------------------------------------------------------------------------
# Una sola instancia: mutex + órdenes por un socket local con contraseña
# --------------------------------------------------------------------------

_instance_mutex = None  # se mantiene abierto mientras viva el proceso (Windows lo libera al salir)


def already_running():
    """True si ya hay otra copia (mutex con nombre: sin carreras entre dos arranques a la vez)."""
    global _instance_mutex
    _instance_mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def send_to_running(args, attempts=20):
    """Envía los argumentos a la copia que ya corre. Reintenta mientras esa copia termina de arrancar."""
    for _ in range(attempts):
        try:
            with open(PORT_FILE, encoding="utf-8") as f:
                port, token = f.read().split()
            with socket.create_connection(("127.0.0.1", int(port)), timeout=1.0) as s:
                s.sendall(json.dumps({"token": token, "args": args}).encode() + b"\n")
                if s.recv(16) == b"ok":
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    return False


class CommandServer:
    """Recibe los argumentos de otra copia del programa (p. ej. «--alternar» desde un acceso directo).

    Solo escucha en 127.0.0.1 y exige la contraseña que guarda en el archivo del puerto (en la
    carpeta del usuario), para que otro programa del equipo no pueda dar órdenes.
    """

    def __init__(self, app):
        self.app = app
        self.token = secrets.token_hex(16)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        os.makedirs(APPDATA, exist_ok=True)
        tmp = PORT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{self.sock.getsockname()[1]} {self.token}")
        os.replace(tmp, PORT_FILE)
        threading.Thread(target=self._serve, name="ordenes", daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return  # se cerró el socket al salir
            try:
                with conn:
                    conn.settimeout(2.0)
                    msg = json.loads(conn.recv(4096).decode().strip())
                    args = [a for a in msg.get("args", []) if a in KNOWN_ARGS]
                    if secrets.compare_digest(str(msg.get("token", "")), self.token):
                        self.app.post("args", args)
                        conn.sendall(b"ok")
            except (OSError, ValueError, AttributeError):
                continue

    def close(self):
        try:
            self.sock.close()
            os.remove(PORT_FILE)
        except OSError:
            pass


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
        return True
    except OSError:
        return False


def set_autostart(on):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                if getattr(sys, "frozen", False):
                    exe = f'"{sys.executable}"'
                else:
                    exe = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, exe + " --oculto")
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        log(f"Autoarranque: {e}")


# --------------------------------------------------------------------------
# Aplicación
# --------------------------------------------------------------------------

class App:
    def __init__(self):
        _app_ref[0] = self
        self.cfg = load_config()
        self.saved_cfg = copy.deepcopy(self.cfg)
        self.queue = queue.Queue()
        self.strips = []
        self.effects = []
        self.test_until = 0.0
        self.rebuild_job = None
        self.blink_job = None
        self.last_step = None
        self.quitting = False
        self.saver = on_battery_or_saver()
        os.makedirs(CACHE_DIR, exist_ok=True)
        threading.Thread(target=N.generate_sounds, args=(CACHE_DIR,), name="sonidos", daemon=True).start()
        _register_class()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = _log_exception
        self.msgwin = MessageWindow()
        self.music = WinMusic(self)
        self.music.volume = self.cfg["volume"]
        self.sfx = WinSfx()
        self.sfx.volume = self.cfg["fx_volume"]
        self.settings = Settings(self, self.root)
        self.server = CommandServer(self)
        self.renderer = Renderer(self)
        self.tray = Tray(self)
        self.build_strips()
        self.update_effect()
        self.update_music()
        self.schedule_blink()
        # el registro manda: si el usuario quitó el autoarranque desde Windows, se respeta
        self.cfg["autostart"] = self.saved_cfg["autostart"] = autostart_enabled()
        if self.cfg["autostart"]:
            set_autostart(True)  # por si el programa se reinstaló en otra carpeta

    # ---------- órdenes desde otros hilos
    def post(self, *msg):
        """Seguro desde cualquier hilo: encola y despierta al hilo de Tk con un mensaje de Windows."""
        self.queue.put(msg)
        self.msgwin.post(WM_COMMANDS)

    def drain_commands(self):
        while not self.quitting:
            try:
                msg = self.queue.get_nowait()
            except queue.Empty:
                return
            trace(f"orden {msg}")
            if msg[0] == "opciones":
                self.settings.open()
            elif msg[0] == "alternar":
                self.set_power(not self.cfg["power"])
            elif msg[0] == "salir":
                self.quit_app()
            elif msg[0] == "args":
                self.handle_args(msg[1])

    # ---------- monitores y ventanas
    def monitors(self):
        mons = list_monitors()
        if self.cfg["monitors"] == "primary":
            return [m for m in mons if m["primary"]][:1] or mons[:1]
        return mons

    def build_strips(self):
        for w in self.renderer.set_strips([]):
            w.destroy()
        self.strips = [StripWindow(self, m) for m in self.monitors()]
        self.renderer.set_strips(list(self.strips))

    def schedule_rebuild(self):
        if self.rebuild_job:
            self.root.after_cancel(self.rebuild_job)

        def go():
            self.rebuild_job = None
            self.build_strips()
            self.update_effect(rebuild=True)
        self.rebuild_job = self.root.after(300, go)

    def update_effect(self, rebuild=False):
        want = (self.cfg["power"] and self.cfg["effect_on"]) or time.monotonic() < self.test_until
        if rebuild or not want:
            for w in self.renderer.set_effects([]):
                w.destroy()
            self.effects = []
        if want and not self.effects:
            self.effects = [EffectWindow(self, m) for m in self.monitors()]
            self.renderer.set_effects(list(self.effects))

    def test_effect(self):
        self.test_until = time.monotonic() + 10
        self.update_effect()
        self.root.after(10200, self.update_effect)

    def update_music(self):
        skip = set(self.cfg.get("music_skip", []))
        self.music.set_tracks([p for p, _ in list_songs(self.cfg["music_dir"]) if p not in skip])
        self.music.set_enabled(self.cfg["music"] and self.cfg["power"])

    def effect_fps(self):
        f = self.cfg.get("fluidez", "auto")
        if f == "eco" or (f == "auto" and self.saver):
            return 30
        return 60

    def power_changed(self):
        """Windows avisó un cambio de energía (enchufe, batería, ahorro)."""
        self.saver = on_battery_or_saver()

    # ---------- configuración
    def apply(self, new, save=False):
        old, self.cfg = self.cfg, new
        if any(old[k] != new[k] for k in N.LAYOUT_KEYS):
            self.schedule_rebuild()
        if any(old[k] != new[k] for k in ("effect_on", "power")):
            self.update_effect()
        if old["volume"] != new["volume"]:
            self.music.set_volume(new["volume"])
        self.sfx.volume = new["fx_volume"]
        if any(old[k] != new[k] for k in ("music", "music_dir", "music_skip", "power")):
            self.update_music()
        if old["autostart"] != new["autostart"]:
            set_autostart(new["autostart"])
        if old["power"] != new["power"] and new["sound"]:
            self.sfx.play("encender.wav" if new["power"] else "apagar.wav")
        if any(old[k] != new[k] for k in ("mode", "rate", "power", "sound", "tray_leds")):
            self.schedule_blink()
        if old["power"] != new["power"]:
            trace("tray.sync")
            self.tray.sync()
            trace("tray.sync hecho")
        self.renderer.poke(force=True)
        self.settings.wake_preview(force=True)
        trace("apply hecho")
        if save:
            self.save()

    def save(self):
        save_config(self.cfg)
        self.saved_cfg = copy.deepcopy(self.cfg)
        self.settings.update_apply()

    def set_power(self, on):
        """El interruptor se aplica y se guarda al momento (sin tocar otros cambios pendientes)."""
        trace(f"set_power {on}")
        if on == self.cfg["power"]:
            return
        new = copy.deepcopy(self.cfg)
        new["power"] = on
        self.apply(new)
        self.saved_cfg["power"] = on
        save_config(self.saved_cfg)
        self.settings.snapshot["power"] = on
        if self.settings.built:
            self.settings.updating = True
            self.settings.power_var.set(on)
            self.settings._power_label()
            self.settings.updating = False
            self.settings.update_apply()

    def handle_args(self, args, first=False):
        if "--salir" in args:
            self.quit_app()
        elif "--alternar" in args:
            self.set_power(not self.cfg["power"])
        elif "--opciones" in args:
            self.settings.open()
        elif first:
            if self.cfg["show_options"] and "--oculto" not in args:
                self.settings.open()
        elif "--oculto" not in args:
            self.settings.open()

    # ---------- latido: sonido de los parpadeos y LEDs de la bandeja
    def schedule_blink(self):
        """Despierta solo en cada parpadeo, y solo si hay sonido o LEDs que mover."""
        if self.blink_job:
            self.root.after_cancel(self.blink_job)
            self.blink_job = None
        c = self.cfg
        if not c["power"] or c["mode"] in N.CONTINUOUS_MODES or c["mode"] == "none":
            return
        if not (c["sound"] or c["tray_leds"]):
            return
        iv = N.rate_to_interval(c["rate"])
        delay = max(0.05, iv - (time.monotonic() % iv) + 0.002)
        self.blink_job = self.root.after(int(delay * 1000), self._blink)

    def _blink(self):
        self.blink_job = None
        c = self.cfg
        s = int(time.monotonic() / N.rate_to_interval(c["rate"]))
        if s != self.last_step:
            self.last_step = s
            if c["sound"] and c["mode"] in ("all", "alternate", "chase"):
                self.sfx.play("tic.wav", min_gap=0.25)
            if c["tray_leds"]:
                self.tray.blink()
        self.schedule_blink()

    # ---------- ciclo de vida
    def run(self, args):
        self.handle_args(args, first=True)
        self.root.mainloop()

    def quit_app(self):
        trace("quit_app desde:\n" + "".join(traceback.format_stack(limit=8)))
        if self.quitting:
            return
        self.quitting = True
        self.music.close()
        self.renderer.stop()
        for w in self.renderer.set_effects([]) + self.renderer.set_strips([]):
            w.destroy()
        self.strips, self.effects = [], []
        self.tray.stop()
        self.server.close()
        self.msgwin.destroy()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main():
    args = sys.argv[1:]
    if "--version" in args:
        ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} {VERSION}\nCompilado: {build_date()}", APP_NAME, 64)
        return 0
    if already_running():
        if not send_to_running(args):
            log("Ya hay una copia abierta pero no responde.")
        return 0
    if "--salir" in args:
        return 0  # no había nada que cerrar
    enable_crash_log()
    set_dpi_awareness()
    app = App()
    trace(f"arranque {args}")
    app.run(args)
    trace("el bucle de Tk terminó")
    app.tray.thread.join(timeout=1.5)
    return 0


if __name__ == "__main__":
    code = main()
    if sys.stdout:
        sys.stdout.flush()
    os._exit(code or 0)  # sin esperar a hilos de bibliotecas (pystray) que a veces no terminan
