"""Base de datos y lógica de detección: qué entra en el rango, qué bajó de precio."""
import json
import os
import sqlite3
import threading
import time

import stores
import talles as T

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
CREATE TABLE IF NOT EXISTS precios (
    tienda TEXT NOT NULL, pid TEXT NOT NULL, ts REAL NOT NULL,
    price REAL, list_price REAL, available INTEGER
);
CREATE INDEX IF NOT EXISTS precios_idx ON precios (tienda, pid, ts);
CREATE TABLE IF NOT EXISTS seguidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL, base_url TEXT NOT NULL, pid TEXT NOT NULL, link TEXT,
    name TEXT, image TEXT, tipo TEXT, ref TEXT,
    objetivo REAL, solo_mi_talle INTEGER NOT NULL DEFAULT 1,
    price REAL, list_price REAL, available INTEGER, precio_ef REAL, disp_ef INTEGER,
    talles TEXT, cuotas TEXT, cats_nombres TEXT, alcanzado INTEGER NOT NULL DEFAULT 0,
    created_at REAL, last_check REAL, last_error TEXT
);
"""

REVISION_COMPLETA = 20 * 3600  # cada cuánto se descarga todo (no solo hasta tu precio máximo)

# Tipos de novedad
NUEVO = "nuevo"              # producto nuevo en la tienda que ya entra en tu rango
ENTRO = "entro_rango"        # estaba más caro y bajó hasta entrar en tu rango
BAJO_MAS = "bajo_mas"        # ya estaba en tu rango y bajó todavía más
REPUSO = "volvio_stock"      # estaba en rango pero sin stock (o sin tu talle), y volvió
OBJETIVO = "objetivo"        # un producto seguido llegó a tu precio objetivo


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
                             ("brands", "last_full", "REAL"),
                             ("brands", "solo_mi_talle", "INTEGER NOT NULL DEFAULT 1"),
                             ("products", "talles", "TEXT"),
                             ("products", "ref", "TEXT"),
                             ("products", "cuotas", "TEXT"),
                             ("products", "cats_nombres", "TEXT"),
                             ("products", "precio_ef", "REAL"),
                             ("products", "disp_ef", "INTEGER"),
                             ("events", "seguido_id", "INTEGER")):
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
            "solo_mi_talle": 1 if datos.get("solo_mi_talle", True) else 0,
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

    def cfg_talles(self):
        return T.preparar(self.get_setting("talles", {}))

    def _recalcular(self, bid):
        b = self.marca(bid)
        cfg = self.cfg_talles()
        rows = self.con.execute("SELECT * FROM products WHERE brand_id=?", (bid,)).fetchall()
        for r in rows:
            d = _fila_a_item(dict(r))
            x = efectivo(b, d, cfg)
            sigue = not (b["last_check"] and (d["last_seen"] or 0) < b["last_check"] - 0.001)
            ver = sigue and pasa_filtros(b, x) and se_ve(b, d["cats"])
            aviso = sigue and avisa(b, x)
            self.con.execute("""UPDATE products SET ok=?, in_range=?, precio_ef=?, disp_ef=?
                                WHERE brand_id=? AND pid=?""",
                             (1 if aviso else 0, 1 if ver else 0, x["price"], 1 if x["available"] else 0,
                              bid, r["pid"]))

    def recalcular_todo(self):
        """Después de cambiar tus talles: se recalcula todo sin generar avisos."""
        with _lock, self.con:
            for b in self.marcas():
                self._recalcular(b["id"])

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
                ORDER BY COALESCE(precio_ef, price) ASC""", (bid, (b["last_check"] or 0) - 0.001)).fetchall()
        return [self._para_ui(dict(r), b["base_url"]) for r in rows]

    def novedades(self, bid=None, limite=300):
        q = """SELECT e.*, p.name, p.link, p.image, p.price, p.list_price, p.available,
                      p.in_range, p.ok, p.precio_ef, p.disp_ef, p.talles, p.cuotas, p.tipo, p.ref,
                      p.cats_nombres, b.name AS brand_name, b.base_url
               FROM events e
               JOIN products p ON p.brand_id=e.brand_id AND p.pid=e.pid
               JOIN brands b ON b.id=e.brand_id"""
        args = ()
        if bid:
            q += " WHERE e.brand_id=?"
            args = (bid,)
        q += " ORDER BY e.seen ASC, e.created_at DESC LIMIT ?"
        with _lock:
            rows = [dict(r) for r in self.con.execute(q, (*args, limite)).fetchall()]
            if not bid:
                rows += [dict(r) for r in self.con.execute("""
                    SELECT e.*, s.name, s.link, s.image, s.price, s.list_price, s.available,
                           1 AS in_range, 1 AS ok, s.precio_ef, s.disp_ef, s.talles, s.cuotas, s.tipo, s.ref,
                           s.cats_nombres, 'Seguidos' AS brand_name, s.base_url
                    FROM events e JOIN seguidos s ON s.id=e.seguido_id
                    WHERE e.seguido_id IS NOT NULL
                    ORDER BY e.seen ASC, e.created_at DESC LIMIT ?""", (limite,)).fetchall()]
        rows.sort(key=lambda r: (r["seen"], -r["created_at"]))
        return [self._para_ui(r, r["base_url"]) for r in rows[:limite]]

    def _para_ui(self, d, tienda):
        """Agrega lo que la interfaz necesita: talles, cuotas e historial resumido."""
        for k in ("talles", "cuotas", "cats_nombres"):
            v = d.get(k)
            d[k] = json.loads(v) if isinstance(v, str) and v else (v or None)
        d["tienda"] = tienda
        cfg = self.cfg_talles()
        d["mi_talle"] = T.mi_talle({"talles": d.get("talles"), "tipo": d.get("tipo"), "name": d.get("name"),
                                    "cats_nombres": d.get("cats_nombres")}, cfg)
        d["veredicto"] = self.veredicto(tienda, d["pid"], d.get("precio_ef") or d.get("price"))
        return d

    # ---------- historial de precios
    def registrar_precio(self, tienda, pid, price, list_price, available, ahora):
        """Guarda un punto solo si cambió algo desde el último (así la base no crece de más)."""
        u = self.con.execute("""SELECT price, list_price, available FROM precios
                                WHERE tienda=? AND pid=? ORDER BY ts DESC LIMIT 1""", (tienda, pid)).fetchone()
        if u and abs((u["price"] or 0) - price) < 0.5 and abs((u["list_price"] or 0) - list_price) < 0.5 \
                and bool(u["available"]) == bool(available):
            return
        self.con.execute("INSERT INTO precios VALUES (?,?,?,?,?,?)",
                         (tienda, pid, ahora, price, list_price, 1 if available else 0))

    def historial(self, tienda, pid):
        with _lock:
            rows = self.con.execute("""SELECT ts, price, list_price, available FROM precios
                                       WHERE tienda=? AND pid=? ORDER BY ts""", (tienda, pid)).fetchall()
        return [dict(r) for r in rows]

    def veredicto(self, tienda, pid, precio):
        """¿Es una oferta de verdad? Mira el historial del producto."""
        return analizar_historial(self.historial(tienda, pid), precio)

    def marcar_vistas(self, bid=None):
        with _lock, self.con:
            if bid == "seguidos":
                self.con.execute("UPDATE events SET seen=1 WHERE seguido_id IS NOT NULL")
            elif bid:
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
        Si la marca tiene "solo mi talle", precio y stock son los de TU talle.
        """
        ahora = ahora or time.time()
        bid = b["id"]
        primera_vez = b["last_check"] is None
        sin_nuevos = primera_vez or bool(b.get("sin_nuevos"))
        cfg = self.cfg_talles()
        nuevas = []
        with _lock, self.con:
            for it in items:
                cats = it.get("cats") or []
                x = efectivo(b, it, cfg)
                ver = pasa_filtros(b, x) and se_ve(b, cats)
                aviso = avisa(b, x)
                prev = self.con.execute("SELECT * FROM products WHERE brand_id=? AND pid=?",
                                        (bid, it["pid"])).fetchone()
                tipo, viejo_precio = None, None
                fuera = prev is not None and b["last_check"] and (prev["last_seen"] or 0) < b["last_check"] - 0.001
                if prev is None or (sin_nuevos and fuera):
                    if prev is None and aviso and not sin_nuevos:
                        tipo = NUEVO
                    viejo_precio = prev["price"] if prev is not None else None
                else:
                    viejo_precio = prev["precio_ef"] if prev["precio_ef"] is not None else prev["price"]
                    prev_disp = prev["disp_ef"] if prev["disp_ef"] is not None else prev["available"]
                    estaba = bool(prev["ok"] if prev["ok"] is not None else prev["in_range"])
                    bajo = x["price"] < (viejo_precio or 0) - 0.5
                    if aviso and not estaba:
                        tipo = REPUSO if (not bajo and x["available"] and not prev_disp) else ENTRO
                    elif aviso and estaba and bajo:
                        tipo = BAJO_MAS
                self.con.execute("""
                    INSERT INTO products (brand_id,pid,name,link,image,price,list_price,available,
                                          in_range,first_seen,last_seen,tipo,cats,ok,talles,ref,cuotas,
                                          cats_nombres,precio_ef,disp_ef)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(brand_id,pid) DO UPDATE SET
                        name=excluded.name, link=excluded.link, image=excluded.image,
                        price=excluded.price, list_price=excluded.list_price,
                        available=excluded.available, in_range=excluded.in_range,
                        last_seen=excluded.last_seen, tipo=excluded.tipo,
                        cats=excluded.cats, ok=excluded.ok, talles=excluded.talles, ref=excluded.ref,
                        cuotas=excluded.cuotas, cats_nombres=excluded.cats_nombres,
                        precio_ef=excluded.precio_ef, disp_ef=excluded.disp_ef""",
                    (bid, it["pid"], it["name"], it["link"], it["image"], it["price"],
                     it["list_price"], 1 if it["available"] else 0, 1 if ver else 0,
                     ahora, ahora, it.get("tipo") or "Otros", json.dumps(cats), 1 if aviso else 0,
                     json.dumps(it.get("talles") or []), it.get("ref") or "",
                     json.dumps(it.get("cuotas")) if it.get("cuotas") else None,
                     json.dumps(it.get("cats_nombres") or []), x["price"], 1 if x["available"] else 0))
                self.registrar_precio(b["base_url"], it["pid"], it["price"], it["list_price"],
                                      it["available"], ahora)
                if tipo:
                    self.con.execute("""INSERT INTO events (brand_id,pid,type,old_price,new_price,created_at)
                                        VALUES (?,?,?,?,?,?)""",
                                     (bid, it["pid"], tipo, viejo_precio, x["price"], ahora))
                    nuevas.append(_novedad(b["name"], it, x, tipo, viejo_precio))
            # Lo que ya no aparece en la tienda queda como "sin stock / fuera de rango",
            # así si vuelve a aparecer a buen precio se avisa.
            self.con.execute("""UPDATE products SET in_range=0, ok=0, available=0, disp_ef=0
                                WHERE brand_id=? AND last_seen < ?""", (bid, ahora))
            self.con.execute("UPDATE brands SET last_check=?, last_error=NULL, sin_nuevos=0 WHERE id=?",
                             (ahora, bid))
        return nuevas

    # ---------- productos seguidos
    def seguidos(self):
        with _lock:
            rows = [dict(r) for r in self.con.execute("SELECT * FROM seguidos ORDER BY created_at DESC")]
        for r in rows:
            r["sin_ver"] = self.con.execute("SELECT COUNT(*) FROM events WHERE seguido_id=? AND seen=0",
                                            (r["id"],)).fetchone()[0]
        return [self._para_ui(r, r["base_url"]) for r in rows]

    def seguir(self, platform, base_url, it, objetivo=None, solo_mi_talle=True):
        with _lock, self.con:
            ya = self.con.execute("SELECT id FROM seguidos WHERE base_url=? AND pid=?",
                                  (base_url, it["pid"])).fetchone()
            if ya:
                self.con.execute("UPDATE seguidos SET objetivo=?, solo_mi_talle=?, alcanzado=0 WHERE id=?",
                                 (objetivo, 1 if solo_mi_talle else 0, ya["id"]))
                return ya["id"]
            cur = self.con.execute("""INSERT INTO seguidos (platform, base_url, pid, link, name, image, tipo,
                                      ref, objetivo, solo_mi_talle, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                                   (platform, base_url, it["pid"], it.get("link"), it.get("name"), it.get("image"),
                                    it.get("tipo"), it.get("ref"), objetivo, 1 if solo_mi_talle else 0, time.time()))
            sid = cur.lastrowid
        self.actualizar_seguido(sid, it, avisar=False)
        return sid

    def dejar_de_seguir(self, sid):
        with _lock, self.con:
            self.con.execute("DELETE FROM events WHERE seguido_id=?", (sid,))
            self.con.execute("DELETE FROM seguidos WHERE id=?", (sid,))

    def seguido(self, base_url, pid):
        with _lock:
            r = self.con.execute("SELECT * FROM seguidos WHERE base_url=? AND pid=?", (base_url, pid)).fetchone()
        return dict(r) if r else None

    def actualizar_seguido(self, sid, it, avisar=True, ahora=None):
        ahora = ahora or time.time()
        with _lock:
            s = dict(self.con.execute("SELECT * FROM seguidos WHERE id=?", (sid,)).fetchone())
        cfg = self.cfg_talles()
        falso = {"only_stock": 1, "solo_mi_talle": s["solo_mi_talle"]}
        x = efectivo(falso, it, cfg)
        tipo, viejo = None, s["precio_ef"]
        objetivo = s["objetivo"]
        llego = bool(x["available"] and objetivo and x["price"] <= objetivo)
        if avisar and s["last_check"]:
            if llego and not s["alcanzado"]:
                tipo = OBJETIVO
            elif x["available"] and viejo and x["price"] < viejo - 0.5:
                tipo = BAJO_MAS
            elif x["available"] and not s["disp_ef"]:
                tipo = REPUSO
        with _lock, self.con:
            self.con.execute("""UPDATE seguidos SET name=?, link=?, image=?, tipo=?, ref=?, price=?, list_price=?,
                                available=?, precio_ef=?, disp_ef=?, talles=?, cuotas=?, cats_nombres=?,
                                alcanzado=?, last_check=?, last_error=NULL WHERE id=?""",
                             (it["name"], it.get("link") or s["link"], it.get("image"), it.get("tipo"),
                              it.get("ref"), it["price"], it["list_price"], 1 if it["available"] else 0,
                              x["price"], 1 if x["available"] else 0, json.dumps(it.get("talles") or []),
                              json.dumps(it.get("cuotas")) if it.get("cuotas") else None,
                              json.dumps(it.get("cats_nombres") or []), 1 if llego else 0, ahora, sid))
            self.registrar_precio(s["base_url"], s["pid"], it["price"], it["list_price"], it["available"], ahora)
            if tipo:
                self.con.execute("""INSERT INTO events (brand_id,pid,type,old_price,new_price,created_at,seguido_id)
                                    VALUES (0,?,?,?,?,?,?)""", (s["pid"], tipo, viejo, x["price"], ahora, sid))
                return [_novedad("Seguidos", it, x, tipo, viejo)]
        return []

    def revisar_seguidos(self):
        nuevas = []
        for s in self.seguidos():
            plat = stores.PLATAFORMAS.get(s["platform"])
            try:
                it = plat.por_id(s["base_url"], s["pid"], s["link"]) if s["platform"] == "shopify" \
                    else plat.por_id(s["base_url"], s["pid"])
                if not it:
                    raise stores.StoreError("La tienda ya no tiene este producto")
                nuevas += self.actualizar_seguido(s["id"], it)
            except Exception as e:  # noqa: BLE001
                with _lock, self.con:
                    self.con.execute("UPDATE seguidos SET last_error=? WHERE id=?", (str(e), s["id"]))
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
        todas += self.revisar_seguidos()
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


