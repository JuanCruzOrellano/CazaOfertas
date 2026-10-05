"""Avisos al celular con un bot de Telegram (gratis)."""
import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{metodo}"


class TelegramError(Exception):
    pass


def _llamar(token, metodo, datos=None, timeout=15):
    url = API.format(token=token.strip(), metodo=metodo)
    body = urllib.parse.urlencode(datos or {}).encode() if datos else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=timeout) as r:
            j = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            j = json.load(e)
        except Exception:  # noqa: BLE001
            raise TelegramError(f"Telegram respondió error {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise TelegramError(f"No se pudo conectar con Telegram ({e})") from e
    if not j.get("ok"):
        desc = j.get("description", "error desconocido")
        if "Unauthorized" in desc or "Not Found" in desc:
            desc = "El token del bot no es válido. Copialo de nuevo desde @BotFather."
        raise TelegramError(desc)
    return j["result"]


def conectar(token):
    """Busca el chat de la persona que le escribió /start al bot."""
    bot = _llamar(token, "getMe")
    updates = _llamar(token, "getUpdates", {"timeout": 0})
    chats = [u["message"]["chat"] for u in updates if u.get("message")]
    if not chats:
        raise TelegramError(f"Abrí Telegram, buscá a @{bot['username']}, tocá Iniciar (o mandale /start) "
                            "y volvé a tocar Conectar.")
    chat = chats[-1]
    nombre = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
    return {"chat_id": chat["id"], "nombre": nombre, "bot": bot["username"]}


def enviar(cfg, texto, foto=None):
    if not cfg or not cfg.get("token") or not cfg.get("chat_id") or not cfg.get("activo", True):
        return
    datos = {"chat_id": cfg["chat_id"], "parse_mode": "HTML"}
    if foto:
        try:
            return _llamar(cfg["token"], "sendPhoto", {**datos, "photo": foto, "caption": texto[:1000]})
        except TelegramError:
            pass  # si la foto falla, va solo el texto
    return _llamar(cfg["token"], "sendMessage", {**datos, "text": texto[:4000],
                                                 "disable_web_page_preview": "true"})


ETIQUETAS = {
    "nuevo": "🆕 Nuevo en tu rango",
    "entro_rango": "📉 Bajó y entró en tu rango",
    "bajo_mas": "📉 Bajó todavía más",
    "volvio_stock": "✅ Volvió el stock",
    "objetivo": "🎯 Llegó a tu precio objetivo",
}


def _p(n):
    return "$" + f"{n:,.0f}".replace(",", ".") if n else ""


def _esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def texto_novedad(n):
    t = f"<b>{_esc(ETIQUETAS.get(n['type'], 'Novedad'))}</b> · {_esc(n['brand'])}\n{_esc(n['name'])}\n"
    t += f"<b>{_p(n['price'])}</b>"
    if n.get("old_price") and n["old_price"] > n["price"]:
        t += f"  <s>{_p(n['old_price'])}</s>"
    if n.get("talle"):
        t += f"\nTu talle: {_esc(n['talle'])}"
    c = n.get("cuotas")
    if c and c.get("n", 0) > 1:
        t += f"\n{c['n']} cuotas sin interés de {_p(c.get('valor'))}"
    if n.get("link"):
        t += f"\n<a href=\"{_esc(n['link'])}\">Ver en la tienda</a>"
    return t


def avisar_novedades(cfg, novedades):
    if not cfg or not cfg.get("chat_id"):
        return
    try:
        for n in novedades[:10]:
            enviar(cfg, texto_novedad(n), n.get("image"))
        if len(novedades) > 10:
            enviar(cfg, f"…y {len(novedades) - 10} novedades más. Abrí CazaOfertas para verlas.")
    except TelegramError:
        pass
