"""Base de datos y lógica de detección: qué entra en el rango, qué bajó de precio."""
import json
import os
import sqlite3
import threading
import time

import stores

DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "CazaOfertas")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "datos.db")

_lock = threading.RLock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    base_url TEXT NOT NULL,
    categories TEXT NOT NULL DEFAULT '[]',
    min_price REAL NOT NULL DEFAULT 0,
    max_price REAL NOT NULL,
    include_kw TEXT NOT NULL DEFAULT '',
    exclude_kw TEXT NOT NULL DEFAULT '',
    only_stock INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_check REAL,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS products (
    brand_id INTEGER NOT NULL,
    pid TEXT NOT NULL,
    name TEXT, link TEXT, image TEXT,
    price REAL, list_price REAL,
    available INTEGER,
    in_range INTEGER NOT NULL DEFAULT 0,
    first_seen REAL, last_seen REAL,
    PRIMARY KEY (brand_id, pid)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    pid TEXT NOT NULL,
    type TEXT NOT NULL,
    old_price REAL, new_price REAL,
    created_at REAL NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

REVISION_COMPLETA = 20 * 3600  # cada cuánto se descarga todo (no solo hasta tu precio máximo)

# Tipos de novedad
NUEVO = "nuevo"              # producto nuevo en la tienda que ya entra en tu rango
ENTRO = "entro_rango"        # estaba más caro y bajó hasta entrar en tu rango
BAJO_MAS = "bajo_mas"        # ya estaba en tu rango y bajó todavía más
REPUSO = "volvio_stock"      # estaba en rango pero sin stock, y volvió


def conectar(path=None):
    con = sqlite3.connect(path or DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    # Columnas agregadas en versiones nuevas
    for tabla, col, tipo in (("products", "tipo", "TEXT"),
                             ("products", "cats", "TEXT"),
                             ("products", "ok", "INTEGER"),
                             ("brands", "tipos_ocultos", "TEXT NOT NULL DEFAULT '[]'"),
                             ("brands", "avisar", "TEXT"),
                             ("brands", "sin_nuevos", "INTEGER NOT NULL DEFAULT 0"),
                             ("brands", "last_full", "REAL")):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({tabla})")]
        if col not in cols:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
    con.commit()
    return con


class Store:
    def __init__(self, path=None):
        self.con = conectar(path)
        self.estado = {}  # brand_id -> texto de progreso

    # ---------- ajustes
    def get_setting(self, key, default=None):
        with _lock:
            r = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_setting(self, key, value):
        with _lock, self.con:
            self.con.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    # ---------- marcas
    def marcas(self):
        with _lock:
            rows = self.con.execute("""
                SELECT b.*,
                  (SELECT COUNT(*) FROM events e WHERE e.brand_id=b.id AND e.seen=0) AS sin_ver,
                  (SELECT COUNT(*) FROM products p WHERE p.brand_id=b.id AND p.in_range=1
                       AND p.last_seen >= COALESCE(b.last_check,0) - 0.001) AS en_rango
                FROM brands b ORDER BY b.name COLLATE NOCASE""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            _decodificar(d)
            d["estado"] = self.estado.get(d["id"])
            out.append(d)
        return out

    def marca(self, bid):
        with _lock:
            r = self.con.execute("SELECT * FROM brands WHERE id=?", (bid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        _decodificar(d)
        return d

    def guardar_marca(self, datos, bid=None):
        """Guarda la marca. Devuelve (id, hay_que_revisar)."""
        avisar = datos.get("avisar")
        campos = {
            "name": datos["name"].strip() or "Marca",
            "platform": datos["platform"],
            "base_url": datos["base_url"],
            "categories": json.dumps(datos.get("categories") or []),
            "min_price": float(datos.get("min_price") or 0),
            "max_price": float(datos["max_price"]),
            "include_kw": datos.get("include_kw", "").strip(),
            "exclude_kw": datos.get("exclude_kw", "").strip(),
            "only_stock": 1 if datos.get("only_stock", True) else 0,
            "avisar": None if avisar is None else json.dumps(list(avisar)),
        }
        revisar = False
        with _lock, self.con:
            if bid:
                vieja = self.marca(bid)
                sets = ", ".join(f"{k}=?" for k in campos)
                self.con.execute(f"UPDATE brands SET {sets} WHERE id=?", (*campos.values(), bid))
                nueva = self.marca(bid)
                if vieja["base_url"] != nueva["base_url"]:
                    # Otra tienda: lo guardado no sirve
                    self.con.execute("DELETE FROM products WHERE brand_id=?", (bid,))
                    self.con.execute("DELETE FROM events WHERE brand_id=?", (bid,))
                    self.con.execute("UPDATE brands SET last_check=NULL WHERE id=?", (bid,))
                    revisar = True
                elif (clave_descarga(vieja) != clave_descarga(nueva)
                      or tope_descarga(nueva) > tope_descarga(vieja)):
                    # Ahora se descargan otras categorías: lo que aparezca por primera vez
                    # en la próxima revisión no cuenta como "nuevo" (no es nuevo en la tienda).
                    self.con.execute("UPDATE brands SET sin_nuevos=1 WHERE id=?", (bid,))
                    self._recalcular(bid)
                    revisar = True
                else:
                    # Cambió solo el rango, filtros o campanitas: recalcular sin generar avisos
                    self._recalcular(bid)
            else:
                cur = self.con.execute(
                    f"INSERT INTO brands ({', '.join(campos)}, created_at) "
                    f"VALUES ({', '.join('?' * len(campos))}, ?)",
                    (*campos.values(), time.time()))
                bid = cur.lastrowid
                revisar = True
        return bid, revisar

    def borrar_marca(self, bid):
        with _lock, self.con:
            for t in ("events", "products"):
                self.con.execute(f"DELETE FROM {t} WHERE brand_id=?", (bid,))
            self.con.execute("DELETE FROM brands WHERE id=?", (bid,))

    def _recalcular(self, bid):
        b = self.marca(bid)
        rows = self.con.execute("SELECT * FROM products WHERE brand_id=?", (bid,)).fetchall()
        for r in rows:
            d = dict(r)
            d["cats"] = _cats(d)
            d["available"] = bool(d["available"])
            sigue = not (b["last_check"] and (d["last_seen"] or 0) < b["last_check"] - 0.001)
            ver = sigue and pasa_filtros(b, d) and se_ve(b, d["cats"])
            aviso = sigue and avisa(b, d)
            self.con.execute("UPDATE products SET ok=?, in_range=? WHERE brand_id=? AND pid=?",
                             (1 if aviso else 0, 1 if ver else 0, bid, r["pid"]))

    def tipos(self, bid, ids=None):
        """Tipos de producto (con cantidad) dentro de las categorías `ids`.
        Sin `ids`, de todo lo descargado. También dice qué categorías todavía no se descargaron."""
        b = self.marca(bid)
        desde = min(b["last_full"] or b["last_check"] or 0, b["last_check"] or 0) - 60
        with _lock:
            rows = self.con.execute("""SELECT COALESCE(tipo,'Otros') AS tipo, cats FROM products
                                       WHERE brand_id=? AND last_seen >= ?""", (bid, desde)).fetchall()
        cuenta = {}
        for r in rows:
            cats = _cats(dict(r))
            if ids is not None and cats is not None and not any(
                    cubre(s, c) for s in ids for c in (cats or [""])):
                continue
            cuenta[r["tipo"]] = cuenta.get(r["tipo"], 0) + 1
        descargadas = [c["id"] for c in categorias_a_descargar(b)] or [""]
        faltan = [] if ids is None or not b["last_check"] else [
            i for i in ids if not any(cubre(d, i) for d in descargadas)]
        tipos = [{"tipo": k, "n": n, "visible": k not in b["tipos_ocultos"]}
                 for k, n in sorted(cuenta.items(), key=lambda kv: -kv[1])]
        return {"tipos": tipos, "faltan": faltan}

    def ocultar_tipos(self, bid, ocultos):
        with _lock, self.con:
            self.con.execute("UPDATE brands SET tipos_ocultos=? WHERE id=?", (json.dumps(ocultos), bid))
            self._recalcular(bid)

    # ---------- consultas para la interfaz
    def productos_en_rango(self, bid):
        b = self.marca(bid)
        with _lock:
            rows = self.con.execute("""
                SELECT * FROM products WHERE brand_id=? AND in_range=1 AND last_seen >= ?
                ORDER BY price ASC""", (bid, (b["last_check"] or 0) - 0.001)).fetchall()
        return [dict(r) for r in rows]

    def novedades(self, bid=None, limite=300):
        q = """SELECT e.*, p.name, p.link, p.image, p.price, p.list_price, p.available,
                      p.in_range, p.ok, b.name AS brand_name
               FROM events e
               JOIN products p ON p.brand_id=e.brand_id AND p.pid=e.pid
               JOIN brands b ON b.id=e.brand_id"""
        args = ()
        if bid:
            q += " WHERE e.brand_id=?"
            args = (bid,)
        q += " ORDER BY e.seen ASC, e.created_at DESC LIMIT ?"
        with _lock:
            rows = self.con.execute(q, (*args, limite)).fetchall()
        return [dict(r) for r in rows]

    def marcar_vistas(self, bid=None):
        with _lock, self.con:
            if bid:
                self.con.execute("UPDATE events SET seen=1 WHERE brand_id=?", (bid,))
            else:
                self.con.execute("UPDATE events SET seen=1")

    # ---------- revisión de precios
    def revisar(self, bid):
        """Descarga el catálogo de la marca y genera las novedades. Devuelve la lista de novedades nuevas."""
        b = self.marca(bid)
        if not b:
            return []
        plat = stores.PLATAFORMAS[b["platform"]]
        self.estado[bid] = "Revisando…"

        def progreso(n):
            self.estado[bid] = f"Revisando… {n} productos leídos"

        try:
            # Revisión rápida: solo hasta tu precio máximo (la tienda ordena de más barato a
            # más caro). Una vez por día, o si cambiaste algo, se revisa todo completo.
            completa = (b["last_check"] is None or b.get("sin_nuevos")
                        or time.time() - (b.get("last_full") or 0) > REVISION_COMPLETA)
            items = plat.productos(b["base_url"], categorias_a_descargar(b), progreso,
                                   tope=None if completa else tope_descarga(b))
        except stores.StoreError as e:
            with _lock, self.con:
                self.con.execute("UPDATE brands SET last_error=? WHERE id=?", (str(e), bid))
            self.estado.pop(bid, None)
            raise
        except Exception as e:  # noqa: BLE001
            with _lock, self.con:
                self.con.execute("UPDATE brands SET last_error=? WHERE id=?",
                                 (f"Error inesperado: {e}", bid))
            self.estado.pop(bid, None)
            raise
        nuevas = self.aplicar(b, items)
        if completa:
            with _lock, self.con:
                self.con.execute("UPDATE brands SET last_full=? WHERE id=?", (time.time(), bid))
        self.estado.pop(bid, None)
        return nuevas

    def aplicar(self, b, items, ahora=None):
        """Compara lo descargado con lo guardado. Separado de revisar() para poder probarlo.

        - in_range: pasa los filtros con el precio general y está en lo tildado ("En tu rango").
        - ok:       está en una categoría con campanita y pasa los filtros con el precio
                    de esa campanita (o el general si no tiene uno propio).
        """
        ahora = ahora or time.time()
        bid = b["id"]
        primera_vez = b["last_check"] is None
        sin_nuevos = primera_vez or bool(b.get("sin_nuevos"))
        nuevas = []
        with _lock, self.con:
            for it in items:
                cats = it.get("cats") or []
                ver = pasa_filtros(b, it) and se_ve(b, cats)   # se ve en "En tu rango"
                aviso = avisa(b, it)                            # te avisa (precio de su campanita)
                prev = self.con.execute("SELECT * FROM products WHERE brand_id=? AND pid=?",
                                        (bid, it["pid"])).fetchone()
                tipo = None
                viejo_precio = None
                fuera = prev is not None and b["last_check"] and (prev["last_seen"] or 0) < b["last_check"] - 0.001
                if prev is None or (sin_nuevos and fuera):
                    # Producto que no estaba en la revisión anterior. Si cambiaste las categorías,
                    # aparece porque ahora se descarga, no porque sea nuevo: no se avisa.
                    if prev is None and aviso and not sin_nuevos:
                        tipo = NUEVO
                    viejo_precio = prev["price"] if prev is not None else None
                else:
                    viejo_precio = prev["price"]
                    estaba = bool(prev["ok"] if prev["ok"] is not None else prev["in_range"])
                    bajo = it["price"] < (prev["price"] or 0) - 0.5
                    if aviso and not estaba:
                        if not bajo and it["available"] and not prev["available"]:
                            tipo = REPUSO
                        else:
                            tipo = ENTRO
                    elif aviso and estaba and bajo:
                        tipo = BAJO_MAS
                self.con.execute("""
                    INSERT INTO products (brand_id,pid,name,link,image,price,list_price,available,
                                          in_range,first_seen,last_seen,tipo,cats,ok)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(brand_id,pid) DO UPDATE SET
                        name=excluded.name, link=excluded.link, image=excluded.image,
                        price=excluded.price, list_price=excluded.list_price,
                        available=excluded.available, in_range=excluded.in_range,
                        last_seen=excluded.last_seen, tipo=excluded.tipo,
                        cats=excluded.cats, ok=excluded.ok""",
                    (bid, it["pid"], it["name"], it["link"], it["image"], it["price"],
                     it["list_price"], 1 if it["available"] else 0,
                     1 if ver else 0,
                     ahora, ahora, it.get("tipo") or "Otros", json.dumps(cats), 1 if aviso else 0))
                if tipo:
                    self.con.execute("""INSERT INTO events (brand_id,pid,type,old_price,new_price,created_at)
                                        VALUES (?,?,?,?,?,?)""",
                                     (bid, it["pid"], tipo, viejo_precio, it["price"], ahora))
                    nuevas.append({"brand": b["name"], "name": it["name"], "type": tipo,
                                   "old_price": viejo_precio, "price": it["price"], "link": it["link"]})
            # Lo que ya no aparece en la tienda queda como "sin stock / fuera de rango",
            # así si vuelve a aparecer a buen precio se avisa.
            self.con.execute("""UPDATE products SET in_range=0, ok=0, available=0
                                WHERE brand_id=? AND last_seen < ?""", (bid, ahora))
            self.con.execute("UPDATE brands SET last_check=?, last_error=NULL, sin_nuevos=0 WHERE id=?",
                             (ahora, bid))
        return nuevas

    def revisar_todas(self, avisar=None):
        from concurrent.futures import ThreadPoolExecutor

        def una(b):
            try:
                return self.revisar(b["id"])
            except Exception:  # noqa: BLE001 — el error ya quedó guardado en la marca
                return []

        with ThreadPoolExecutor(max_workers=2) as ex:
            todas = [n for lista in ex.map(una, self.marcas()) for n in lista]
        self.set_setting("ultima_revision", time.time())
        if avisar and todas:
            avisar(todas)
        return todas


def _palabras(texto):
    return [w.strip().lower() for w in (texto or "").split(",") if w.strip()]


def _decodificar(d):
    if isinstance(d["categories"], str):
        d["categories"] = json.loads(d["categories"])
    d["tipos_ocultos"] = json.loads(d.get("tipos_ocultos") or "[]")
    av = d.get("avisar")
    d["avisar"] = json.loads(av) if isinstance(av, str) else av
    d["avisar_ids"] = ids_aviso(d)


def _cats(row):
    c = row.get("cats")
    if c is None:
        return None  # producto guardado por una versión vieja: no sabemos su categoría
    return json.loads(c) if isinstance(c, str) else c


def ids_ver(b):
    """Categorías tildadas. "" = toda la tienda."""
    return [c["id"] for c in b["categories"]] or [""]


def ids_aviso(b):
    """Categorías con campanita. Si nunca se tocó una campanita, son las mismas que las tildadas."""
    if b.get("avisar") is None:
        return ids_ver(b)
    return [x["id"] if isinstance(x, dict) else x for x in b["avisar"]]


def cubre(s, c):
    """¿La categoría s incluye a la categoría c?"""
    return s == "" or c == s or (s.endswith("/") and c.startswith(s))


def _en(ids, cats):
    if cats is None:
        return True
    return any(cubre(s, c) for s in ids for c in (cats or [""]))


def se_ve(b, cats):
    return _en(ids_ver(b), cats)


def reglas_aviso(b):
    """[(categoría, mínimo, máximo)] de cada campanita. Sin precio propio usa el general."""
    if b.get("avisar") is None:
        return [(i, b["min_price"] or 0, b["max_price"]) for i in ids_ver(b)]
    out = []
    for x in b["avisar"]:
        if isinstance(x, dict):
            out.append((x["id"], float(x.get("min_price") or b["min_price"] or 0),
                        float(x.get("max_price") or b["max_price"])))
        else:
            out.append((x, b["min_price"] or 0, b["max_price"]))
    return out


def regla_para(b, cats):
    """La campanita más específica que cubre al producto (ej. Hombre › Calzado antes que Hombre)."""
    reglas = reglas_aviso(b)
    if cats is None:
        return max(reglas, key=lambda r: len(r[0])) if reglas else None
    candidatas = [r for r in reglas if any(cubre(r[0], c) for c in (cats or [""]))]
    return max(candidatas, key=lambda r: len(r[0])) if candidatas else None


def avisa(b, it):
    r = regla_para(b, it.get("cats"))
    # Los tipos ocultos solo filtran lo que ves; la campanita avisa de toda su categoría.
    return bool(r) and pasa_filtros(b, it, (r[1], r[2]), con_tipos=False)


def tope_descarga(b):
    """Hasta qué precio hace falta descargar en la revisión rápida."""
    return max([b["max_price"]] + [r[2] for r in reglas_aviso(b)])


def categorias_a_descargar(b):
    """Unión de lo tildado y lo que tiene campanita, sin repetir subcategorías. [] = toda la tienda."""
    ids = set(ids_ver(b)) | set(ids_aviso(b))
    if "" in ids:
        return []
    ids = [i for i in ids if not any(o != i and cubre(o, i) for o in ids)]
    nombres = {c["id"]: c.get("name", c["id"]) for c in b["categories"]}
    for x in b.get("avisar") or []:
        if isinstance(x, dict):
            nombres.setdefault(x["id"], x.get("name", x["id"]))
    return [{"id": i, "name": nombres.get(i, i)} for i in sorted(ids)]


def clave_descarga(b):
    return json.dumps([c["id"] for c in categorias_a_descargar(b)])


def pasa_filtros(b, it, rango=None, con_tipos=True):
    minimo, maximo = rango or (b["min_price"] or 0, b["max_price"])
    if b["only_stock"] and not it["available"]:
        return False
    if con_tipos and (it.get("tipo") or "Otros") in (b.get("tipos_ocultos") or []):
        return False
    if not minimo <= it["price"] <= maximo:
        return False
    nombre = (it.get("name") or "").lower()
    incluir = _palabras(b["include_kw"])
    if incluir and not any(w in nombre for w in incluir):
        return False
    if any(w in nombre for w in _palabras(b["exclude_kw"])):
        return False
    return True


def en_rango(b, it):
    return pasa_filtros(b, it) and se_ve(b, it.get("cats"))
