"""Tiendas parecidas, automáticamente.

Cuando agregás una tienda, la app lee sus categorías (zapatos, botas, carteras…; o
perfumes, maquillaje…) y las compara con las de un directorio de tiendas conocidas.
Las que más se parecen se usan para comparar precios, aunque no las hayas agregado.

El directorio vive en GitHub (web/tiendas.json): si se suman tiendas ahí, les llega
a todos sin publicar una versión nueva.
"""
import json
import math
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import core
import stores

URL_DIRECTORIO = "https://raw.githubusercontent.com/JuanCruzOrellano/CazaOfertas/main/web/tiendas.json"
LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "tiendas.json")
CACHE_DIR = os.path.join(core.DATA_DIR, "tiendas_directorio.json")
CACHE_PERFILES = os.path.join(core.DATA_DIR, "tiendas_perfiles.json")
DIAS_PERFIL = 7

_lock = threading.Lock()
_perfiles = {}      # base -> {"ts", "palabras": [...], "ok": bool}
_cargado = [False]

VACIAS = set("""de del la el los las y e o u para con por sin en a al ver todo todos todas toda
ofertas oferta sale nuevo nuevos nueva nuevas hombre hombres mujer mujeres nino ninos nina ninas
kids unisex otros otras otro varios varias mas outlet coleccion colecciones temporada marcas marca
categoria categorias productos producto hot cyber black friday promo promos promocion promociones
liquidacion destacados destacado novedades novedad lanzamientos especiales especial home inicio
tienda online envio gratis regalo regalos dia dias semana week menu shop the and for adultos adulto
junior juniors bebe bebes linea lineas tipo tipos""".split())


def _limpiar(texto):
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    palabras = []
    for w in re.findall(r"[a-z]{3,}", t):
        if w in VACIAS:
            continue
        if len(w) > 4 and w.endswith("es") and w[-3] not in "aeiou":
            w = w[:-2]
        elif len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        palabras.append(w)
    return palabras


# ------------------------------------------------------------------ persistencia
def _leer(path, defecto):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return defecto


def _escribir(path, datos):
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def directorio():
    """Lista de tiendas conocidas: la de GitHub (se actualiza sola) o la que vino con la app."""
    cache = _leer(CACHE_DIR, {})
    if cache.get("tiendas") and time.time() - cache.get("ts", 0) < 86400:
        return cache["tiendas"]
    try:
        import actualizador
        datos = json.loads(actualizador._bajar(URL_DIRECTORIO, timeout=15))
        if datos.get("tiendas"):
            _escribir(CACHE_DIR, {"ts": time.time(), "tiendas": datos["tiendas"]})
            return datos["tiendas"]
    except Exception:  # noqa: BLE001
        pass
    if cache.get("tiendas"):
        return cache["tiendas"]
    return _leer(LOCAL, {}).get("tiendas", [])


def _cargar():
    if not _cargado[0]:
        _perfiles.update(_leer(CACHE_PERFILES, {}))
        _cargado[0] = True


def perfil(base, platform="vtex", forzar=False):
    """Palabras que describen qué vende una tienda (sale de sus categorías)."""
    base = base.rstrip("/")
    with _lock:
        _cargar()
        p = _perfiles.get(base)
    if p and not forzar and time.time() - p.get("ts", 0) < DIAS_PERFIL * 86400:
        return p
    palabras, ok = [], False
    try:
        if platform == "shopify":
            nombres = [c["name"] for c in stores.Shopify.categorias(base)]
        else:  # rápido: un solo intento corto, así una tienda caída no demora todo
            nombres = []

            def recorrer(nodos):
                for n in nodos or []:
                    nombres.append(str(n.get("name") or ""))
                    recorrer(n.get("children"))
            recorrer(stores._get(base + "/api/catalog_system/pub/category/tree/3", timeout=12, intentos=1))
        for nombre in nombres:
            palabras += _limpiar(nombre)
        ok = bool(palabras)
    except Exception:  # noqa: BLE001
        ok = False
    p = {"ts": time.time(), "palabras": sorted(set(palabras)), "ok": ok}
    if not ok and platform == "vtex":
        p["ts"] = time.time() - (DIAS_PERFIL - 1) * 86400  # si falló, se reintenta en un día
    with _lock:
        _perfiles[base] = p
        _escribir(CACHE_PERFILES, _perfiles)
    return p


def _idf(perfiles):
    n = len(perfiles) or 1
    df = {}
    for p in perfiles:
        for w in p:
            df[w] = df.get(w, 0) + 1
    return {w: math.log(1 + n / c) for w, c in df.items()}


def _parecido(a, b, idf):
    comunes = a & b
    if not comunes:
        return 0.0
    num = sum(idf.get(w, 1) ** 2 for w in comunes)
    na = math.sqrt(sum(idf.get(w, 1) ** 2 for w in a))
    nb = math.sqrt(sum(idf.get(w, 1) ** 2 for w in b))
    return num / (na * nb) if na and nb else 0.0


def similares(base, platform="vtex", n=8, minimo=0.12):
    """Las tiendas del directorio que venden cosas parecidas a `base`, de más a menos parecida."""
    base = base.rstrip("/")
    propio = set(perfil(base, platform)["palabras"])
    if not propio:
        return []
    tiendas = [t for t in directorio() if t["base"].rstrip("/") != base]
    with ThreadPoolExecutor(max_workers=6) as ex:
        perfiles = list(ex.map(lambda t: perfil(t["base"]), tiendas))
    vivas = [(t, set(p["palabras"])) for t, p in zip(tiendas, perfiles) if p["ok"]]
    idf = _idf([s for _, s in vivas] + [propio])
    puntaje = sorted(((_parecido(propio, s, idf), t) for t, s in vivas), key=lambda x: -x[0])
    out = [dict(t, parecido=round(p, 2)) for p, t in puntaje if p >= minimo][:n]
    # Las que venden de todo (Carrefour, Jumbo…) sirven de respaldo para productos de marca
    if len(out) < 3:
        for t, _ in vivas:
            if t.get("rubro") == "general" and t["base"] not in [o["base"] for o in out]:
                out.append(dict(t, parecido=0))
    return out


def precalentar(marcas):
    """Arma los perfiles en segundo plano para que la primera comparación sea rápida."""
    def tarea():
        try:
            for m in marcas:
                similares(m["base_url"], m["platform"])
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=tarea, daemon=True).start()
