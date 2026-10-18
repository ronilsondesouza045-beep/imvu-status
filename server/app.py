# -*- coding: utf-8 -*-
"""
IMVU Status - backend do site + do "presente gratis".

Serve tambem a versao web (pasta web/) em "/", com as mesmas funcoes do app:
consulta de nick (online/offline, sala, card do avatar), diretorio de salas,
conta (Firebase) e resgate do presente.

Fica logado com a conta do DONO (credenciais apenas em variaveis de ambiente
do servidor; nada disso vai para o app/APK) e envia presentes usando os
mesmos endpoints que o site do IMVU usa (api.imvu.com).

Controles:
  - 100 creditos a cada 24 horas por avatar (guardado por CID: trocar de nick nao burla)
  - 1 aparelho = 1 avatar para sempre (o app manda o ID do aparelho, que nao
    muda com VPN, 4G ou reinstalacao do app)
  - contas muito novas (menos de GIFT_IDADE_MINIMA_DIAS) nao recebem
  - limite de avatares diferentes por IP dentro da janela
  - teto de envios por dia (protege seus creditos de abuso)
  - checa o saldo da conta antes de enviar
  - limitador simples por IP

Variaveis de ambiente:
  IMVU_USER           usuario da conta do dono
  IMVU_PASS           senha da conta do dono
  IMVU_SESSAO         (opcional) sessao pronta gerada por server/login_local.py:
                      cole aqui o texto e o servidor ja acorda logado,
                      sem precisar de senha nem 2FA no servidor
  PRESENTE_CREDITOS       creditos por avatar em cada resgate (padrao 100)
  GIFT_JANELA_HORAS       horas entre dois resgates do mesmo avatar (padrao 24)
  GIFT_IDADE_MINIMA_DIAS  idade minima da conta que recebe, em dias (padrao 7)
  GIFT_LIMITE_IP_AVATARES avatares diferentes por IP na janela (padrao 3)
  GIFT_LIMITE_DIARIO      envios por dia no total (padrao 10)
  GIFT_MENSAGEM       mensagem padrao junto do presente (opcional)
  KEEPALIVE_MINUTOS   de quantos em quantos minutos o servidor "acorda" a
                      sessao do IMVU para ela nao expirar por inatividade
                      (padrao 5; 0 desliga)
  FIREBASE_KEY        Web API Key do projeto Firebase (login por email no app).
                      Sem ela, o login nao e exigido (modo antigo).
  EXIGIR_LOGIN        "1" (padrao) exige conta logada com email confirmado;
                      "0" volta ao modo antigo (so aparelho)
  ADMIN_TOKEN         token para /admin/2fa e /admin/status
  DATA_DIR            pasta dos dados (padrao: server/dados)
  PORT                porta (padrao 8000)

NUNCA escreva credenciais no codigo nem suba a pasta de dados para o GitHub.
"""

import ast
import base64
import datetime
import hashlib
import http.cookiejar
import json
import mimetypes
import os
import re
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = "https://api.imvu.com"
UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36")

WEB_VERSAO = "2.0.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAIZ_DIR = os.path.dirname(BASE_DIR)
WEB_DIR = os.path.join(RAIZ_DIR, "web")
ASSETS_DIR = os.path.join(RAIZ_DIR, "assets")
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "dados")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")
ENV_HASH_FILE = os.path.join(DATA_DIR, "sessao_env.hash")
DB_FILE = os.path.join(DATA_DIR, "gifts.db")

IMVU_USER = (os.environ.get("IMVU_USER") or "").strip()
IMVU_PASS = os.environ.get("IMVU_PASS") or ""
GIFT_CREDITOS = max(100, int(os.environ.get("PRESENTE_CREDITOS") or 100))
GIFT_JANELA_HORAS = max(1, int(os.environ.get("GIFT_JANELA_HORAS") or 24))
GIFT_IDADE_MINIMA_DIAS = max(0, int(os.environ.get("GIFT_IDADE_MINIMA_DIAS") or 7))
GIFT_LIMITE_IP_AVATARES = max(1, int(os.environ.get("GIFT_LIMITE_IP_AVATARES") or 3))
GIFT_LIMITE_DIARIO = max(1, int(os.environ.get("GIFT_LIMITE_DIARIO") or 10))
GIFT_MENSAGEM = (os.environ.get("GIFT_MENSAGEM") or "").strip()
KEEPALIVE_MINUTOS = max(0, int(os.environ.get("KEEPALIVE_MINUTOS") or 5))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or ""
FIREBASE_KEY = (os.environ.get("FIREBASE_KEY") or "").strip()
EXIGIR_LOGIN = (os.environ.get("EXIGIR_LOGIN", "1") or "1").strip().lower() not in ("0", "false", "no")
FB_LOOKUP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:lookup?key="
PORT = int(os.environ.get("PORT") or 8000)

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

_DB_LOCK = threading.Lock()
_RATE_LOCK = threading.Lock()
_RATE = {}


class ErroAPI(Exception):
    pass


def _log(msg):
    hora = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{hora}] {msg}", flush=True)


def criar_pastas():
    os.makedirs(DATA_DIR, exist_ok=True)


def init_db():
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS presentes ("
                " cid TEXT NOT NULL, nick TEXT, quando TEXT NOT NULL, txn TEXT NOT NULL,"
                " PRIMARY KEY (cid, txn))"
            )
            colunas = {l[1] for l in con.execute("PRAGMA table_info(presentes)").fetchall()}
            if "ip" not in colunas:
                con.execute("ALTER TABLE presentes ADD COLUMN ip TEXT")
            con.execute(
                "CREATE TABLE IF NOT EXISTS aparelhos ("
                " aparelho TEXT PRIMARY KEY, cid TEXT NOT NULL, nick TEXT, criado TEXT NOT NULL)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS contas ("
                " uid TEXT PRIMARY KEY, email TEXT, cid TEXT NOT NULL, nick TEXT, criado TEXT NOT NULL)"
            )
            con.commit()
        finally:
            con.close()


