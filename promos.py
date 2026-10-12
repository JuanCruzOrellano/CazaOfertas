"""Promos de bancos y billeteras, y calendario de eventos (Hot Sale, CyberMonday…).

La lista vive en GitHub (web/promos.json): si se actualiza ahí, les llega a todos sin
publicar una versión nueva. Si no hay internet, se usa la que vino con la app.
"""
import json
import os
import time

import core

URL = "https://raw.githubusercontent.com/JuanCruzOrellano/CazaOfertas/main/web/promos.json"
LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "promos.json")
CACHE = os.path.join(core.DATA_DIR, "promos_cache.json")


def _leer(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def datos():
    cache = _leer(CACHE) or {}
    if cache.get("datos") and time.time() - cache.get("ts", 0) < 6 * 3600:
        return cache["datos"]
    try:
        import actualizador
        d = json.loads(actualizador._bajar(URL, timeout=15))
        if d.get("promos") is not None:
            try:
                with open(CACHE, "w", encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "datos": d}, f, ensure_ascii=False)
            except OSError:
                pass
            return d
    except Exception:  # noqa: BLE001
        pass
    local = _leer(LOCAL) or {}
    viejo = cache.get("datos") or {}
    # el más nuevo de los dos
    return viejo if viejo.get("actualizado", "") > local.get("actualizado", "") else (local or viejo)


def dominio(url):
    u = (url or "").split("://")[-1].split("/")[0].lower()
    return u[4:] if u.startswith("www.") else u


def para_app(mis_medios):
    """Lo que necesita la interfaz: medios, promos vigentes, eventos y el rubro de cada tienda."""
    d = datos() or {}
    hoy = time.strftime("%Y-%m-%d")

    def vigente(p):
        fin = p.get("hasta") or max(p.get("fechas") or ["0"])
        return fin >= hoy

    rubros = {}
    try:
        import parecidas
        for t in parecidas.directorio():
            rubros[dominio(t["base"])] = t.get("rubro")
    except Exception:  # noqa: BLE001
        pass
    return {"medios": d.get("medios", []), "promos": [p for p in d.get("promos", []) if vigente(p)],
            "eventos": d.get("eventos", []), "actualizado": d.get("actualizado"),
            "rubros": rubros, "mis_medios": list(mis_medios or [])}
