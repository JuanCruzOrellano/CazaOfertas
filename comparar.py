"""Busca el mismo producto en otras tiendas VTEX: por su código de modelo o por su
código de barras (EAN), que es igual en todas las tiendas y sirve para cualquier rubro."""
import time
from concurrent.futures import ThreadPoolExecutor

import stores
import talles as T

# Multimarcas argentinas que usan VTEX. Se pueden editar en Ajustes.
TIENDAS_DEFECTO = [
    "https://www.grid.com.ar",
    "https://www.dexter.com.ar",
    "https://www.moov.com.ar",
    "https://www.stockcenter.com.ar",
    "https://www.sporting.com.ar",
    "https://www.newsport.com.ar",
    "https://www.solodeportes.com.ar",
    "https://www.nike.com.ar",
    "https://www.topper.com.ar",
]

_cache = {}  # (tienda, codigo) -> (ts, resultado)


NOMBRES = {"perfumeriasrouge": "Rouge", "stockcenter": "Stock Center", "solodeportes": "Solo Deportes",
           "newsport": "Newsport", "masonline": "Más Online", "oncity": "On City", "fravega": "Frávega"}


def nombre_tienda(base):
    clave = base.split("://")[-1].replace("www.", "").split(".")[0].lower()
    if clave in NOMBRES:
        return NOMBRES[clave]
    return base.split("://")[-1].replace("www.", "").split(".")[0].capitalize()


def comparar(codigo, tiendas, cfg, excluir=None, eans=None):
    """Devuelve una fila por tienda: precio, si está tu talle, link… ordenado de más barato a más caro.
    Busca por código de modelo y, si no aparece, por código de barras."""
    codigo = (codigo or "").strip()
    eans = [e for e in (eans or []) if e]
    if len(codigo) < 4 and not eans:
        return []

    def una(base):
        clave = (base, codigo.lower(), tuple(eans[:4]))
        if clave in _cache and time.time() - _cache[clave][0] < 1800:
            return _cache[clave][1]
        fila = {"tienda": nombre_tienda(base), "base": base}
        try:
            encontrados = stores.Vtex.buscar_codigo(base, codigo) if len(codigo) >= 4 else []
            if not encontrados and eans:
                encontrados = stores.Vtex.buscar_ean(base, eans)
            if not encontrados:
                fila["estado"] = "no_esta"
            else:
                mejor = None
                for it in encontrados:
                    mt = T.mi_talle(it, cfg)
                    precio = mt["precio"] if mt and mt["precio"] else it["price"]
                    disp = mt["disponible"] if mt else it["available"]
                    cand = {"nombre": it["name"], "precio": precio, "lista": it["list_price"],
                            "disponible": disp, "mi_talle": mt, "link": it["link"], "cuotas": it.get("cuotas"),
                            "image": it.get("image")}
                    if not mejor or (cand["disponible"], -cand["precio"]) > (mejor["disponible"], -mejor["precio"]):
                        mejor = cand
                fila.update(mejor, estado="ok")
        except stores.StoreError as e:
            fila.update(estado="error", error=str(e))
        except Exception as e:  # noqa: BLE001
            fila.update(estado="error", error=f"{e}")
        _cache[clave] = (time.time(), fila)
        return fila

    lista = [t for t in tiendas if t and t.rstrip("/") != (excluir or "").rstrip("/")]
    with ThreadPoolExecutor(max_workers=6) as ex:
        filas = list(ex.map(una, lista))
    orden = {"ok": 0, "no_esta": 1, "error": 2}
    filas.sort(key=lambda f: (orden[f["estado"]], not f.get("disponible"), f.get("precio") or 9e12))
    return filas