def _agora_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def _limite_janela_str():
    corte = _agora_utc() - datetime.timedelta(hours=GIFT_JANELA_HORAS)
    return corte.strftime("%Y-%m-%d %H:%M:%S")


def ultimo_presente(cid):
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute("SELECT MAX(quando) FROM presentes WHERE cid = ?", (cid,))
            linha = cur.fetchone()
            texto = linha[0] if linha and linha[0] else None
        finally:
            con.close()
    if not texto:
        return None
    try:
        return datetime.datetime.strptime(texto, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def ja_recebeu_janela(cid):
    ultimo = ultimo_presente(cid)
    if not ultimo:
        return None
    if _agora_utc() - ultimo < datetime.timedelta(hours=GIFT_JANELA_HORAS):
        return ultimo
    return None


def texto_volta(ultimo):
    falta = ultimo + datetime.timedelta(hours=GIFT_JANELA_HORAS) - _agora_utc()
    seg = max(0, int(falta.total_seconds()))
    horas, resto = divmod(seg, 3600)
    minutos = resto // 60
    if horas:
        return f"em {horas}h{minutos:02d}"
    return f"em {minutos} min"


def idade_conta_dias(data):
    try:
        seg = int(data.get("registered"))
    except Exception:
        seg = None
    if seg:
        return int((time.time() - seg) / 86400)
    criado = str(data.get("created") or "")
    try:
        dt = datetime.datetime.fromisoformat(criado.replace("Z", "+00:00"))
        return (_agora_utc() - dt).days
    except Exception:
        return None


def presentes_do_ip(ip):
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute(
                "SELECT COUNT(DISTINCT cid) FROM presentes WHERE ip = ? AND quando >= ?",
                (ip, _limite_janela_str()),
            )
            return int(cur.fetchone()[0] or 0)
        finally:
            con.close()


def presentes_hoje():
    hoje = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute("SELECT COUNT(*) FROM presentes WHERE quando LIKE ?", (hoje + "%",))
            return int(cur.fetchone()[0] or 0)
        finally:
            con.close()


def registrar_presente(cid, nick, txn, ip=None):
    quando = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            con.execute(
                "INSERT OR IGNORE INTO presentes (cid, nick, quando, txn, ip)"
                " VALUES (?, ?, ?, ?, ?)",
                (cid, nick, quando, txn, ip),
            )
            con.commit()
        finally:
            con.close()


def cid_do_aparelho(aparelho):
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute("SELECT cid FROM aparelhos WHERE aparelho = ?", (aparelho,))
            linha = cur.fetchone()
            return linha[0] if linha else None
        finally:
            con.close()


def registrar_aparelho(aparelho, cid, nick):
    quando = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            con.execute(
                "INSERT OR IGNORE INTO aparelhos (aparelho, cid, nick, criado)"
                " VALUES (?, ?, ?, ?)",
                (aparelho, str(cid), nick, quando),
            )
            con.commit()
        finally:
            con.close()


def aparelhos_ligados():
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute("SELECT COUNT(*) FROM aparelhos")
            return int(cur.fetchone()[0] or 0)
        finally:
            con.close()


def conta_do_uid(uid):
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute("SELECT uid, email, cid, nick FROM contas WHERE uid = ?", (uid,))
            linha = cur.fetchone()
            return dict(linha) if linha else None
        finally:
            con.close()


def conta_do_cid(cid):
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute("SELECT uid, email, cid, nick FROM contas WHERE cid = ?", (str(cid),))
            linha = cur.fetchone()
            return dict(linha) if linha else None
        finally:
            con.close()


def registrar_conta(uid, email, cid, nick):
    quando = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            con.execute(
                "INSERT OR IGNORE INTO contas (uid, email, cid, nick, criado)"
                " VALUES (?, ?, ?, ?, ?)",
                (uid, email, str(cid), nick, quando),
            )
            con.commit()
        finally:
            con.close()


def contas_ligadas():
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute("SELECT COUNT(*) FROM contas")
            return int(cur.fetchone()[0] or 0)
        finally:
            con.close()


_CACHE_TOKEN = {}


def validar_conta(id_token):
    """Confere o token do Firebase e devolve {uid, email, verificado} ou None."""
    if not FIREBASE_KEY or not id_token:
        return None
    agora = time.time()
    guardado = _CACHE_TOKEN.get(id_token)
    if guardado and guardado[0] > agora:
        return guardado[1]
    try:
        corpo = json.dumps({"idToken": id_token}).encode("utf-8")
        req = urllib.request.Request(
            FB_LOOKUP_URL + FIREBASE_KEY, data=corpo, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
            js = json.loads(resp.read().decode("utf-8", errors="replace"))
        usuarios = js.get("users") or []
        if not usuarios:
            return None
        u = usuarios[0]
        info = {
            "uid": u.get("localId") or "",
            "email": u.get("email") or "",
            "verificado": bool(u.get("emailVerified")),
        }
        if not info["uid"]:
            return None
        _CACHE_TOKEN[id_token] = (agora + 600, info)
        if len(_CACHE_TOKEN) > 500:
            corte = agora - 60
            for t in list(_CACHE_TOKEN):
                if _CACHE_TOKEN[t][0] < corte:
                    del _CACHE_TOKEN[t]
        return info
    except Exception:
        return None


def materializar_sessao_env():
    valor = (os.environ.get("IMVU_SESSAO") or "").strip()
    if not valor:
        return False
    texto = valor
    try:
        decodificado = base64.b64decode(valor).decode("utf-8")
        json.loads(decodificado)
        texto = decodificado
    except Exception:
        pass
    try:
        dados = json.loads(texto)
    except Exception:
        _log("AVISO: IMVU_SESSAO invalido; ignorando")
        return False
    marca = hashlib.sha256(valor.encode("utf-8")).hexdigest()
    try:
        with open(ENV_HASH_FILE, "r", encoding="utf-8") as f:
            marca_anterior = (f.read() or "").strip()
    except Exception:
        marca_anterior = ""
    if marca_anterior == marca and os.path.exists(SESSION_FILE):
        _log("IMVU_SESSAO sem mudanca; mantendo a sessao mais nova salva no servidor")
        return False
    try:
        criar_pastas()
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f)
        with open(ENV_HASH_FILE, "w", encoding="utf-8") as f:
            f.write(marca)
        try:
            os.chmod(SESSION_FILE, 0o600)
        except Exception:
            pass
        _log("sessao carregada de IMVU_SESSAO")
        return True
    except Exception as e:
        _log(f"AVISO: nao consegui gravar a sessao de IMVU_SESSAO: {e}")
        return False


def _extrair_cid(valor):
    if valor is None:
        return None
    texto = str(valor).strip()
    m = re.search(r"user-(\d+)", texto)
    if m:
        return m.group(1)
    if texto.isdigit() and len(texto) >= 3:
        return texto
    return None


def rate_ok(ip, maximo=20, janela=60):
    agora = time.time()
    with _RATE_LOCK:
        marcas = [t for t in _RATE.get(ip, []) if agora - t < janela]
        if len(marcas) >= maximo:
            _RATE[ip] = marcas
            return False
        marcas.append(agora)
        _RATE[ip] = marcas
    return True


# ---------------------------------------------------------------------------
# Consulta publica (site): fala com o IMVU SEM a conta do dono (sessao anonima,
# sem cookies) para nao gastar/bloquear a conta que paga os presentes.
# ---------------------------------------------------------------------------

_CACHE_PUB = {}
_CACHE_PUB_LOCK = threading.Lock()
_CACHE_PUB_TTL = 15


def _cache_pegar(chave):
    agora = time.time()
    with _CACHE_PUB_LOCK:
        item = _CACHE_PUB.get(chave)
        if item and item[0] > agora:
            return item[1]
        if item:
            _CACHE_PUB.pop(chave, None)
    return None


def _cache_guardar(chave, valor):
    with _CACHE_PUB_LOCK:
        _CACHE_PUB[chave] = (time.time() + _CACHE_PUB_TTL, valor)
        if len(_CACHE_PUB) > 800:
            agora = time.time()
            for k in list(_CACHE_PUB):
                if _CACHE_PUB[k][0] < agora:
                    _CACHE_PUB.pop(k, None)


def _req_publico(url, metodo="GET", corpo=None, texto_puro=False):
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=dados, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as resp:
            texto = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            texto = e.read().decode("utf-8", errors="replace")
        except Exception:
            raise ErroAPI(f"HTTP {e.code}")
    except Exception as e:
        raise ErroAPI(f"falha de rede: {e}")
    if "too many requests" in texto.lower():
        raise ErroAPI("IMVU bloqueou temporariamente (muitas requisicoes).")
    if texto_puro:
        return texto
    try:
        js = json.loads(texto)
    except Exception:
        raise ErroAPI("resposta inesperada do IMVU")
    if isinstance(js, dict) and js.get("status") == "failure":
        raise ErroAPI(f"{js.get('error', '?')}: {js.get('message', '?')}")
    return js


def _resolver_nick_publico(nick):
    url = f"{API}/user?" + urllib.parse.urlencode({"username": nick})
    js = _req_publico(url)
    raiz = js.get("denormalized", {})
    colecao = raiz.get(js.get("id"), {})
    itens = (colecao.get("data") or {}).get("items") or []
    if not itens:
        return None
    node_url = itens[0]
    node = raiz.get(node_url, {})
    m = re.search(r"user-(\d+)$", node_url)
    return {
        "cid": m.group(1) if m else "?",
        "data": node.get("data", {}),
        "rel": node.get("relations", {}),
    }


def _checar_publico(nick):
    u = _resolver_nick_publico(nick)
    if not u:
        return None
    d = u["data"]
    presenca = {}
    try:
        purl = u["rel"].get("presence") or f"{API}/presence/presence-{u['cid']}"
        pjs = _req_publico(purl)
        pnode = (pjs.get("denormalized") or {}).get(purl) or {}
        presenca = pnode.get("data", {})
    except ErroAPI:
        pass
    online = bool(presenca.get("online", d.get("online", False)))
    sala = None
    if "current_room" in u["rel"]:
        try:
            rjs = _req_publico(u["rel"]["current_room"])
            rnode = (rjs.get("denormalized") or {}).get(u["rel"]["current_room"]) or {}
            dados_sala = rnode.get("data") or {}
            if dados_sala:
                sala = {
                    "nome": str(dados_sala.get("name") or "?"),
                    "privacidade": str(dados_sala.get("privacy") or "?").lower(),
                }
        except ErroAPI:
            sala = None
    perfil = {}
    try:
        if "profile" in u["rel"]:
            pjs = _req_publico(u["rel"]["profile"])
            pnode = (pjs.get("denormalized") or {}).get(u["rel"]["profile"]) or {}
            perfil = pnode.get("data", {})
    except ErroAPI:
        pass
    look = None
    try:
        if "profile_outfit" in u["rel"]:
            ojs = _req_publico(u["rel"]["profile_outfit"])
            onode = (ojs.get("denormalized") or {}).get(u["rel"]["profile_outfit"]) or {}
            odata = onode.get("data", {})
            prods = [p.get("product_id") for p in (odata.get("products") or [])
                     if isinstance(p, dict) and p.get("product_id")]
            if prods:
                look = {"total": len(prods), "corpo": None}
                purl = f"{API}/product/product-{prods[0]}"
                try:
                    prjs = _req_publico(purl)
                    prnode = (prjs.get("denormalized") or {}).get(purl) or {}
                    look["corpo"] = (prnode.get("data") or {}).get("product_name")
                except ErroAPI:
                    pass
    except ErroAPI:
        pass
    marcas = []
    if d.get("is_vip"):
        marcas.append("VIP")
    if d.get("is_ap") or d.get("is_ap_plus"):
        marcas.append("AccessPass")
    if d.get("is_creator"):
        marcas.append("Criador")
    foto = perfil.get("image") or d.get("thumbnail_url") or None
    if foto and not str(foto).startswith(("http://", "https://")):
        foto = None
    return {
        "nick": d.get("username") or nick,
        "display_name": d.get("display_name") or d.get("username") or nick,
        "cid": u["cid"],
        "online": online,
        "availability": (d.get("availability") or "").strip().lower(),
        "sala": sala,
        "pais": d.get("country") or "?",
        "registrado": d.get("registered"),
        "idade_dias": idade_conta_dias(d),
        "seguidores": perfil.get("approx_follower_count"),
        "look": look,
        "foto_perfil": foto,
        "marcas": marcas,
        "tagline": (d.get("tagline") or "")[:140] or None,
    }


def checar_nick_publico(nick, ip):
    nick = (nick or "").strip()
    if not nick or len(nick) > 40:
        return 400, {"erro": "digite um nick valido"}
    if not rate_ok("pub:" + ip, maximo=60, janela=60):
        return 429, {"erro": "muitas tentativas; espere um pouco"}
    chave = nick.lower()
    guardado = _cache_pegar(chave)
    if guardado is not None:
        if guardado is False:
            return 404, {"erro": "nick nao encontrado no IMVU"}
        return 200, guardado
    try:
        dados = _checar_publico(nick)
    except ErroAPI as e:
        if "too many requests" in str(e).lower():
            return 429, {"erro": "muitas requisicoes; tente de novo em alguns minutos"}
        return 502, {"erro": "IMVU nao respondeu; tente de novo"}
    if not dados:
        _cache_guardar(chave, False)
        return 404, {"erro": "nick nao encontrado no IMVU"}
    _cache_guardar(chave, dados)
    return 200, dados


def buscar_sala_publica(room_id):
    m = re.search(r"(\d+-\d+)", room_id or "")
    if not m:
        raise ErroAPI("ID invalido (ex: 12345678-1)")
    rid = m.group(1)
    texto = _req_publico(
        f"https://www.imvu.com/rooms/show-room-details.php?room_instance_id={rid}",
        texto_puro=True,
    )
    mi = re.search(r"var roomInfo = (\{.*?\n\})\s*\n</script>", texto, re.S)
    if not mi:
        raise ErroAPI("sala nao encontrada (so salas publicas do diretorio)")
    bloco = mi.group(1)
    info = None
    for tentativa in (
        bloco,
        re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False",
                 re.sub(r"\bnull\b", "None", bloco))),
    ):
        try:
            info = ast.literal_eval(tentativa)
            if isinstance(info, dict):
                break
        except Exception:
            info = None
    if not isinstance(info, dict):
        info = {
            "room_name": (re.search(r"'room_name'\s*:\s*'([^']*)'", bloco) or [None, "?"])[1],
            "owner": (re.search(r"'owner'\s*:\s*'([^']*)'", bloco) or [None, "?"])[1],
            "count": (re.search(r"'count'\s*:\s*'(\d+)'", bloco) or [None, "?"])[1],
            "max": (re.search(r"'max'\s*:\s*'(\d+)'", bloco) or [None, "?"])[1],
            "desc": "",
            "participants": [],
        }
    participantes = []
    for p in info.get("participants") or []:
        if isinstance(p, dict):
            participantes.append([str(p.get("name", "?")), str(p.get("size", "") or "")])
        else:
            participantes.append([str(p), ""])
    return {
        "id": rid,
        "nome": str(info.get("room_name", "?")),
        "owner": str(info.get("owner", "?")),
        "count": str(info.get("count", "?")),
        "max": str(info.get("max", "?")),
        "desc": urllib.parse.unquote(str(info.get("desc") or ""))[:220],
        "participantes": participantes,
    }


