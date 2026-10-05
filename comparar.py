"""Busca el mismo producto (por su código de modelo) en otras tiendas VTEX."""
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


def nombre_tienda(base):
    return base.split("://")[-1].replace("www.", "").split(".")[0].capitalize()


def comparar(codigo, tiendas, cfg, excluir=None):
    """Devuelve una fila por tienda: precio, si está tu talle, link… ordenado de más barato a más caro."""
    codigo = (codigo or "").strip()
    if len(codigo) < 4:
        return []

    def una(base):
        clave = (base, codigo.lower())
        if clave in _cache and time.time() - _cache[clave][0] < 1800:
            return _cache[clave][1]
        fila = {"tienda": nombre_tienda(base), "base": base}
        try:
            encontrados = stores.Vtex.buscar_codigo(base, codigo)
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
