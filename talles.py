"""Talles: entiende si un producto es calzado, ropa o medias y compara con los talles del usuario.

Calzado: todo se pasa a centímetros (largo del pie), así se pueden comparar talles
argentinos (40, 41…), europeos, de EE.UU. (hombre / mujer / niños) y centímetros.
"""
import re

# Tolerancia al comparar calzado: medio número argentino ≈ 0,33 cm
TOLERANCIA_CM = 0.35

SISTEMAS_CALZADO = {
    "AR": "Argentino (40, 41…)",
    "EU": "Europeo (41, 42…)",
    "USH": "EE.UU. hombre (8, 9…)",
    "USM": "EE.UU. mujer (7, 8…)",
    "CM": "Centímetros (26,5…)",
}

LETRAS = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "4XL", "5XL"]
_ALIAS_LETRAS = {"2XL": "XXL", "3XL": "XXXL", "XXXXL": "4XL", "SMALL": "S", "MEDIUM": "M", "LARGE": "L",
                 "CH": "S", "MED": "M", "GDE": "L", "XG": "XL", "XXG": "XXL"}
UNICO = {"U", "UN", "UNICO", "ÚNICO", "TU", "TALLE UNICO", "TALLE ÚNICO", "OS", "ONE SIZE", "OSFA", "STD"}

# ------------------------------------------------------------ conversiones de calzado
def cm_desde(sistema, valor):
    """Talle del usuario en cualquier sistema -> centímetros."""
    try:
        n = float(str(valor).replace(",", ".").strip())
    except ValueError:
        return None
    return {"AR": (n - 1) / 1.5, "EU": (n - 2) / 1.5, "USH": n + 18, "USM": n + 17, "CM": n}.get(sistema)


def equivalencias(cm):
    """Centímetros -> el mismo talle en todos los sistemas (para mostrar)."""
    if not cm:
        return {}
    r = lambda x: round(x * 2) / 2  # noqa: E731 — redondeo a medio número
    return {"AR": r(cm * 1.5 + 1), "EU": r(cm * 1.5 + 2), "USH": r(cm - 18), "USM": r(cm - 17),
            "CM": round(cm, 1)}


_YOUTH = {3: 22, 3.5: 22.5, 4: 23, 4.5: 23.5, 5: 23.5, 5.5: 24, 6: 24, 6.5: 24.5, 7: 25}


def _num(s):
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def calzado_cm(raw, mujer=False):
    """Talle de calzado tal como lo publica la tienda -> (cm_desde, cm_hasta) o None."""
    s = str(raw).upper().replace("TALLE", "").replace("US", "").replace("ARG", "").replace("AR", "")
    s = s.replace("½", ".5").strip()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*CM", s)
    if m:
        v = _num(m.group(1))
        return (v, v)
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*[-/]\s*(\d+(?:[.,]\d+)?)", s)
    if m:  # rango "39-42" o "35/36"
        a, b = calzado_cm(m.group(1), mujer), calzado_cm(m.group(2), mujer)
        if a and b:
            return (min(a[0], b[0]), max(a[1], b[1]))
        return None
    m = re.match(r"^\s*(W|M)?\s*(\d+(?:[.,]\d+)?)\s*([YCK])?\s*$", s)
    if not m:
        return None
    pref, n, suf = m.group(1), _num(m.group(2)), m.group(3)
    if n is None:
        return None
    if suf == "Y":  # juvenil (Nike)
        v = _YOUTH.get(n, n + 18.5)
        return (v, v)
    if suf in ("C", "K"):  # niños chicos
        v = 0.85 * n + 7.8
        return (v, v)
    if 30 <= n <= 50:  # numeración argentina
        v = (n - 1) / 1.5
        return (v, v)
    if 2 <= n <= 17:  # EE.UU.
        v = n + (17 if (mujer or pref == "W") else 18)
        return (v, v)
    if 20 <= n < 30:  # centímetros sueltos
        return (n, n)
    return None


# ------------------------------------------------------------------ ropa
def letra(raw):
    s = str(raw).upper().strip()
    s = re.sub(r"\(.*?\)", "", s).strip()
    s = s.replace("TALLE", "").strip()
    if s in UNICO:
        return "U"
    s = _ALIAS_LETRAS.get(s, s)
    if s in LETRAS:
        return s
    m = re.match(r"^(XXS|XS|S|M|L|XL|XXL|XXXL|[2-5]XL)\b", s)
    if m:
        return _ALIAS_LETRAS.get(m.group(1), m.group(1))
    return None