class SessaoIMVU:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=_SSL_CTX),
        )
        self.sauce = None
        self.cid = None
        self.nick = None
        self.estado = "novo"
        self.ultimo_erro = None
        self._lock = threading.Lock()
        self._checado_em = 0.0
        self.valido_em = 0.0

    def _req(self, url, metodo="GET", corpo=None, com_sauce=False):
        headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
        if self.sauce and (metodo != "GET" or com_sauce):
            headers["x-imvu-sauce"] = self.sauce
            headers["x-imvu-application"] = "next_desktop/1"
        dados = None
        if corpo is not None:
            dados = json.dumps(corpo).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=dados, headers=headers, method=metodo)
        try:
            with self.opener.open(req, timeout=25) as resp:
                texto = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                texto = e.read().decode("utf-8", errors="replace")
            except Exception:
                raise ErroAPI(f"HTTP {e.code}")
        except Exception as e:
            raise ErroAPI(f"falha de rede: {e}")
        if "too many requests" in texto.lower():
            raise ErroAPI("IMVU bloqueou temporariamente (muitas requisicoes).")
        try:
            js = json.loads(texto)
        except Exception:
            raise ErroAPI("resposta inesperada do IMVU")
        if isinstance(js, dict) and js.get("status") == "failure":
            raise ErroAPI(f"{js.get('error', '?')}: {js.get('message', '?')}")
        return js

    def salvar_sessao(self):
        try:
            criar_pastas()
            dados = {
                "cookies": [
                    {
                        "version": c.version, "name": c.name, "value": c.value,
                        "port": c.port, "domain": c.domain, "path": c.path,
                        "secure": c.secure, "expires": c.expires,
                        "discard": c.discard, "comment": c.comment,
                        "comment_url": c.comment_url, "rest": c._rest,
                    }
                    for c in self.cookies
                ],
                "sauce": self.sauce,
                "cid": self.cid,
                "nick": self.nick,
            }
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(dados, f)
            try:
                os.chmod(SESSION_FILE, 0o600)
            except Exception:
                pass
            _log("sessao salva (o arquivo fica so no servidor; nunca suba pro GitHub)")
        except Exception as e:
            _log(f"aviso: nao consegui salvar a sessao: {e}")

    def carregar_sessao(self):
        if not os.path.exists(SESSION_FILE):
            return False
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                dados = json.load(f)
            for cd in dados.get("cookies") or []:
                cookie = http.cookiejar.Cookie(
                    cd.get("version", 0), cd.get("name", ""), cd.get("value", ""),
                    cd.get("port"), False, cd.get("domain", ""), True, cd.get("domain", "").startswith("."),
                    cd.get("path", "/"), True, cd.get("secure", False),
                    cd.get("expires"), cd.get("discard", False), cd.get("comment"),
                    cd.get("comment_url"), cd.get("rest") or {},
                )
                self.cookies.set_cookie(cookie)
            self.sauce = dados.get("sauce")
            self.cid = dados.get("cid")
            self.nick = dados.get("nick")
            return bool(self.sauce)
        except Exception as e:
            _log(f"aviso: sessao salva invalida ({e}); sera ignorada")
            return False

    def validar(self):
        try:
            js = self._req(f"{API}/login/me")
            node = (js.get("denormalized") or {}).get(js.get("id"), {})
            data = node.get("data") or {}
            rel = node.get("relations") or {}
            self.sauce = data.get("sauce") or self.sauce
            for candidato in (rel.get("user"), data.get("user"), rel.get("account"), data.get("account")):
                cid = _extrair_cid(candidato)
                if cid:
                    self.cid = cid
                    break
            if self.cid and not self.nick:
                try:
                    ujs = self._req(f"{API}/user/user-{self.cid}")
                    unode = (ujs.get("denormalized") or {}).get(ujs.get("id"), {})
                    udata = unode.get("data") or {}
                    self.nick = udata.get("username") or udata.get("display_name") or self.nick
                except ErroAPI:
                    pass
            if not self.sauce:
                self.ultimo_erro = "resposta do IMVU sem sauce"
                self.estado = "sessao_expirada"
                return False
            self.estado = "logado"
            self.ultimo_erro = None
            self._checado_em = time.time()
            self.valido_em = self._checado_em
            return True
        except ErroAPI as e:
            self.ultimo_erro = str(e)
            self.estado = "sessao_expirada"
            return False

    def login(self, codigo="", usuario=None, senha=None):
        usuario = (usuario if usuario is not None else IMVU_USER).strip()
        senha = senha if senha is not None else IMVU_PASS
        if not usuario or not senha:
            self.estado = "sem_credenciais"
            self.ultimo_erro = "configure IMVU_USER e IMVU_PASS no servidor"
            return False
        corpo = {
            "gdpr_cookie_acceptance": True,
            "username": usuario,
            "password": senha,
            "remember_device": True,
        }
        if codigo:
            corpo["2fa_code"] = codigo
        try:
            self._req(f"{API}/login", metodo="POST", corpo=corpo)
        except ErroAPI as e:
            msg = str(e)
            if any(t in msg.lower() for t in ("2fa", "code", "verific", "challenge")):
                self.estado = "precisa_2fa"
                self.ultimo_erro = "IMVU pediu codigo de verificacao"
            else:
                self.estado = "erro_login"
                self.ultimo_erro = msg
            return False
        if not self.validar():
            self.estado = "erro_login"
            return False
        self.salvar_sessao()
        _log("login OK no IMVU")
        return True

    def garantir_login(self):
        with self._lock:
            if self.sauce and (time.time() - self._checado_em) < 600:
                return self.estado == "logado" or self.validar()
            if self.sauce and self.validar():
                self.salvar_sessao()
                return True
            return self.login()

    def manter_viva(self):
        """Bate no IMVU de tempos em tempos para a sessao nao morrer de inatividade.

        Sessao de site expira quando fica parada — e isso que derruba a conta
        do servidor e faz o presente "sumir" do nada. Chamando /login/me
        periodicamente a sessao segue valida (igual ao app oficial aberto).
        """
        if not self.sauce:
            return self.garantir_login()
        try:
            with self._lock:
                ok = self.validar()
            if ok:
                self.salvar_sessao()
                try:
                    # visita o site como um navegador faria; ajuda a renovar
                    # a sessao da web (so /login/me pode nao bastar)
                    self._req("https://www.imvu.com/")
                except Exception:
                    pass
                return True
        except Exception as e:
            self.ultimo_erro = f"falha de rede: {e}"
            self.estado = "sessao_expirada"
        _log(f"keep-alive: sessao caiu ({self.ultimo_erro}); tentando entrar de novo")
        try:
            return self.garantir_login()
        except Exception:
            return False

    def resolver_nick(self, nick):
        url = f"{API}/user?" + urllib.parse.urlencode({"username": nick})
        js = self._req(url)
        raiz = js.get("denormalized", {})
        colecao = raiz.get(js.get("id"), {})
        itens = (colecao.get("data") or {}).get("items") or []
        if not itens:
            return None
        node_url = itens[0]
        node = raiz.get(node_url, {})
        m = re.search(r"user-(\d+)$", node_url)
        if not m:
            return None
        return {"cid": m.group(1), "data": node.get("data", {})}

    def saldo(self):
        js = self._req(f"{API}/wallet/wallet-{self.cid}")
        node = (js.get("denormalized") or {}).get(js.get("id"), {})
        data = node.get("data") or {}
        return int(data.get("credits") or 0)

    def enviar_creditos(self, cid_destino, mensagem):
        txn = f"gift-{self.cid}-1-{int(time.time())}"
        corpo = {
            "message": (mensagem or GIFT_MENSAGEM or "")[:200],
            "is_thank_you": False,
            "txn_id": txn,
            "type": 0,
            "credit_amount": GIFT_CREDITOS,
            "id": f"{API}/user/user-{cid_destino}",
        }
        self._req(f"{API}/user/user-{self.cid}/gifts", metodo="POST", corpo=corpo)
        return txn

    def wishlist(self, cid, limite=5):
        js = self._req(f"{API}/user/user-{cid}/wishlist?limit={limite}", com_sauce=True)
        raiz = js.get("denormalized") or {}
        colecao = raiz.get(js.get("id"), {})
        data = colecao.get("data") or {}
        itens = []
        for url in (data.get("items") or [])[:limite]:
            node = raiz.get(url) or {}
            d = node.get("data") or {}
            itens.append({
                "nome": d.get("name") or d.get("product_name") or "item",
                "preco": d.get("price_credits") or d.get("credits"),
            })
        return {"total": int(data.get("total_count") or len(itens)), "itens": itens}


