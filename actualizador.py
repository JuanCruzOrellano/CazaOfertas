"""Actualización automática desde GitHub Releases.

Al abrir la app se consulta la última versión publicada. Si es más nueva, la interfaz
muestra un aviso; al aceptar se descarga el .exe nuevo, se reemplaza el actual y se
vuelve a abrir.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request

from version import APP_VERSION

REPO = "JuanCruzOrellano/CazaOfertas"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGINA = f"https://github.com/{REPO}/releases/latest"
EXE = "CazaOfertas.exe"

estado = {"actual": APP_VERSION, "nueva": None, "notas": "", "url": None,
          "descargando": False, "progreso": 0, "error": None, "error_busqueda": None, "buscado": None}


def _num(v):
    partes = []
    for p in str(v).lstrip("vV").split("-")[0].split("."):
        try:
            partes.append(int(p))
        except ValueError:
            partes.append(0)
    return tuple(partes + [0] * (3 - len(partes)))


def es_exe():
    return bool(getattr(sys, "frozen", False))


_ultima = [0.0]


def _bajar(url, timeout=20):
    """GET que prueba con curl_cffi (trae sus propios certificados) y si no con urllib."""
    errores = []
    try:
        from curl_cffi import requests as creq
        r = creq.get(url, headers={"User-Agent": "CazaOfertas", "Accept": "application/vnd.github+json"},
                     impersonate="chrome", timeout=timeout)
        if r.status_code < 400:
            return r.content
        errores.append(f"GitHub respondió {r.status_code}")
    except Exception as e:  # noqa: BLE001
        errores.append(str(e))
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "CazaOfertas"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        errores.append(str(e))
    raise RuntimeError("; ".join(errores))


def buscar():
    """Consulta GitHub. Deja el resultado (o el error) en `estado`."""
    import time
    _ultima[0] = time.time()
    try:
        data = json.loads(_bajar(API))
    except Exception as e:  # noqa: BLE001
        estado["error_busqueda"] = f"No se pudo consultar GitHub ({e})"
        return
    estado["error_busqueda"] = None
    estado["buscado"] = time.time()
    tag = data.get("tag_name") or ""
    if _num(tag) <= _num(APP_VERSION):
        estado.update(nueva=None)
        return
    url = next((a["browser_download_url"] for a in data.get("assets") or []
                if a.get("name", "").lower() == EXE.lower()), None)
    estado.update(nueva=tag.lstrip("vV"), notas=(data.get("body") or "").strip()[:800], url=url)


def buscar_en_fondo(si_pasaron=0):
    import time
    if time.time() - _ultima[0] >= si_pasaron:
        threading.Thread(target=buscar, daemon=True).start()


def actualizar(salir):
    """Descarga el .exe nuevo y deja un script que lo reemplaza cuando esta app se cierra."""
    if not es_exe() or not estado["url"]:
        return False, "Descargá la versión nueva desde " + PAGINA
    if estado["descargando"]:
        return True, "Ya se está descargando"
    estado.update(descargando=True, progreso=0, error=None)

    def tarea():
        try:
            actual = sys.executable
            carpeta = tempfile.mkdtemp(prefix="cazaofertas-update-")
            nuevo = os.path.join(carpeta, EXE)
            try:
                req = urllib.request.Request(estado["url"], headers={"User-Agent": "CazaOfertas"})
                with urllib.request.urlopen(req, timeout=60) as r, open(nuevo, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    bajado = 0
                    while True:
                        trozo = r.read(256 * 1024)
                        if not trozo:
                            break
                        f.write(trozo)
                        bajado += len(trozo)
                        if total:
                            estado["progreso"] = int(bajado * 100 / total)
            except Exception:  # noqa: BLE001 — plan B: curl_cffi
                estado["progreso"] = 50
                with open(nuevo, "wb") as f:
                    f.write(_bajar(estado["url"], timeout=180))
            if os.path.getsize(nuevo) < 1_000_000:
                raise RuntimeError("La descarga quedó incompleta")
            bat = os.path.join(carpeta, "actualizar.bat")
            # Reintenta reemplazar el .exe hasta que esta app termine de cerrarse (mientras
            # está abierta Windows no deja pisarlo). Sin tasklist/find ni ventanas.
            with open(bat, "w", encoding="utf-8") as f:
                f.write(f"""@echo off
chcp 65001 >nul
set N=0
:copiar
ping -n 2 127.0.0.1 >nul
move /y "{nuevo}" "{actual}" >nul 2>nul
if not errorlevel 1 goto listo
set /a N=N+1
if %N% lss 120 goto copiar
exit /b 1
:listo
start "" "{actual}" --sin-ventana
(goto) 2>nul & rmdir /s /q "{carpeta}"
""")
            subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000,  # CREATE_NO_WINDOW
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
            estado["progreso"] = 100
            salir()
        except Exception as e:  # noqa: BLE001
            estado.update(descargando=False, error=f"No se pudo actualizar: {e}")

    threading.Thread(target=tarea, daemon=True).start()
    return True, "Descargando…"
