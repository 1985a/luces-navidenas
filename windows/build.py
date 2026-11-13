"""Compila Luces Navideñas para Windows: ejecutable (PyInstaller) e instalador (NSIS).

Uso, en Windows y desde la raíz del repositorio:
    python -m pip install -r windows/requirements.txt
    python windows/build.py

Deja en dist/ el instalador LucesNavidenas-<versión>-Instalador.exe y su suma SHA-256.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIN = ROOT / "windows"
BUILD = ROOT / "build" / "win"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))
import nucleo

VERSION = nucleo.VERSION
STAMP = time.strftime("%Y-%m-%d %H:%M")


def run(cmd):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def version_file(path):
    """Datos que Windows muestra en Propiedades → Detalles y en el Administrador de tareas."""
    nums = [int(x) for x in VERSION.split(".")] + [0] * 4
    tup = tuple(nums[:4])
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tup}, prodvers={tup}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('0C0A04B0', [
      StringStruct('CompanyName', 'Luces Navideñas'),
      StringStruct('FileDescription', 'Luces Navideñas'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'LucesNavidenas'),
      StringStruct('LegalCopyright', 'GPL-3.0-or-later'),
      StringStruct('OriginalFilename', 'LucesNavidenas.exe'),
      StringStruct('ProductName', 'Luces Navideñas'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [0x0C0A, 1200])])
  ]
)
""", encoding="utf-8")


def find_makensis():
    found = shutil.which("makensis")
    if found:
        return found
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if base and Path(base, "NSIS", "makensis.exe").exists():
            return str(Path(base, "NSIS", "makensis.exe"))
    sys.exit("No se encontró makensis (instala NSIS 3).")


def main():
    shutil.rmtree(BUILD, ignore_errors=True)
    src = BUILD / "src"
    src.mkdir(parents=True)
    # Fuente con la fecha de compilación sellada
    code = (WIN / "luces_navidenas_win.py").read_text(encoding="utf-8")
    code = code.replace('BUILD_DATE = "@BUILD_DATE@"', f'BUILD_DATE = "{STAMP}"', 1)
    (src / "luces_navidenas_win.py").write_text(code, encoding="utf-8")
    shutil.copy(ROOT / "nucleo.py", src / "nucleo.py")
    version_file(BUILD / "version.txt")

    ico = WIN / "luces-navidenas.ico"
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
         "--name", "LucesNavidenas", "--icon", ico, "--add-data", f"{ico}{os.pathsep}.",
         "--collect-data", "sv_ttk", "--hidden-import", "pystray._win32",
         "--version-file", BUILD / "version.txt", "--paths", src,
         "--distpath", BUILD / "dist", "--workpath", BUILD / "work", "--specpath", BUILD,
         src / "luces_navidenas_win.py"])
    app_dir = BUILD / "dist" / "LucesNavidenas"
    shutil.copy(ROOT / "LICENSE", app_dir / "LICENSE.txt")
    shutil.copy(ROOT / "THIRD_PARTY_NOTICES.md", app_dir / "THIRD_PARTY_NOTICES.txt")

    DIST.mkdir(exist_ok=True)
    out = DIST / f"LucesNavidenas-{VERSION}-Instalador.exe"
    run([find_makensis(), "/V3", "/INPUTCHARSET", "UTF8", f"/DVERSION={VERSION}", f"/DSRCDIR={app_dir}",
         f"/DOUTFILE={out}", f"/DICON={ico}", f"/DLICENSE={ROOT / 'LICENSE'}", WIN / "instalador.nsi"])
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (DIST / (out.name + ".sha256")).write_text(f"{digest}  {out.name}\n", encoding="ascii")
    print(f"Listo: {out} ({out.stat().st_size / 1e6:.1f} MB) sha256 {digest}")


if __name__ == "__main__":
    main()