SESSAO = SessaoIMVU()


STATUS_FRESCO_SEG = 15 * 60


def status_publico():
    hoje = presentes_hoje()
    restantes = max(0, GIFT_LIMITE_DIARIO - hoje)
    sessao_fresca = bool(SESSAO.valido_em) and (time.time() - SESSAO.valido_em) < STATUS_FRESCO_SEG
    return {
        "disponivel": bool(SESSAO.estado == "logado" and sessao_fresca),
        "presentes_hoje": hoje,
        "restantes_hoje": restantes,
        "janela_horas": GIFT_JANELA_HORAS,
        "creditos_por_presente": GIFT_CREDITOS,
    }


def config_publica():
    return {
        "ok": True,
        "servico": "IMVU Status",
        "versao_site": WEB_VERSAO,
        "firebase_key": FIREBASE_KEY,
        "google_client_id": (os.environ.get("GOOGLE_CLIENT_ID") or "").strip(),
        "login_exigido": bool(FIREBASE_KEY and EXIGIR_LOGIN),
        "creditos": GIFT_CREDITOS,
        "janela_horas": GIFT_JANELA_HORAS,
        "repo_github": "ronilsondesouza045-beep/imvu-status",
    }


def pedir_presente(body, ip):
    nick = str(body.get("nick") or "").strip()
    mensagem = str(body.get("mensagem") or "").strip()[:200]
    aparelho = str(body.get("aparelho") or "").strip()[:64]
    if not nick or len(nick) > 40:
        return 400, {"erro": "digite um nick valido"}
    if not rate_ok(ip):
        return 429, {"erro": "muitas tentativas; espere um pouco"}

    token = str(body.get("token") or "").strip()[:4096]
    conta = validar_conta(token) if token else None
    if FIREBASE_KEY and EXIGIR_LOGIN:
        if not conta:
            if token:
                return 401, {"erro": "sua sessao expirou ou o login nao confere; "
                                      "entre de novo no app (aba CONTA)"}
            return 401, {"erro": "entre na sua conta do app (aba CONTA) para "
                                  "resgatar o presente"}
        if not conta["verificado"]:
            return 403, {"erro": "confirme seu email para liberar o resgate; "
                                  "veja a caixa de entrada (e o spam) e tente de novo"}

    if not SESSAO.garantir_login():
        if SESSAO.estado == "precisa_2fa":
            _log("AVISO: login pendente de 2FA; use POST /admin/2fa com o codigo")
        else:
            _log(f"AVISO: sem sessao do IMVU ({SESSAO.estado}: {SESSAO.ultimo_erro}) — "
                 "rode o configurar_presente.bat e atualize a IMVU_SESSAO")
        return 503, {"erro": "presente indisponivel no momento (tente mais tarde)"}

    try:
        destino = SESSAO.resolver_nick(nick)
    except ErroAPI as e:
        if "too many requests" in str(e).lower():
            return 429, {"erro": "muitas requisicoes; tente de novo em alguns minutos"}
        return 502, {"erro": "IMVU nao respondeu; tente de novo"}
    if not destino:
        return 404, {"erro": "nick nao encontrado no IMVU"}

    cid_destino = destino["cid"]
    nick_ok = destino["data"].get("username") or nick
    if str(cid_destino) == str(SESSAO.cid):
        return 409, {"erro": "esse avatar nao pode receber os creditos gratis"}

    dias = idade_conta_dias(destino["data"])
    if dias is not None and dias < GIFT_IDADE_MINIMA_DIAS:
        return 403, {"erro": f"essa conta e muito nova para receber "
                              f"(minimo de {GIFT_IDADE_MINIMA_DIAS} dias de conta)"}

    if conta:
        registro = conta_do_uid(conta["uid"])
        if registro and str(registro.get("cid")) != str(cid_destino):
            nome_ligado = registro.get("nick") or f"user-{registro.get('cid')}"
            return 409, {"erro": f"sua conta esta ligada ao avatar {nome_ligado}; "
                                  "por seguranca o resgate fica sempre nele"}
        dono = conta_do_cid(cid_destino)
        if dono and dono.get("uid") != conta["uid"]:
            return 409, {"erro": "esse avatar ja esta ligado a outra conta; "
                                  "por seguranca o resgate so continua nessa conta"}

    ultimo = ja_recebeu_janela(cid_destino)
    if ultimo:
        return 409, {"erro": "esse avatar ja recebeu os creditos; "
                               f"pode receber de novo {texto_volta(ultimo)}"}

    if aparelho:
        ligado = cid_do_aparelho(aparelho)
        if ligado and ligado != str(cid_destino):
            return 409, {"erro": "esse aparelho ja esta ligado a outro avatar; "
                                  "so da para resgatar sempre com o mesmo nick"}

    if presentes_do_ip(ip) >= GIFT_LIMITE_IP_AVATARES:
        return 429, {"erro": "muitos avatares diferentes pedindo deste mesmo "
                              "aparelho/rede; tente mais tarde"}

    if presentes_hoje() >= GIFT_LIMITE_DIARIO:
        return 429, {"erro": "os creditos de hoje acabaram; tente amanha"}

    try:
        saldo = SESSAO.saldo()
    except ErroAPI:
        saldo = None
    if saldo is not None and saldo < GIFT_CREDITOS:
        return 503, {"erro": "presente indisponivel no momento (tente mais tarde)"}

    try:
        txn = SESSAO.enviar_creditos(cid_destino, mensagem)
    except ErroAPI as e:
        erro = str(e)
        baixo = erro.lower()
        if "too many requests" in baixo:
            return 429, {"erro": "muitas requisicoes; tente de novo em alguns minutos"}
        if "USER_GIFT-009" in erro:
            return 403, {"erro": "essa conta nao pode receber presentes (conta teen)"}
        if "USER_GIFT-206" in erro or "USER_GIFT-306" in erro:
            return 403, {"erro": "essa conta precisa confirmar o email no IMVU antes de receber"}
        if ("USER_GIFT-107" in erro or "USER_GIFT-212" in erro or "USER_GIFT-308" in erro
                or "credit" in baixo or "fund" in baixo):
            return 503, {"erro": "presente indisponivel no momento (tente mais tarde)"}
        _log(f"erro ao enviar creditos: {e}")
        return 502, {"erro": "nao consegui enviar agora; tente de novo"}

    registrar_presente(cid_destino, nick_ok, txn, ip)
    if aparelho:
        registrar_aparelho(aparelho, cid_destino, nick_ok)
    if conta:
        registrar_conta(conta["uid"], conta["email"], cid_destino, nick_ok)
    _log(f"{GIFT_CREDITOS} creditos enviados para {nick_ok} (user-{cid_destino})"
         + (f" | conta {conta['email']}" if conta else ""))
    return 200, {
        "ok": True,
        "nick": nick_ok,
        "creditos": GIFT_CREDITOS,
        "mensagem": f"{GIFT_CREDITOS} creditos enviados! Confira sua carteira no IMVU.",
    }


