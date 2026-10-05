"""CazaOfertas — sigue los precios de tus marcas y te avisa cuando algo entra en tu rango.

Uso:
    python app.py            abre la aplicación
    python app.py --revisar  revisa precios en segundo plano y avisa (lo usa la tarea de Windows)
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core  # noqa: E402
import stores  # noqa: E402
import windows  # noqa: E402
import actualizador  # noqa: E402
import comparar  # noqa: E402
import talles  # noqa: E402
import telegram  # noqa: E402
from version import APP_VERSION  # noqa: E402

PUERTO = int(os.environ.get("CAZAOFERTAS_PUERTO", "8767"))
URL = f"http://127.0.0.1:{PUERTO}/"
VERSION = 15
# Si corre como .exe (PyInstaller), los archivos vienen empaquetados en sys._MEIPASS
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(BASE, "web")
TIPOS_MIME = {".png": "image/png", ".ico": "image/x-icon", ".html": "text/html; charset=utf-8"}

db = core.Store()
revisando = threading.Event()
ultimo_ping = [0.0]


def revisar_en_fondo(bid=None):
    if revisando.is_set():
        return False

    def tarea():
        revisando.set()
        try:
            nuevas = []
            if bid:
                try:
                    nuevas = db.revisar(bid)
                except Exception:  # noqa: BLE001
                    pass
            else:
                nuevas = db.revisar_todas()
            avisar(nuevas, toast=False)
        finally:
            revisando.clear()

    threading.Thread(target=tarea, daemon=True).start()
    return True


def avisar(novedades, toast=True):
    """Manda las novedades: notificación de Windows y/o Telegram."""
    if not novedades:
        return
    if toast:
        windows.avisar_novedades(novedades, URL)
    telegram.avisar_novedades(db.get_setting("telegram"), novedades)


def programacion():
    """Deja la tarea de Windows según los ajustes (modo evento o cada X horas)."""
    ev = db.get_setting("evento") or {}
    horas = db.get_setting("horas", 0)
    if ev.get("activo") and ev.get("hasta", 0) > time.time():
        return windows.programar(horas, minutos=15)
    if ev.get("activo"):  # el evento terminó: volver a lo normal
        db.set_setting("evento", {**ev, "activo": False})
    return windows.programar(horas)


def ajustes():
    tg = db.get_setting("telegram") or {}
    cfg = db.cfg_talles()
    return {"horas": db.get_setting("horas", 0), "es_windows": windows.ES_WINDOWS,
            "evento": db.get_setting("evento") or {"activo": False},
            "talles": db.get_setting("talles", {}),
            "equivalencias": talles.equivalencias(cfg.get("calzado_cm")),
            "sistemas": talles.SISTEMAS_CALZADO,
            "telegram": {"conectado": bool(tg.get("chat_id")), "nombre": tg.get("nombre"),
                         "bot": tg.get("bot"), "activo": tg.get("activo", True)},
            "tiendas_comparar": db.get_setting("tiendas_comparar") or comparar.TIENDAS_DEFECTO}


def detalle_producto(q):
    if q.get("seguido"):
        with core._lock:
            r = db.con.execute("SELECT * FROM seguidos WHERE id=?", (int(q["seguido"]),)).fetchone()
        if not r:
            raise ValueError("Ese producto ya no está en seguidos")
        d = db._para_ui(dict(r), r["base_url"])
        d["platform"] = r["platform"]
    else:
        b = db.marca(int(q["marca"]))
        with core._lock:
            r = db.con.execute("SELECT * FROM products WHERE brand_id=? AND pid=?",
                               (b["id"], q["pid"])).fetchone()
        if not r:
            raise ValueError("No encontré ese producto")
        d = db._para_ui(dict(r), b["base_url"])
        d["platform"] = b["platform"]
        d["marca_nombre"] = b["name"]
        d["solo_mi_talle"] = b.get("solo_mi_talle", 1)
    d["historial"] = db.historial(d["tienda"], d["pid"])
    s = db.seguido(d["tienda"], d["pid"])
    d["seguido"] = {"id": s["id"], "objetivo": s["objetivo"]} if s else None
    d["tiene_telegram"] = bool((db.get_setting("telegram") or {}).get("chat_id"))
    return d


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---- utilidades
    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _ruta(self):
        p = urllib.parse.urlparse(self.path)
        return p.path.rstrip("/") or "/", dict(urllib.parse.parse_qsl(p.query))

    # ---- GET
    def do_GET(self):  # noqa: N802
        ruta, q = self._ruta()
        try:
            if ruta == "/":
                with open(os.path.join(WEB, "index.html"), "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif ruta in ("/logo.png", "/favicon.ico"):
                nombre = "logo.png"
                with open(os.path.join(WEB, nombre), "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(body)
            elif ruta == "/api/actualizacion":
                actualizador.buscar_en_fondo(si_pasaron=10 * 60)
                self._json({**actualizador.estado, "es_exe": actualizador.es_exe(),
                            "pagina": actualizador.PAGINA})
            elif ruta == "/api/ping":
                ultimo_ping[0] = time.time()
                self._json({"ok": True, "app": "cazaofertas", "version": VERSION, "app_version": APP_VERSION})
            elif ruta == "/api/marcas":
                self._json({"marcas": db.marcas(), "revisando": revisando.is_set(),
                            "ultima_revision": db.get_setting("ultima_revision")})
            elif ruta.startswith("/api/marca/") and ruta.endswith("/tipos"):
                ids = q.get("ids")
                self._json(db.tipos(int(ruta.split("/")[3]),
                                    None if ids is None else json.loads(ids)))
            elif ruta.startswith("/api/marca/") and ruta.endswith("/rango"):
                bid = int(ruta.split("/")[3])
                self._json({"productos": db.productos_en_rango(bid)})
            elif ruta == "/api/novedades":
                bid = int(q["marca"]) if q.get("marca") else None
                self._json({"novedades": db.novedades(bid)})
            elif ruta == "/api/ajustes":
                self._json(ajustes())
            elif ruta == "/api/producto":
                self._json(detalle_producto(q))
            elif ruta == "/api/seguidos":
                self._json({"seguidos": db.seguidos()})
            elif ruta == "/api/equivalencias":
                self._json(talles.equivalencias(talles.cm_desde(q.get("sistema", "AR"), q.get("valor"))))
            else:
                self._json({"error": "No encontrado"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    # ---- POST / PUT / DELETE
    def do_POST(self):  # noqa: N802
        ruta, _ = self._ruta()
        try:
            d = self._body()
            if ruta == "/api/detectar":
                plat, base = stores.detectar(d.get("url"))
                cats = stores.PLATAFORMAS[plat].categorias(base)
                self._json({"platform": plat, "base_url": base, "categories": cats})
            elif ruta == "/api/conteos":
                plat = stores.PLATAFORMAS.get(d.get("platform"))
                if not plat or not hasattr(plat, "conteos"):
                    return self._json({"conteos": {}})
                self._json({"conteos": plat.conteos(d["base_url"], list(d.get("ids") or [])[:400])})
            elif ruta == "/api/marca":
                if not d.get("max_price"):
                    return self._json({"error": "Poné un precio máximo."}, 400)
                bid, _ = db.guardar_marca(d)
                revisar_en_fondo(bid)
                self._json({"id": bid})
            elif ruta.startswith("/api/marca/") and ruta.endswith("/tipos"):
                db.ocultar_tipos(int(ruta.split("/")[3]), list(d.get("ocultos") or []))
                self._json({"ok": True})
            elif ruta == "/api/revisar":
                ok = revisar_en_fondo(d.get("marca"))
                self._json({"ok": ok})
            elif ruta == "/api/vistas":
                db.marcar_vistas(d.get("marca"))
                self._json({"ok": True})
            elif ruta == "/api/ajustes":
                msg, ok = "Ajustes guardados", True
                if "talles" in d:
                    db.set_setting("talles", d["talles"] or {})
                    db.recalcular_todo()
                if "tiendas_comparar" in d:
                    lista = [stores.normalizar_url(u) for u in d["tiendas_comparar"] if str(u).strip()]
                    db.set_setting("tiendas_comparar", lista)
                if "horas" in d or "evento" in d:
                    if "horas" in d:
                        db.set_setting("horas", int(d.get("horas") or 0))
                    if "evento" in d:
                        db.set_setting("evento", d["evento"])
                    ok, msg = programacion()
                self._json({"ok": ok, "mensaje": msg, "ajustes": ajustes()})
            elif ruta == "/api/telegram/conectar":
                info = telegram.conectar(d.get("token", ""))
                db.set_setting("telegram", {"token": d["token"].strip(), **info, "activo": True})
                telegram.enviar(db.get_setting("telegram"),
                                "🦅 <b>CazaOfertas conectado.</b> Acá te van a llegar las ofertas.")
                self._json({"ok": True, **info})
            elif ruta == "/api/telegram/probar":
                telegram.enviar(db.get_setting("telegram"), "🦅 Prueba de CazaOfertas: ¡funciona!")
                self._json({"ok": True})
            elif ruta == "/api/telegram/desconectar":
                db.set_setting("telegram", {})
                self._json({"ok": True})
            elif ruta == "/api/telegram/compartir":
                telegram.enviar(db.get_setting("telegram"), d.get("texto", ""), d.get("foto"))
                self._json({"ok": True})
            elif ruta == "/api/comparar":
                tiendas = db.get_setting("tiendas_comparar") or comparar.TIENDAS_DEFECTO
                tiendas = list(dict.fromkeys(tiendas + [m["base_url"] for m in db.marcas()
                                                        if m["platform"] == "vtex"]))
                filas = comparar.comparar(d.get("ref"), tiendas, db.cfg_talles(), excluir=d.get("tienda"))
                self._json({"filas": filas})
            elif ruta == "/api/seguir":
                if d.get("link"):
                    base, it = stores.Vtex.por_link(d["link"])
                    plat = "vtex"
                else:
                    b = db.marca(int(d["marca"]))
                    with core._lock:
                        r = db.con.execute("SELECT * FROM products WHERE brand_id=? AND pid=?",
                                           (b["id"], d["pid"])).fetchone()
                    it = core._fila_a_item(dict(r))
                    base, plat = b["base_url"], b["platform"]
                obj = float(d["objetivo"]) if d.get("objetivo") else None
                sid = db.seguir(plat, base, it, obj, d.get("solo_mi_talle", True))
                self._json({"ok": True, "id": sid})
            elif ruta == "/api/dejar_de_seguir":
                db.dejar_de_seguir(int(d["id"]))
                self._json({"ok": True})
            elif ruta == "/api/actualizar":
                def salir():
                    time.sleep(0.5)
                    os._exit(0)
                ok, msg = actualizador.actualizar(salir)
                self._json({"ok": ok, "mensaje": msg})
            elif ruta == "/api/buscar_actualizacion":
                actualizador.buscar()
                self._json({**actualizador.estado, "es_exe": actualizador.es_exe()})
            elif ruta == "/api/salir":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif ruta == "/api/probar_aviso":
                windows.notificar("CazaOfertas", "Así te vas a enterar cuando algo baje de precio.")
                self._json({"ok": True})
            else:
                self._json({"error": "No encontrado"}, 404)
        except (stores.StoreError, telegram.TelegramError, ValueError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def do_PUT(self):  # noqa: N802
        ruta, _ = self._ruta()
        try:
            bid = int(ruta.split("/")[3])
            d = self._body()
            if not d.get("max_price"):
                return self._json({"error": "Poné un precio máximo."}, 400)
            _, hay_que_revisar = db.guardar_marca(d, bid)
            if hay_que_revisar:
                revisar_en_fondo(bid)
            self._json({"id": bid})
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def do_DELETE(self):  # noqa: N802
        ruta, _ = self._ruta()
        try:
            db.borrar_marca(int(ruta.split("/")[3]))
            self._json({"ok": True})
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)


def ya_abierta():
    """True si ya hay una CazaOfertas de esta misma versión abierta.
    Si hay una versión vieja abierta, la cierra para que arranque la nueva."""
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=2) as r:
            info = json.load(r)
    except Exception:  # noqa: BLE001
        return False
    if info.get("app") != "cazaofertas":
        return False
    if info.get("version") == VERSION:
        return True
    try:
        req = urllib.request.Request(URL + "api/salir", data=b"{}", method="POST")
        urllib.request.urlopen(req, timeout=3).read()
        time.sleep(1.5)
    except Exception:  # noqa: BLE001
        pass
    return False


def vigilante(server):
    """Cierra el servidor cuando se cierra la ventana (la ventana avisa que sigue viva)."""
    while True:
        time.sleep(15)
        if ultimo_ping[0] and time.time() - ultimo_ping[0] > 180 and not revisando.is_set():
            server.shutdown()
            return


def modo_revisar():
    ev = db.get_setting("evento") or {}
    if ev.get("activo") and ev.get("hasta", 0) <= time.time():
        programacion()  # terminó el evento: vuelve a la frecuencia normal
    db.revisar_todas(avisar)


def preparar_windows():
    """Copia el logo para las notificaciones y actualiza la tarea programada si la app cambió de lugar."""
    windows.copiar_logo(os.path.join(WEB, "logo.png"), core.DATA_DIR)
    ev = db.get_setting("evento") or {}
    if (db.get_setting("horas", 0) or ev.get("activo")) and windows.ES_WINDOWS:
        threading.Thread(target=programacion, daemon=True).start()


def main():
    if "--revisar" in sys.argv:
        windows.copiar_logo(os.path.join(WEB, "logo.png"), core.DATA_DIR)
        modo_revisar()
        return
    if ya_abierta():
        windows.abrir_ventana(URL)
        return
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PUERTO), Handler)
    except OSError:
        print(f"El puerto {PUERTO} está ocupado por otro programa.")
        return
    threading.Thread(target=vigilante, args=(server,), daemon=True).start()
    preparar_windows()
    actualizador.buscar_en_fondo()
    if "--sin-ventana" not in sys.argv:
        windows.abrir_ventana(URL)
    print(f"CazaOfertas abierto en {URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    socket.setdefaulttimeout(40)
    main()
