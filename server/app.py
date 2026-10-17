# -*- coding: utf-8 -*-
"""
IMVU Status - backend do "presente gratis".

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
  ADMIN_TOKEN         token para /admin/2fa e /admin/status
  DATA_DIR            pasta dos dados (padrao: server/dados)
  PORT                porta (padrao 8000)

NUNCA escreva credenciais no codigo nem suba a pasta de dados para o GitHub.
"""

import base64
import datetime
import http.cookiejar
import json
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "dados")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")
DB_FILE = os.path.join(DATA_DIR, "gifts.db")

IMVU_USER = (os.environ.get("IMVU_USER") or "").strip()
IMVU_PASS = os.environ.get("IMVU_PASS") or ""
GIFT_CREDITOS = max(100, int(os.environ.get("PRESENTE_CREDITOS") or 100))
GIFT_JANELA_HORAS = max(1, int(os.environ.get("GIFT_JANELA_HORAS") or 24))
GIFT_IDADE_MINIMA_DIAS = max(0, int(os.environ.get("GIFT_IDADE_MINIMA_DIAS") or 7))
GIFT_LIMITE_IP_AVATARES = max(1, int(os.environ.get("GIFT_LIMITE_IP_AVATARES") or 3))
GIFT_LIMITE_DIARIO = max(1, int(os.environ.get("GIFT_LIMITE_DIARIO") or 10))
GIFT_MENSAGEM = (os.environ.get("GIFT_MENSAGEM") or "").strip()
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or ""
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
    try:
        criar_pastas()
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f)
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
                return False
            self.estado = "logado"
            self.ultimo_erro = None
            self._checado_em = time.time()
            return True
        except ErroAPI as e:
            self.ultimo_erro = str(e)
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


def status_publico():
    hoje = presentes_hoje()
    restantes = max(0, GIFT_LIMITE_DIARIO - hoje)
    return {
        "disponivel": SESSAO.estado == "logado" or bool(SESSAO.sauce),
        "presentes_hoje": hoje,
        "restantes_hoje": restantes,
        "janela_horas": GIFT_JANELA_HORAS,
        "creditos_por_presente": GIFT_CREDITOS,
    }


def pedir_presente(body, ip):
    nick = str(body.get("nick") or "").strip()
    mensagem = str(body.get("mensagem") or "").strip()[:200]
    aparelho = str(body.get("aparelho") or "").strip()[:64]
    if not nick or len(nick) > 40:
        return 400, {"erro": "digite um nick valido"}
    if not rate_ok(ip):
        return 429, {"erro": "muitas tentativas; espere um pouco"}

    if not SESSAO.garantir_login():
        if SESSAO.estado == "precisa_2fa":
            _log("AVISO: login pendente de 2FA; use POST /admin/2fa com o codigo")
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
    _log(f"{GIFT_CREDITOS} creditos enviados para {nick_ok} (user-{cid_destino})")
    return 200, {
        "ok": True,
        "nick": nick_ok,
        "creditos": GIFT_CREDITOS,
        "mensagem": f"{GIFT_CREDITOS} creditos enviados! Confira sua carteira no IMVU.",
    }


def checar_nick(nick, ip):
    nick = (nick or "").strip()
    if not nick or len(nick) > 40:
        return 400, {"erro": "digite um nick valido"}
    if not rate_ok(ip):
        return 429, {"erro": "muitas tentativas; espere um pouco"}
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
    if SESSAO.sauce and SESSAO.garantir_login():
        try:
            wl = SESSAO.wishlist(cid)
            wishlist = {"disponivel": True, "total": wl["total"], "itens": wl["itens"]}
        except ErroAPI:
            wishlist = {"disponivel": False, "motivo": "o IMVU nao deixou ver a wishlist agora"}
    else:
        wishlist = {"disponivel": False, "motivo": "servidor ainda sem sessao logada"}
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
    return 200, {
        "nick": nick_ok,
        "cid": cid,
        "ja_recebeu": bool(ultimo),
        "quando": ultimo.strftime("%Y-%m-%d %H:%M") if ultimo else None,
        "volta_em": texto_volta(ultimo) if ultimo else None,
        "apto": apto,
        "motivo": motivo,
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
        if rota in ("/", "/health"):
            self._responder(200, {"servico": "imvu-status presente", "ok": True})
        elif rota == "/status":
            if not rate_ok(ip):
                self._responder(429, {"erro": "muitas tentativas; espere um pouco"})
                return
            self._responder(200, status_publico())
        elif rota == "/checar":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            nick = (qs.get("nick") or [""])[0]
            codigo, resposta = checar_nick(nick, ip)
            self._responder(codigo, resposta)
        elif rota == "/admin/status":
            if not admin_autorizado(self.headers):
                self._responder(403, {"erro": "nao autorizado"})
                return
            info = status_publico()
            info.update({
                "estado_login": SESSAO.estado,
                "ultimo_erro": SESSAO.ultimo_erro,
                "creditos_por_envio": GIFT_CREDITOS,
                "janela_horas": GIFT_JANELA_HORAS,
                "idade_minima_dias": GIFT_IDADE_MINIMA_DIAS,
                "limite_diario": GIFT_LIMITE_DIARIO,
                "aparelhos_ligados": aparelhos_ligados(),
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


def main():
    criar_pastas()
    init_db()
    materializar_sessao_env()
    if SESSAO.carregar_sessao():
        _log("sessao salva encontrada; validando...")
    threading.Thread(target=aquecer_login, daemon=True).start()
    servidor = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    servidor.daemon_threads = True
    _log(f"servindo na porta {PORT}")
    servidor.serve_forever()


if __name__ == "__main__":
    main()