def checar_nick(nick, ip, token=None):
    nick = (nick or "").strip()
    if not nick or len(nick) > 40:
        return 400, {"erro": "digite um nick valido"}
    if not rate_ok(ip):
        return 429, {"erro": "muitas tentativas; espere um pouco"}
    conta = validar_conta(str(token or "").strip()) if token else None
    try:
        destino = SESSAO.resolver_nick(nick)
    except ErroAPI as e:
        if "too many requests" in str(e).lower():
            return 429, {"erro": "muitas requisicoes; tente de novo em alguns minutos"}
        return 502, {"erro": "IMVU nao respondeu; tente de novo"}
    if not destino:
        return 404, {"erro": "nick nao encontrado no IMVU"}
    cid = destino["cid"]
    nick_ok = destino["data"].get("username") or nick
    try:
        sessao_ok = SESSAO.garantir_login()
    except Exception:
        sessao_ok = False
    if sessao_ok:
        try:
            wl = SESSAO.wishlist(cid)
            wishlist = {"disponivel": True, "total": wl["total"], "itens": wl["itens"]}
        except ErroAPI:
            wishlist = {"disponivel": False, "motivo": "o IMVU nao deixou ver a wishlist agora"}
    else:
        wishlist = {"disponivel": False, "motivo": "servidor sem sessao logada no momento"}
    ultimo = ja_recebeu_janela(cid)
    dias = idade_conta_dias(destino["data"])
    apto = True
    motivo = None
    if ultimo:
        apto = False
        motivo = "ja recebeu nas ultimas %dh" % GIFT_JANELA_HORAS
    elif dias is not None and dias < GIFT_IDADE_MINIMA_DIAS:
        apto = False
        motivo = f"conta com menos de {GIFT_IDADE_MINIMA_DIAS} dias"
    elif FIREBASE_KEY and EXIGIR_LOGIN and not conta:
        apto = False
        motivo = "entre na sua conta no app (aba CONTA) para desbloquear o resgate"
    elif conta and not conta["verificado"]:
        apto = False
        motivo = "confirme seu email para desbloquear o resgate"
    elif conta:
        registro = conta_do_uid(conta["uid"])
        if registro and str(registro.get("cid")) != str(cid):
            apto = False
            motivo = ("sua conta esta ligada ao avatar "
                      f"{registro.get('nick') or ('user-' + str(registro.get('cid')))}")
        else:
            dono = conta_do_cid(cid)
            if dono and dono.get("uid") != conta["uid"]:
                apto = False
                motivo = "esse avatar ja esta ligado a outra conta"
    if apto and not sessao_ok:
        apto = False
        motivo = ("o presente esta pausado no momento (servidor reconectando); "
                  "tente de novo em alguns minutos")
    return 200, {
        "nick": nick_ok,
        "cid": cid,
        "ja_recebeu": bool(ultimo),
        "quando": ultimo.strftime("%Y-%m-%d %H:%M") if ultimo else None,
        "volta_em": texto_volta(ultimo) if ultimo else None,
        "apto": apto,
        "motivo": motivo,
        "login_exigido": bool(FIREBASE_KEY and EXIGIR_LOGIN),
        "servidor_ok": sessao_ok,
        "conta": ({"email": conta["email"], "verificado": bool(conta["verificado"])}
                  if conta else None),
        "creditos": GIFT_CREDITOS,
        "wishlist": wishlist,
        "restantes_hoje": max(0, GIFT_LIMITE_DIARIO - presentes_hoje()),
    }


