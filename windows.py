"""Integración con Windows: notificaciones, tarea programada y ventana de la app."""
import os
import shutil
import subprocess
import sys
import webbrowser

ES_WINDOWS = os.name == "nt"
TAREA = "CazaOfertas - revisar precios"
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
SIN_VENTANA = 0x08000000 if ES_WINDOWS else 0  # CREATE_NO_WINDOW


def _xml(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def notificar(titulo, texto, link=None):
    """Muestra una notificación de Windows (toast). En otros sistemas solo la imprime."""
    if not ES_WINDOWS:
        print(f"[aviso] {titulo}: {texto}")
        return
    launch = f' activationType="protocol" launch="{_xml(link)}"' if link else ""
    img = (f'<image placement="appLogoOverride" hint-crop="circle" src="{_xml("file:///" + LOGO[0].replace(os.sep, "/"))}"/>'
           if LOGO[0] and os.path.exists(LOGO[0]) else "")
    xml = (f'<toast{launch}><visual><binding template="ToastGeneric">'
           f"<text>{_xml(titulo)}</text><text>{_xml(texto)}</text>{img}"
           f"</binding></visual></toast>")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('{xml.replace(chr(39), chr(39) * 2)}');"
        "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($t);"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       creationflags=SIN_VENTANA, timeout=30, capture_output=True)
    except Exception as e:  # noqa: BLE001
        print("No se pudo mostrar la notificación:", e)


def precio(n):
    return "$" + f"{n:,.0f}".replace(",", ".")


ETIQUETAS = {
    "nuevo": "Nuevo en tu rango",
    "entro_rango": "Bajó y entró en tu rango",
    "bajo_mas": "Bajó todavía más",
    "volvio_stock": "Volvió a tener stock",
}


def avisar_novedades(novedades, url_app=None):
    if not novedades:
        return
    if len(novedades) == 1:
        n = novedades[0]
        texto = f"{n['name']} — {precio(n['price'])}"
        if n.get("old_price") and n["old_price"] > n["price"]:
            texto += f" (antes {precio(n['old_price'])})"
        notificar(f"{n['brand']}: {ETIQUETAS.get(n['type'], 'Novedad')}", texto, n.get("link"))
        return
    marcas = sorted({n["brand"] for n in novedades})
    resumen = ", ".join(marcas[:4]) + ("…" if len(marcas) > 4 else "")
    mas_barato = min(novedades, key=lambda n: n["price"])
    notificar(f"{len(novedades)} novedades de precio",
              f"{resumen}. Ej: {mas_barato['name']} a {precio(mas_barato['price'])}",
              url_app)


# ------------------------------------------------------------ tarea programada
LOGO = [None]


def copiar_logo(origen, carpeta):
    """Deja el logo en una carpeta fija (el .exe se descomprime en una carpeta temporal)."""
    try:
        destino = os.path.join(carpeta, "logo.png")
        if os.path.exists(origen) and (not os.path.exists(destino)
                                       or os.path.getsize(destino) != os.path.getsize(origen)):
            shutil.copyfile(origen, destino)
        LOGO[0] = destino if os.path.exists(destino) else None
    except OSError:
        LOGO[0] = None


def _pythonw():
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


def programar(horas, minutos=None):
    """Crea (o borra si horas=0) la tarea de Windows que revisa precios en segundo plano.
    Con `minutos` (modo evento) revisa cada esa cantidad de minutos."""
    if not ES_WINDOWS:
        return False, "Solo disponible en Windows."
    if not horas and not minutos:
        subprocess.run(["schtasks", "/Delete", "/TN", TAREA, "/F"],
                       capture_output=True, creationflags=SIN_VENTANA)
        return True, "Revisión automática desactivada."
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}" --revisar'      # versión .exe
    else:
        app = os.path.abspath(os.path.join(os.path.dirname(__file__), "app.py"))
        cmd = f'"{_pythonw()}" "{app}" --revisar'
    frecuencia = ["/SC", "MINUTE", "/MO", str(int(minutos))] if minutos else ["/SC", "HOURLY", "/MO", str(int(horas))]
    r = subprocess.run(["schtasks", "/Create", "/F", "/TN", TAREA, *frecuencia, "/TR", cmd],
                       capture_output=True, text=True, creationflags=SIN_VENTANA)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "No se pudo crear la tarea").strip()
    if minutos:
        return True, f"Modo evento: revisa cada {minutos} minutos."
    return True, f"Revisará los precios cada {horas} h, aunque la app esté cerrada."


# ------------------------------------------------------------- abrir ventana
def abrir_ventana(url):
    """Abre la interfaz como ventana de aplicación (Edge o Chrome en modo app)."""
    if ES_WINDOWS:
        rutas = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for r in rutas:
            if os.path.exists(r):
                subprocess.Popen([r, f"--app={url}", "--window-size=1280,850"])
                return
    else:
        for b in ("chromium", "google-chrome", "microsoft-edge"):
            if shutil.which(b):
                subprocess.Popen([b, f"--app={url}"])
                return
    webbrowser.open(url)