# ------------------------------------------------------------- tipo de producto
_CLASES = [
    ("medias", ("media", "soquete", "calcet")),
    ("calzado", ("calzado", "zapatilla", "botin", "botín", "ojota", "sandalia", "bota", "pantufla", "slide", "chancla")),
    ("ropa_inf", ("pantal", "short", "calza", "jogger", "bermuda", "pollera", "falda", "malla", "traje de ba", "jean", "babucha")),
    ("ropa_sup", ("remera", "camiseta", "buzo", "campera", "musculosa", "chomba", "top", "camisa", "chaleco",
                  "body", "vestido", "rompeviento", "polo", "sweater", "hoodie", "conjunto", "enterito")),
]

NOMBRES_CLASE = {"calzado": "Calzado", "ropa_sup": "Ropa (arriba)", "ropa_inf": "Ropa (abajo)",
                 "medias": "Medias", "otro": "Talle único / accesorio"}


def clase(tipo, nombre="", talles=None):
    """calzado / ropa_sup / ropa_inf / medias / otro."""
    texto = f"{tipo or ''} {nombre or ''}".lower()
    for c, claves in _CLASES:
        if any(k in texto for k in claves):
            return c
    # Por los talles que trae
    valores = [t[0] for t in (talles or [])]
    if valores:
        if sum(1 for v in valores if letra(v) and letra(v) != "U") >= len(valores) / 2:
            return "ropa_sup"
        if sum(1 for v in valores if calzado_cm(v)) >= len(valores) / 2:
            return "calzado"
    return "otro"


# ------------------------------------------------------------- comparación
def _es_mujer(it):
    t = " ".join([it.get("name") or ""] + list(it.get("cats_nombres") or [])).lower()
    return any(k in t for k in ("mujer", "women", "wmns", "femenino", "dama"))


def coincide(raw, cls, cfg, mujer=False):
    """¿El talle `raw` de la tienda es el talle del usuario para esta clase?"""
    if cls == "calzado":
        cm = cfg.get("calzado_cm")
        r = calzado_cm(raw, mujer)
        return bool(cm and r and r[0] - TOLERANCIA_CM <= cm <= r[1] + TOLERANCIA_CM)
    if cls == "medias":
        cm = cfg.get("calzado_cm")
        r = calzado_cm(raw, mujer)
        if cm and r:
            return r[0] - 0.5 <= cm <= r[1] + 0.5
        l = letra(raw)
        if cm and l:  # S/M/L de medias según el largo del pie (tabla Nike)
            rangos = {"XS": (19.5, 22), "S": (22, 24.5), "M": (24.5, 27), "L": (27, 29.5), "XL": (29.5, 32)}
            a = rangos.get(l)
            return bool(a and a[0] - 0.2 <= cm <= a[1] + 0.2)
        return l == "U"
    if cls in ("ropa_sup", "ropa_inf"):
        quiero = cfg.get(cls)
        if not quiero:
            return False
        l = letra(raw)
        if l == "U":
            return True
        if str(quiero).upper() == str(raw).upper().strip():
            return True
        return l is not None and l == letra(quiero)
    return True


def configurado(cls, cfg):
    if cls in ("calzado", "medias"):
        return bool(cfg.get("calzado_cm"))
    if cls in ("ropa_sup", "ropa_inf"):
        return bool(cfg.get(cls))
    return False


def mi_talle(it, cfg):
    """Mira los talles del producto contra los del usuario.

    Devuelve None si no aplica (sin datos de talle, talle único o el usuario no configuró
    ese tipo). Si aplica: {"clase", "talles": [nombres que coinciden], "disponible", "precio"}.
    """
    talles = it.get("talles") or []
    if not talles or not cfg:
        return None
    cls = clase(it.get("tipo"), it.get("name"), talles)
    if cls == "otro" or not configurado(cls, cfg):
        return None
    if all(letra(t[0]) == "U" for t in talles):
        return None
    mujer = _es_mujer(it)
    mios = [t for t in talles if coincide(t[0], cls, cfg, mujer)]
    if cls == "calzado" and len(mios) > 1:
        # Quedarse con el/los más cercanos (ej. AR 42 = US 9.5 antes que US 9)
        cm = cfg["calzado_cm"]
        dist = {t[0]: min(abs(cm - x) for x in calzado_cm(t[0], mujer)) for t in mios}
        mejor = min(dist.values())
        mios = [t for t in mios if dist[t[0]] <= mejor + 0.12]
    disp = [t for t in mios if t[1]]
    precio = min((t[2] for t in disp if t[2]), default=None) or min((t[2] for t in mios if t[2]), default=None)
    return {"clase": cls, "talles": [t[0] for t in mios], "disponible": bool(disp), "precio": precio}


def preparar(cfg):
    """Completa la configuración guardada con los centímetros calculados."""
    cfg = dict(cfg or {})
    cal = cfg.get("calzado") or {}
    cfg["calzado_cm"] = cm_desde(cal.get("sistema", "AR"), cal.get("valor")) if cal.get("valor") else None
    return cfg
