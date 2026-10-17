# -*- coding: utf-8 -*-
"""
IMVU Status - backend do "presente gratis".

Fica logado com a conta do DONO (credenciais apenas em variaveis de ambiente
do servidor; nada disso vai para o app/APK) e envia presentes usando os
mesmos endpoints que o site do IMVU usa (api.imvu.com).

Controles:
  - 1 presente por avatar (guardado por CID: trocar de nick nao burla)
  - teto de presentes por dia (protege seus creditos de abuso)
  - checa o saldo da conta antes de enviar
  - limitador simples por IP

Variaveis de ambiente:
  IMVU_USER           usuario da conta do dono
  IMVU_PASS           senha da conta do dono
  IMVU_SESSAO         (opcional) sessao pronta gerada por server/login_local.py:
                      cole aqui o texto e o servidor ja acorda logado,
                      sem precisar de senha nem 2FA no servidor
  GIFT_PRODUCT_ID     ID numerico do item que sera presenteado
  GIFT_CREDITOS       valor do presente em creditos (padrao 500)
  GIFT_LIMITE_PESSOA  presentes por avatar (padrao 1)
  GIFT_LIMITE_DIARIO  presentes por dia no total (padrao 10)
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
GIFT_PRODUCT_ID = re.sub(r"\D", "", os.environ.get("GIFT_PRODUCT_ID") or "")
GIFT_CREDITOS = int(os.environ.get("GIFT_CREDITOS") or 500)
GIFT_LIMITE_PESSOA = int(os.environ.get("GIFT_LIMITE_PESSOA") or 1)
GIFT_LIMITE_DIARIO = int(os.environ.get("GIFT_LIMITE_DIARIO") or 10)
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
            con.commit()
        finally:
            con.close()


def presentes_do_cid(cid):
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute("SELECT COUNT(*) FROM presentes WHERE cid = ?", (cid,))
            return int(cur.fetchone()[0] or 0)
        finally:
            con.close()


def quando_recebeu(cid):
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            cur = con.execute("SELECT MIN(quando) FROM presentes WHERE cid = ?", (cid,))
            linha = cur.fetchone()
            return linha[0] if linha and linha[0] else None
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


def registrar_presente(cid, nick, txn):
    quando = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _DB_LOCK:
        con = sqlite3.connect(DB_FILE)
        try:
            con.execute(
                "INSERT OR IGNORE INTO presentes (cid, nick, quando, txn) VALUES (?, ?, ?, ?)",
                (cid, nick, quando, txn),
            )
            con.commit()
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

    def nome_produto(self):
        js = self._req(f"{API}/product/product-{GIFT_PRODUCT_ID}")
        node = (js.get("denormalized") or {}).get(js.get("id"), {})
        return (node.get("data") or {}).get("product_name")

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
                "nome": d.get("product_name") or "item",
                "preco": d.get("price_credits") or d.get("credits"),
            })
        return {"total": int(data.get("total_count") or len(itens)), "itens": itens}

    def enviar_presente(self, cid_destino, mensagem):
        txn = f"gift-{self.cid}-{cid_destino}-{int(time.time())}"
        corpo = {
            "id": f"{API}/user/user-{cid_destino}",
            "product_id": f"{API}/product/product-{GIFT_PRODUCT_ID}",
            "is_thank_you": False,
            "txn_id": txn,
            "type": 1,
            "message": (mensagem or GIFT_MENSAGEM or "")[:200],
        }
        self._req(f"{API}/user/user-{self.cid}/gifts", metodo="POST", corpo=corpo)
        return txn


SESSAO = SessaoIMVU()
_PRODUTO_NOME = {"nome": None, "checado": 0.0}


def status_publico():
    hoje = presentes_hoje()
    restantes = max(0, GIFT_LIMITE_DIARIO - hoje)
    return {
        "disponivel": SESSAO.estado == "logado" or bool(SESSAO.sauce),
        "presentes_hoje": hoje,
        "restantes_hoje": restantes,
        "limite_por_avatar": GIFT_LIMITE_PESSOA,
        "creditos_por_presente": GIFT_CREDITOS,
    }


def pedir_presente(body, ip):
    nick = str(body.get("nick") or "").strip()
    mensagem = str(body.get("mensagem") or "").strip()[:200]
    if not nick or len(nick) > 40:
        return 400, {"erro": "digite um nick valido"}
    if not rate_ok(ip):
        return 429, {"erro": "muitas tentativas; espere um pouco"}
    if not GIFT_PRODUCT_ID:
        return 503, {"erro": "presente indisponivel no momento"}

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
        return 409, {"erro": "esse avatar nao pode receber o presente gratis"}

    if presentes_do_cid(cid_destino) >= GIFT_LIMITE_PESSOA:
        return 409, {"erro": "esse avatar ja recebeu o presente gratis"}

    if presentes_hoje() >= GIFT_LIMITE_DIARIO:
        return 429, {"erro": "os presentes de hoje acabaram; tente amanha"}

    try:
        saldo = SESSAO.saldo()
    except ErroAPI:
        saldo = None
    if saldo is not None and saldo < GIFT_CREDITOS:
        return 503, {"erro": "presente indisponivel no momento (tente mais tarde)"}

    try:
        txn = SESSAO.enviar_presente(cid_destino, mensagem)
    except ErroAPI as e:
        msg = str(e).lower()
        if "too many requests" in msg:
            return 429, {"erro": "muitas requisicoes; tente de novo em alguns minutos"}
        if "credit" in msg or "fund" in msg:
            return 503, {"erro": "presente indisponivel no momento (tente mais tarde)"}
        _log(f"erro ao enviar presente: {e}")
        return 502, {"erro": "nao consegui enviar o presente agora; tente de novo"}

    registrar_presente(cid_destino, nick_ok, txn)
    nome = _PRODUTO_NOME.get("nome")
    if not nome and (time.time() - _PRODUTO_NOME["checado"] > 3600):
        try:
            nome = SESSAO.nome_produto()
            _PRODUTO_NOME.update({"nome": nome, "checado": time.time()})
        except ErroAPI:
            _PRODUTO_NOME["checado"] = time.time()
    _log(f"presente enviado para {nick_ok} (user-{cid_destino})")
    return 200, {
        "ok": True,
        "nick": nick_ok,
        "presente": nome or "item",
        "mensagem": "Presente enviado! Confira seu inventario no IMVU.",
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
    return 200, {
        "nick": nick_ok,
        "cid": cid,
        "ja_recebeu": presentes_do_cid(cid) >= GIFT_LIMITE_PESSOA,
        "quando": quando_recebeu(cid),
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
                "produto_id": GIFT_PRODUCT_ID,
                "limite_diario": GIFT_LIMITE_DIARIO,
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
    if not GIFT_PRODUCT_ID:
        _log("AVISO: GIFT_PRODUCT_ID nao configurado; /gift vai responder 'indisponivel'")
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