def _fila_a_item(d):
    d["cats"] = _cats(d)
    d["available"] = bool(d["available"])
    for k in ("talles", "cats_nombres"):
        v = d.get(k)
        d[k] = json.loads(v) if isinstance(v, str) and v else (v or [])
    return d


def efectivo(b, it, cfg):
    """Precio y stock que importan: si la marca pide "solo mi talle", los de tu talle."""
    x = dict(it)
    if b.get("solo_mi_talle", 1):
        mt = T.mi_talle(it, cfg)
        if mt is not None:
            x["available"] = mt["disponible"]
            if mt["precio"]:
                x["price"] = mt["precio"]
            x["mi_talle"] = mt
    return x


def _novedad(marca, it, x, tipo, viejo):
    mt = x.get("mi_talle")
    return {"brand": marca, "name": it["name"], "type": tipo, "old_price": viejo, "price": x["price"],
            "link": it.get("link"), "image": it.get("image"), "list_price": it.get("list_price"),
            "talle": ", ".join(mt["talles"]) if mt and mt.get("talles") else None,
            "cuotas": it.get("cuotas")}


DIA = 86400


def analizar_historial(hist, precio):
    """Veredicto sobre el precio actual mirando el historial.

    Devuelve {"tipo": minimo|bueno|ya_estuvo|inflado|nuevo, "texto": ..., "min", "max", "desde"}.
    """
    if not hist or not precio:
        return None
    ahora = hist[-1]["ts"]
    desde = hist[0]["ts"]
    dias = (ahora - desde) / DIA
    previos = [h for h in hist[:-1] if h["price"]]
    out = {"min": min(h["price"] for h in hist), "max": max(h["price"] for h in hist),
           "desde": desde, "puntos": len(hist)}
    if not previos or dias < 2:
        out.update(tipo="nuevo", texto="Todavía no hay historial: la app empezó a seguirlo hace poco.")
        return out
    min_prev = min(h["price"] for h in previos)
    max_prev = max(h["price"] for h in previos)
    lista = hist[-1]["list_price"] or precio
    # Descuento inflado: el precio "tachado" nunca se cobró en todo el historial
    if lista > precio * 1.05 and lista > max_prev * 1.03 and dias >= 14:
        out.update(tipo="inflado", texto=f"El precio tachado ({_p(lista)}) nunca se cobró en "
                                         f"{int(dias)} días: el descuento real es menor.")
        return out
    if precio <= min_prev - 0.5:
        out.update(tipo="minimo", texto=f"Precio más bajo desde que lo seguimos ({int(dias)} días).")
        return out
    # ¿Ya estuvo a este precio o menos hace poco? (subieron y "bajaron")
    recientes = [h for h in previos if ahora - h["ts"] <= 60 * DIA and h["price"] <= precio + 0.5]
    if recientes:
        h = min(recientes, key=lambda h: h["price"])
        hace = max(1, int((ahora - h["ts"]) / DIA))
        if h["price"] < precio - 0.5:
            out.update(tipo="ya_estuvo", texto=f"Hace {hace} días estuvo más barato: {_p(h['price'])}.")
        else:
            out.update(tipo="ya_estuvo", texto=f"No es nuevo: hace {hace} días ya estaba a este precio.")
        return out
    if precio <= min_prev + (max_prev - min_prev) * 0.25:
        out.update(tipo="bueno", texto=f"Buen precio: cerca del mínimo que tuvo ({_p(min_prev)}).")
        return out
    out.update(tipo="normal", texto=f"Precio habitual. El más bajo que tuvo: {_p(min_prev)}.")
    return out


def _p(n):
    return "$" + f"{n:,.0f}".replace(",", ".")