def admin_autorizado(headers):
    if not ADMIN_TOKEN:
        return False
    enviado = headers.get("X-Admin-Token") or ""
    if not enviado:
        auth = headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            enviado = auth[7:].strip()
    return enviado == ADMIN_TOKEN


class Handler(BaseHTTPRequestHandler):
    server_version = "IMVUStatusGift/1.0"

    def log_message(self, fmt, *args):
        _log("%s %s" % (self.address_string(), fmt % args))

    def _responder(self, codigo, obj):
        dados = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(dados)
        except Exception:
            pass

    def _servir_arquivo(self, diretorio, relativo, cache="no-store"):
        partes = [p for p in str(relativo).replace("\\", "/").split("/")
                  if p and p not in (".", "..")]
        caminho = os.path.join(diretorio, *partes)
        if not os.path.isfile(caminho):
            self._responder(404, {"erro": "arquivo nao encontrado"})
            return
        tipo = mimetypes.guess_type(caminho)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in ("application/javascript",
                                                "application/json"):
            tipo += "; charset=utf-8"
        try:
            with open(caminho, "rb") as f:
                dados = f.read()
        except Exception:
            self._responder(500, {"erro": "nao consegui ler o arquivo"})
            return
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        try:
            self.wfile.write(dados)
        except Exception:
            pass

    def _corpo_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 10000:
            return {}
        try:
            dados = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
            return dados if isinstance(dados, dict) else {}
        except Exception:
            return {}

    def do_GET(self):
        rota = urllib.parse.urlparse(self.path).path
        ip = self.client_address[0]
        if rota == "/health":
            self._responder(200, {"servico": "imvu-status presente", "ok": True})
        elif rota in ("/", "/index.html"):
            self._servir_arquivo(WEB_DIR, "index.html")
        elif rota == "/manifest.webmanifest":
            self._servir_arquivo(WEB_DIR, "manifest.webmanifest")
        elif rota == "/favicon.ico":
            self._servir_arquivo(ASSETS_DIR, "icon.png", cache="public, max-age=86400")
        elif rota.startswith("/web/"):
            self._servir_arquivo(WEB_DIR, rota[len("/web/"):])
        elif rota.startswith("/assets/"):
            self._servir_arquivo(ASSETS_DIR, rota[len("/assets/"):],
                                 cache="public, max-age=86400")
        elif rota == "/api/config":
            self._responder(200, config_publica())
        elif rota == "/api/nick":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            nick = (qs.get("nick") or [""])[0]
            codigo, resposta = checar_nick_publico(nick, ip)
            self._responder(codigo, resposta)
        elif rota == "/api/sala":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            rid = (qs.get("id") or [""])[0]
            if not rate_ok("pub:" + ip, maximo=60, janela=60):
                self._responder(429, {"erro": "muitas tentativas; espere um pouco"})
                return
            try:
                self._responder(200, buscar_sala_publica(rid))
            except ErroAPI as e:
                if "too many requests" in str(e).lower():
                    self._responder(429, {"erro": "muitas requisicoes; tente de novo em alguns minutos"})
                else:
                    self._responder(404, {"erro": str(e)})
        elif rota == "/status":
            if not rate_ok(ip):
                self._responder(429, {"erro": "muitas tentativas; espere um pouco"})
                return
            self._responder(200, status_publico())
        elif rota == "/checar":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            nick = (qs.get("nick") or [""])[0]
            token = (self.headers.get("X-Conta-Token") or "").strip()
            codigo, resposta = checar_nick(nick, ip, token)
            self._responder(codigo, resposta)
        elif rota == "/admin/status":
            if not admin_autorizado(self.headers):
                self._responder(403, {"erro": "nao autorizado"})
                return
            info = status_publico()
            info.update({
                "estado_login": SESSAO.estado,
                "ultimo_erro": SESSAO.ultimo_erro,
                "keepalive_minutos": KEEPALIVE_MINUTOS,
                "sessao_validada_ha_seg": (int(time.time() - SESSAO.valido_em)
                                           if SESSAO.valido_em else None),
                "creditos_por_envio": GIFT_CREDITOS,
                "janela_horas": GIFT_JANELA_HORAS,
                "idade_minima_dias": GIFT_IDADE_MINIMA_DIAS,
                "limite_diario": GIFT_LIMITE_DIARIO,
                "aparelhos_ligados": aparelhos_ligados(),
                "contas_ligadas": contas_ligadas(),
                "login_exigido": bool(FIREBASE_KEY and EXIGIR_LOGIN),
                "teto_diario_creditos": GIFT_LIMITE_DIARIO * GIFT_CREDITOS,
            })
            self._responder(200, info)
        else:
            self._responder(404, {"erro": "rota nao encontrada"})

    def do_POST(self):
        rota = urllib.parse.urlparse(self.path).path
        ip = self.client_address[0]
        body = self._corpo_json()
        if rota == "/gift":
            codigo, resposta = pedir_presente(body, ip)
            self._responder(codigo, resposta)
        elif rota == "/admin/2fa":
            if not admin_autorizado(self.headers):
                self._responder(403, {"erro": "nao autorizado"})
                return
            codigo = str(body.get("codigo") or "").strip()
            if not codigo:
                self._responder(400, {"erro": "envie {\"codigo\": \"123456\"}"})
                return
            ok = SESSAO.login(codigo)
            self._responder(200 if ok else 401, {
                "ok": ok,
                "estado": SESSAO.estado,
                "detalhe": SESSAO.ultimo_erro,
            })
        else:
            self._responder(404, {"erro": "rota nao encontrada"})


