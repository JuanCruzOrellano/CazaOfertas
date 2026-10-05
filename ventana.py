"""Ventana propia de CazaOfertas.

Usa el motor web que ya trae Windows (WebView2) dentro de una ventana común, con el
título y el ícono de la app: sin barra de direcciones, sin pestañas y sin el logo de
Edge. Si por algún motivo no está disponible, app.py vuelve a la ventana de Edge.
"""
import os
import sys
import threading
import time

_ventana = [None]
ES_WINDOWS = os.name == "nt"


def disponible():
    try:
        import webview  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _log(msg):
    try:
        import core
        with open(os.path.join(core.DATA_DIR, "ventana.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _ventanas_propias():
    """Ventanas visibles de este proceso (para ponerles el ícono)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid = os.getpid()
    encontradas = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cada(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            encontradas.append(hwnd)
        return True

    user32.EnumWindows(cada, 0)
    return encontradas


def _poner_icono(ico):
    """Reemplaza el ícono de Python por el de la app (barra de título y de tareas)."""
    if not ES_WINDOWS or not ico or not os.path.exists(ico):
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        cargar = lambda tam: user32.LoadImageW(None, ico, 1, tam, tam, 0x10)  # noqa: E731
        grande, chico = cargar(256), cargar(16)
        for _ in range(20):  # la ventana tarda un instante en existir
            hwnds = _ventanas_propias()
            if hwnds:
                for h in hwnds:
                    user32.SendMessageW(h, 0x80, 1, ctypes.c_void_p(grande))  # WM_SETICON, ICON_BIG
                    user32.SendMessageW(h, 0x80, 0, ctypes.c_void_p(chico))   # ICON_SMALL
                return
            time.sleep(0.25)
    except Exception as e:  # noqa: BLE001
        _log(f"icono: {e}")


def abrir(url, ico=None, datos=None):
    """Abre la ventana y espera a que el usuario la cierre.
    Devuelve False si no se pudo abrir (y entonces se usa Edge)."""
    try:
        import webview
    except Exception as e:  # noqa: BLE001
        _log(f"sin pywebview: {e}")
        return False
    if ES_WINDOWS:
        try:  # que Windows la muestre como "CazaOfertas" y no como "Python"
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CazaOfertas.App")
        except Exception:  # noqa: BLE001
            pass
    try:
        try:  # los links de las tiendas se abren en el navegador de siempre
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        except Exception:  # noqa: BLE001
            pass
        w = webview.create_window("CazaOfertas", url, width=1280, height=850,
                                  min_size=(900, 600), background_color="#F7F5F2",
                                  text_select=True)
        _ventana[0] = w
        opciones = {"private_mode": False}
        if datos:
            opciones["storage_path"] = os.path.join(datos, "ventana")
        if ES_WINDOWS:
            opciones["gui"] = "edgechromium"
        threading.Thread(target=_poner_icono, args=(ico,), daemon=True).start()
        webview.start(**opciones)
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo abrir la ventana: {e!r}")
        _ventana[0] = None
        return False


def mostrar():
    """Trae la ventana al frente (cuando se abre la app otra vez estando abierta)."""
    w = _ventana[0]
    if not w:
        return False
    try:
        w.restore()
        w.show()
        w.on_top = True
        w.on_top = False
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    print("pywebview:", disponible(), sys.version)
