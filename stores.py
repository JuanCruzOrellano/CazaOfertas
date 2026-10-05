"""Adaptadores de tiendas: saben leer el catálogo de cada tipo de tienda online.

- VTEX: la plataforma que usan nike.com.ar, topper.com.ar y muchas tiendas argentinas.
- Shopify: tiendas que exponen /products.json.

Cada adaptador devuelve productos normalizados:
    {"pid", "name", "link", "image", "price", "list_price", "available"}
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
PAUSA = 0.4  # segundos entre pedidos (Shopify)
HILOS = 6    # páginas que se piden a la vez (VTEX)
TOPE_VTEX = 2500  # VTEX no deja pasar de este número de producto en una búsqueda


class StoreError(Exception):
    def __init__(self, msg, codigo=None, no_reintentar=False):
        super().__init__(msg)
        self.codigo = codigo
        self.no_reintentar = no_reintentar


def normalizar_url(url):
    url = (url or "").strip()
    if not url:
        raise StoreError("Falta la dirección de la tienda.")
    if not url.startswith("http"):
        url = "https://" + url
    p = urllib.parse.urlparse(url)
    host = p.netloc
    if host.count(".") == 2 and host.endswith(".com.ar") or host.count(".") == 1:
        if not host.startswith("www."):
            host = "www." + host
    return f"{p.scheme}://{host}"


try:
    # curl_cffi se hace pasar por Chrome: tiendas protegidas por Cloudflare
    # (como nike.com.ar) bloquean al Python común pero dejan pasar a esto.
    from curl_cffi import requests as _creq
except Exception:  # noqa: BLE001
    _creq = None

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-AR,es;q=0.9",
}


import gzip
import threading

_local = threading.local()


def _get(url, headers_out=None, timeout=30, intentos=3):
    """GET que devuelve JSON. Reintenta si la tienda está saturada o se corta la conexión."""
    for i in range(intentos):
        try:
            if _creq is not None:
                return _get_chrome(url, headers_out, timeout)
            return _get_urllib(url, headers_out, timeout)
        except StoreError as e:
            reintentable = e.codigo in (None, 429, 500, 502, 503, 504) and not e.no_reintentar
            if not reintentable or i == intentos - 1:
                raise
            time.sleep(1.5 * (i + 1))


def _sesion():
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = _creq.Session(impersonate="chrome")
    return s


def _get_chrome(url, headers_out, timeout):
    try:
        r = _sesion().get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        _local.s = None
        raise StoreError(f"No se pudo conectar con la tienda ({e})") from e
    if r.status_code >= 400:
        raise StoreError(f"La tienda respondió con error {r.status_code}", r.status_code)
    if headers_out is not None:
        headers_out.update({k.lower(): v for k, v in r.headers.items()})
    try:
        return json.loads(r.content.decode("utf-8", "replace"))
    except ValueError as e:
        raise StoreError("La tienda no devolvió datos de productos (puede estar bloqueando la app)",
                         no_reintentar=True) from e


def _get_urllib(url, headers_out=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **HEADERS})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if headers_out is not None:
                headers_out.update({k.lower(): v for k, v in r.headers.items()})
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
    except urllib.error.HTTPError as e:
        raise StoreError(f"La tienda respondió con error {e.code}", e.code) from e
    except urllib.error.URLError as e:
        raise StoreError(f"No se pudo conectar con la tienda ({e.reason})") from e
    except TimeoutError as e:
        raise StoreError("La tienda tardó demasiado en responder") from e
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except ValueError as e:
        raise StoreError("La tienda no devolvió datos de productos", no_reintentar=True) from e


# --------------------------------------------------------------------- VTEX
class Vtex:
    nombre = "vtex"

    @staticmethod
    def detectar(base):
        data = _get(base + "/api/catalog_system/pub/category/tree/1", timeout=20)
        return isinstance(data, list)

    @staticmethod
    def categorias(base):
        tree = _get(base + "/api/catalog_system/pub/category/tree/3")
        out = []

        def walk(nodes, path_ids, path_names):
            for n in nodes:
                ids = path_ids + [str(n["id"])]
                names = path_names + [n["name"].strip()]
                out.append({"id": "/" + "/".join(ids) + "/",
                            "name": " › ".join(names),
                            "depth": len(ids) - 1})
                walk(n.get("children") or [], ids, names)

        walk(tree, [], [])
        return out

    @staticmethod
    def conteos(base, ids):
        """Cuántos productos tiene cada categoría (para mostrarlo al elegir)."""
        from concurrent.futures import ThreadPoolExecutor

        def uno(cid):
            hdr = {}
            try:
                fq = urllib.parse.quote("C:" + cid, safe="")
                _get(f"{base}/api/catalog_system/pub/products/search?fq={fq}&_from=0&_to=0", hdr, timeout=20)
                return cid, _total(hdr.get("resources"))
            except StoreError:
                return cid, None

        with ThreadPoolExecutor(max_workers=6) as ex:
            return dict(ex.map(uno, ids))

    @staticmethod
    def productos(base, categorias, progreso=None, tope=None):
        """Descarga los productos de las categorías, varias páginas a la vez.

        tope: si se pasa un precio, como las páginas vienen ordenadas de más barato a
        más caro, deja de descargar cuando una tanda entera ya está por encima del tope.
        """
        from concurrent.futures import ThreadPoolExecutor

        vistos = {}
        lock = threading.Lock()
        arbol = []

        def url(cid, desde):
            q = f"_from={desde}&_to={desde + 49}&O=OrderByPriceASC"
            if cid:
                q = "fq=" + urllib.parse.quote("C:" + cid, safe="") + "&" + q
            return f"{base}/api/catalog_system/pub/products/search?{q}"

        def guardar(cid, lote):
            precios = []
            with lock:
                for p in lote:
                    item = Vtex._normalizar(p)
                    if not item:
                        continue
                    cs = [x for x in (p.get("categoriesIds") or []) if isinstance(x, str)]
                    item["cats"] = cs or ([cid] if cid else [])
                    vistos[item["pid"]] = item
                    precios.append(item["price"])
                n = len(vistos)
            if progreso:
                progreso(n)
            return min(precios) if precios else None

        def hijos(cid):
            if not arbol:
                arbol.extend(Vtex.categorias(base))
            prof = cid.count("/") - 1 if cid else 0
            return [c["id"] for c in arbol
                    if c["id"].startswith(cid or "/") and c["id"].count("/") - 1 == prof + 1]

        def una_categoria(cid, ex):
            hdr = {}
            primero = _get(url(cid, 0), hdr)
            minimo = guardar(cid, primero or [])
            total = _total(hdr.get("resources")) or len(primero or [])
            if total > TOPE_VTEX + 50:
                # Demasiados productos para una sola búsqueda: se divide en subcategorías
                subs = hijos(cid)
                if subs:
                    for s in subs:
                        una_categoria(s, ex)
                    return
            if tope is not None and minimo is not None and minimo > tope:
                return
            desdes = list(range(50, min(total, TOPE_VTEX + 50), 50))
            for i in range(0, len(desdes), HILOS):
                tanda = desdes[i:i + HILOS]
                lotes = list(ex.map(lambda d: _get(url(cid, d)), tanda))
                minimos = [guardar(cid, l or []) for l in lotes]
                if tope is not None and minimos and minimos[-1] is not None and minimos[-1] > tope:
                    break

        with ThreadPoolExecutor(max_workers=HILOS) as ex:
            for c in (categorias or [{"id": ""}]):
                una_categoria(c["id"], ex)
        return list(vistos.values())

    @staticmethod
    def _normalizar(p):
        disponibles, todos = [], []
        imagen = None
        talles = {}       # talle -> [talle, disponible, precio]
        cuotas = None     # mejor plan sin interés de un talle con stock
        for it in p.get("items") or []:
            if not imagen and it.get("images"):
                imagen = it["images"][0].get("imageUrl")
            nombre_talle = Vtex._talle(it)
            for s in it.get("sellers") or []:
                o = s.get("commertialOffer") or {}
                precio = o.get("Price") or 0
                if precio <= 0:
                    continue
                par = (precio, o.get("ListPrice") or precio)
                todos.append(par)
                hay = (o.get("AvailableQuantity") or 0) > 0
                if hay:
                    disponibles.append(par)
                    for c in o.get("Installments") or []:
                        if c.get("InterestRate") == 0 and (not cuotas or c["NumberOfInstallments"] > cuotas["n"]):
                            cuotas = {"n": c["NumberOfInstallments"], "valor": c.get("Value")}
                if nombre_talle:
                    prev = talles.get(nombre_talle)
                    if not prev or (hay and not prev[1]) or (hay == prev[1] and precio < prev[2]):
                        talles[nombre_talle] = [nombre_talle, hay, float(precio)]
        base = disponibles or todos
        if not base:
            return None
        precio, lista = min(base)
        return {
            "pid": str(p.get("productId")),
            "name": (p.get("productName") or "").strip(),
            "link": p.get("link") or "",
            "image": imagen or "",
            "price": float(precio),
            "list_price": float(max(lista, precio)),
            "available": bool(disponibles),
            "tipo": Vtex._tipo(p),
            "ref": (p.get("productReference") or p.get("productReferenceCode") or "").strip(),
            "talles": list(talles.values()),
            "cuotas": cuotas,
            "cats_nombres": [c.strip("/").replace("/", " › ") for c in (p.get("categories") or [])[:1]],
        }

    @staticmethod
    def _talle(it):
        for campo in ("Talle", "talle", "TALLE", "Size", "Tamaño", "Talla", "Numero", "Número"):
            v = it.get(campo)
            if isinstance(v, list) and v:
                return str(v[0]).strip()
        for v in it.get("variations") or []:
            nombre = v.get("name") if isinstance(v, dict) else v
            if str(nombre).lower() in ("talle", "size", "talla", "numero", "número") and isinstance(v, dict):
                vals = v.get("values") or []
                if vals:
                    return str(vals[0]).strip()
        m = re.search(r"(?:Talle|Size|Talla)\s*:\s*([^\s]+(?:\s?[YC])?)", it.get("name") or "", re.I)
        return m.group(1) if m else None

    # --- búsquedas puntuales (seguidos y comparador)
    @staticmethod
    def por_id(base, pid):
        r = _get(f"{base}/api/catalog_system/pub/products/search?fq=productId:{urllib.parse.quote(str(pid))}")
        return Vtex._con_cats(r[0]) if r else None

    @staticmethod
    def por_link(url):
        """Link de un producto (…/algo/p) -> (base, producto)."""
        p = urllib.parse.urlparse(url if url.startswith("http") else "https://" + url)
        base = f"{p.scheme}://{p.netloc}"
        partes = [x for x in p.path.split("/") if x]
        if len(partes) < 2 or partes[-1] != "p":
            raise StoreError("Ese link no parece de un producto (tiene que terminar en /p).")
        r = _get(f"{base}/api/catalog_system/pub/products/search/{urllib.parse.quote(partes[-2])}/p")
        if not r:
            raise StoreError("No encontré ese producto en la tienda.")
        return base, Vtex._con_cats(r[0])

    @staticmethod
    def buscar_codigo(base, codigo):
        """Busca un código de modelo (ej. CW2288-111) en otra tienda."""
        r = _get(f"{base}/api/catalog_system/pub/products/search?ft={urllib.parse.quote(codigo)}&_from=0&_to=9",
                 timeout=20, intentos=1)
        cod = codigo.lower().replace(" ", "")
        out = []
        for p in r or []:
            texto = json.dumps(p, ensure_ascii=False).lower().replace(" ", "")
            if cod in texto:
                it = Vtex._con_cats(p)
                if it:
                    out.append(it)
        return out

    @staticmethod
    def _con_cats(p):
        it = Vtex._normalizar(p)
        if it:
            it["cats"] = [x for x in (p.get("categoriesIds") or []) if isinstance(x, str)]
        return it

    @staticmethod
    def _tipo(p):
        for campo in ("Tipo de producto", "Tipo de Producto", "Tipo", "Producto"):
            v = p.get(campo)
            if isinstance(v, list) and v and isinstance(v[0], str) and v[0].strip():
                return v[0].strip().capitalize()
        cats = p.get("categories") or []
        if cats:
            partes = [x for x in cats[0].split("/") if x]
            if partes:
                return partes[-1].strip().capitalize()
        return "Otros"


def _total(resources):
    # cabecera "resources: 0-49/795"
    try:
        return int(resources.split("/")[1])
    except Exception:
        return None


# ------------------------------------------------------------------ Shopify
class Shopify:
    nombre = "shopify"

    @staticmethod
    def detectar(base):
        data = _get(base + "/products.json?limit=1", timeout=20)
        return isinstance(data, dict) and "products" in data

    @staticmethod
    def categorias(base):
        data = _get(base + "/collections.json?limit=250")
        return [{"id": c["handle"], "name": c["title"], "depth": 0}
                for c in data.get("collections", [])]

    @staticmethod
    def productos(base, categorias, progreso=None, tope=None):  # noqa: ARG004
        vistos = {}
        rutas = [(c["id"], f"/collections/{c['id']}/products.json") for c in categorias] or [(None, "/products.json")]
        for handle, ruta in rutas:
            for page in range(1, 41):
                data = _get(f"{base}{ruta}?limit=250&page={page}")
                lote = data.get("products", [])
                if not lote:
                    break
                for p in lote:
                    item = Shopify._normalizar(base, p)
                    if item:
                        previo = vistos.get(item["pid"])
                        cats = previo["cats"] if previo else []
                        if handle and handle not in cats:
                            cats = cats + [handle]
                        item["cats"] = cats
                        vistos[item["pid"]] = item
                if progreso:
                    progreso(len(vistos))
                if len(lote) < 250:
                    break
                time.sleep(PAUSA)
        return list(vistos.values())

    @staticmethod
    def _normalizar(base, p):
        disp, todos = [], []
        for v in p.get("variants") or []:
            try:
                precio = float(v.get("price") or 0)
                lista = float(v.get("compare_at_price") or 0) or precio
            except ValueError:
                continue
            if precio <= 0:
                continue
            todos.append((precio, lista))
            if v.get("available", True):
                disp.append((precio, lista))
        b = disp or todos
        if not b:
            return None
        precio, lista = min(b)
        imgs = p.get("images") or []
        idx = next((i for i, o in enumerate(p.get("options") or [])
                    if str(o.get("name", "")).lower() in ("talle", "size", "talla", "numero", "número")), None)
        talles = {}
        if idx is not None:
            for v in p.get("variants") or []:
                n = v.get(f"option{idx + 1}")
                if n:
                    hay = bool(v.get("available", True))
                    pr = float(v.get("price") or 0)
                    prev = talles.get(n)
                    if not prev or (hay and not prev[1]):
                        talles[n] = [n, hay, pr]
        return {
            "pid": str(p.get("id")),
            "name": p.get("title", "").strip(),
            "link": f"{base}/products/{p.get('handle')}",
            "image": imgs[0].get("src", "") if imgs else "",
            "price": precio,
            "list_price": max(lista, precio),
            "available": bool(disp),
            "tipo": (p.get("product_type") or "Otros").strip().capitalize(),
            "ref": "",
            "talles": list(talles.values()),
            "cuotas": None,
        }

    @staticmethod
    def por_id(base, pid, link=None):
        if not link:
            return None
        data = _get(link.rstrip("/") + ".json")
        it = Shopify._normalizar(base, data.get("product") or {})
        if it:
            it["cats"] = []
        return it


PLATAFORMAS = {"vtex": Vtex, "shopify": Shopify}


def detectar(url):
    base = normalizar_url(url)
    candidatos = [base]
    if "://www." in base:
        candidatos.append(base.replace("://www.", "://", 1))
    errores = []
    for b in candidatos:
        for plat in (Vtex, Shopify):
            try:
                if plat.detectar(b):
                    return plat.nombre, b
            except StoreError as e:
                errores.append(str(e))
    detalle = f" Detalle: {errores[0]}." if errores else ""
    extra = "" if _creq else " (Falta un componente: cerrá la app y volvé a abrir Instalar.bat.)"
    raise StoreError(
        "No pude leer el catálogo de esa tienda. La app funciona con tiendas VTEX "
        "(como nike.com.ar o topper.com.ar) y Shopify." + detalle + extra)