def aquecer_login():
    time.sleep(1)
    if SESSAO.garantir_login():
        _log("sessao do IMVU pronta")
    else:
        _log(f"login pendente ({SESSAO.estado}): {SESSAO.ultimo_erro}")


def manter_sessao_viva():
    intervalo = KEEPALIVE_MINUTOS * 60
    while intervalo > 0:
        time.sleep(intervalo)
        try:
            if SESSAO.manter_viva():
                _log("keep-alive: sessao do IMVU renovada")
            else:
                _log(f"keep-alive: sessao fora ({SESSAO.estado}: {SESSAO.ultimo_erro})"
                     " — rode o configurar_presente.bat e atualize a IMVU_SESSAO")
        except Exception as e:
            _log(f"keep-alive: falhou ({e})")


def main():
    criar_pastas()
    init_db()
    materializar_sessao_env()
    if SESSAO.carregar_sessao():
        _log("sessao salva encontrada; validando...")
    threading.Thread(target=aquecer_login, daemon=True).start()
    if KEEPALIVE_MINUTOS > 0:
        threading.Thread(target=manter_sessao_viva, daemon=True).start()
        _log(f"keep-alive ligado (a cada {KEEPALIVE_MINUTOS} min)")
    servidor = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    servidor.daemon_threads = True
    _log(f"servindo na porta {PORT}")
    servidor.serve_forever()


if __name__ == "__main__":
    main()
