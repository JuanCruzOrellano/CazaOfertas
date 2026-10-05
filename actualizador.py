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
EXE = "CazaOfertas-app.zip"   # archivos de la app (código), sin .exe

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


CARPETA = os.path.dirname(os.path.abspath(__file__))


def es_exe():
    """¿Se puede actualizar sola? Solo si fue instalada con el instalador (no en desarrollo)."""
    return os.path.exists(os.path.join(CARPETA, ".instalado"))


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
    """Baja los archivos nuevos de la app (un .zip con el código), los copia encima de los
    actuales y la vuelve a abrir. No hay ningún .exe que el antivirus tenga que analizar."""
    if not es_exe() or not estado["url"]:
        return False, "Descargá la versión nueva desde " + PAGINA
    if estado["descargando"]:
        return True, "Ya se está descargando"
    estado.update(descargando=True, progreso=0, error=None)

    def tarea():
        import io
        import zipfile
        try:
            datos = _bajar(estado["url"], timeout=120)
            estado["progreso"] = 60
            z = zipfile.ZipFile(io.BytesIO(datos))
            if "app.py" not in z.namelist():
                raise RuntimeError("El archivo descargado no es de CazaOfertas")
            z.extractall(CARPETA)
            estado["progreso"] = 80
            sin_ventana = 0x08000000 if os.name == "nt" else 0
            req = os.path.join(CARPETA, "requirements.txt")
            if os.path.exists(req):
                py = sys.executable.replace("pythonw.exe", "python.exe")
                subprocess.run([py, "-m", "pip", "install", "--user", "--upgrade", "-q",
                                "--disable-pip-version-check", "-r", req],
                               creationflags=sin_ventana, capture_output=True, timeout=300)
            estado["progreso"] = 100
            # La app nueva cierra a esta (tiene otro número de versión) y toma su lugar;
            # la ventana abierta se recarga sola.
            subprocess.Popen([sys.executable, os.path.join(CARPETA, "app.py"), "--sin-ventana"],
                             cwd=CARPETA, close_fds=True, creationflags=sin_ventana)
            salir()
        except Exception as e:  # noqa: BLE001
            estado.update(descargando=False, error=f"No se pudo actualizar: {e}")

    threading.Thread(target=tarea, daemon=True).start()
    return True, "Descargando…"
