# -*- coding: utf-8 -*-
"""
IMVU Status - app Android (KivyMD) que consulta a API publica do IMVU.

Mostra: online/offline, disponibilidade, sala atual (publica/privada quando o
IMVU expoe), diretorio de salas publicas e login opcional na sua conta.
Nao faz varredura de pessoas nem furar localizacao desativada.

Compilado para APK via buildozer (GitHub Actions). Veja README.md.
"""

import http.cookiejar
import html as htmlmod
import json
import re
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import datetime
import hashlib
import os
import webbrowser

from kivy.clock import Clock
from kivy.lang import Builder
from kivy.metrics import dp

from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.toolbar import MDTopAppBar  # noqa: F401
from kivymd.uix.bottomnavigation import MDBottomNavigation, MDBottomNavigationItem  # noqa: F401
from kivymd.uix.card import MDCard  # noqa: F401
from kivymd.uix.label import MDLabel  # noqa: F401
from kivymd.uix.button import MDRaisedButton, MDFlatButton  # noqa: F401
from kivymd.uix.textfield import MDTextField  # noqa: F401
from kivymd.uix.spinner import MDSpinner  # noqa: F401
from kivymd.uix.boxlayout import MDBoxLayout  # noqa: F401
from kivymd.uix.selectioncontrol import MDCheckbox  # noqa: F401

API = "https://api.imvu.com"
UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36")

APP_VERSAO = "1.3.1"
REPO_GH = "ronilsondesouza045-beep/imvu-status"
RELEASES_API = f"https://api.github.com/repos/{REPO_GH}/releases/latest"

# URL do seu backend do presente gratis (veja server/app.py e o README).
# Troque pelo endereco que voce hospedar (ex: https://xxxx.onrender.com).
# O app so conversa com esse servidor: sua conta e seus creditos NUNCA
# ficam no APK nem aparecem na interface.
PRESENTE_URL = "https://imvu-status-presente.onrender.com"

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

VERDE = "#4CAF50"
VERMELHO = "#EF5350"
AMARELO = "#FFB300"


class ErroAPI(Exception):
    pass


def _limpa(texto):
    return str(texto or "").replace("[", "(").replace("]", ")")


def _data_epoch(seg):
    try:
        return datetime.datetime.fromtimestamp(int(seg), tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return "?"


def _versao_tupla(txt):
    partes = re.findall(r"\d+", str(txt or ""))
    return tuple(int(p) for p in partes[:4]) or (0,)


_APARELHO_CACHE = {"id": None}


def _id_aparelho():
    if _APARELHO_CACHE["id"] is not None:
        return _APARELHO_CACHE["id"]
    vid = ""
    try:
        from jnius import autoclass
        atividade = autoclass("org.kivy.android.PythonActivity").mActivity
        secure = autoclass("android.provider.Settings$Secure")
        aid = secure.getString(atividade.getContentResolver(), "android_id")
        if aid:
            vid = hashlib.sha256(("imvu-status:" + str(aid)).encode("utf-8")).hexdigest()[:32]
    except Exception:
        pass
    if not vid:
        try:
            from kivy.app import App
            pasta = App.get_running_app().user_data_dir
            caminho = os.path.join(pasta, "aparelho.txt")
            if os.path.exists(caminho):
                with open(caminho, "r", encoding="utf-8") as f:
                    vid = (f.read() or "").strip()
            if not vid:
                vid = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
                os.makedirs(pasta, exist_ok=True)
                with open(caminho, "w", encoding="utf-8") as f:
                    f.write(vid)
        except Exception:
            vid = ""
    _APARELHO_CACHE["id"] = vid
    return vid


class ClienteIMVU:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=_SSL_CTX),
        )
        self.logado = False
        self.nick = None
        self.sauce = None

    def _req(self, url, metodo="GET", corpo=None, json_sim=True):
        headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
        if self.sauce and metodo != "GET":
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
        if not json_sim:
            return texto
        try:
            js = json.loads(texto)
        except Exception:
            raise ErroAPI("resposta inesperada do IMVU")
        if isinstance(js, dict) and js.get("status") == "failure":
            raise ErroAPI(f"{js.get('error', '?')}: {js.get('message', '?')}")
        return js

    def baixar_imagem(self, url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with self.opener.open(req, timeout=25) as resp:
                dados = resp.read()
        except Exception:
            return None
        if dados and (dados[:4] == b"\x89PNG" or dados[:3] == b"\xff\xd8\xff"):
            return dados
        return None

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
        return {
            "cid": m.group(1) if m else "?",
            "data": node.get("data", {}),
            "rel": node.get("relations", {}),
        }

    def checar(self, nick):
        u = self.resolver_nick(nick)
        if not u:
            return None
        presenca = {}
        try:
            purl = u["rel"].get("presence") or f"{API}/presence/presence-{u['cid']}"
            pjs = self._req(purl)
            pnode = (pjs.get("denormalized") or {}).get(purl) or {}
            presenca = pnode.get("data", {})
        except ErroAPI:
            pass
        online = bool(presenca.get("online", u["data"].get("online", False)))
        resultado = {
            "nick": u["data"].get("username") or nick,
            "cid": u["cid"],
            "online": online,
            "availability": (u["data"].get("availability") or "").strip().lower(),
            "data": u["data"],
            "presence": presenca,
            "sala": None,
        }
        if "current_room" in u["rel"]:
            try:
                rjs = self._req(u["rel"]["current_room"])
                rnode = (rjs.get("denormalized") or {}).get(u["rel"]["current_room"]) or {}
                resultado["sala"] = rnode.get("data", {})
            except ErroAPI:
                resultado["sala"] = {}
        perfil = {}
        try:
            if "profile" in u["rel"]:
                pjs = self._req(u["rel"]["profile"])
                pnode = (pjs.get("denormalized") or {}).get(u["rel"]["profile"]) or {}
                perfil = pnode.get("data", {})
        except ErroAPI:
            pass
        look = None
        try:
            if "profile_outfit" in u["rel"]:
                ojs = self._req(u["rel"]["profile_outfit"])
                onode = (ojs.get("denormalized") or {}).get(u["rel"]["profile_outfit"]) or {}
                odata = onode.get("data", {})
                prods = [p.get("product_id") for p in (odata.get("products") or [])
                         if isinstance(p, dict) and p.get("product_id")]
                if prods:
                    look = {"total": len(prods), "corpo": None}
                    purl = f"{API}/product/product-{prods[0]}"
                    try:
                        prjs = self._req(purl)
                        prnode = (prjs.get("denormalized") or {}).get(purl) or {}
                        look["corpo"] = (prnode.get("data") or {}).get("product_name")
                    except ErroAPI:
                        pass
        except ErroAPI:
            pass
        resultado["perfil"] = perfil
        resultado["look"] = look
        resultado["imagem_3d"] = u["data"].get("avatar_image")
        resultado["retrato_3d"] = u["data"].get("avatar_portrait_image")
        resultado["foto_perfil"] = perfil.get("image") or u["data"].get("thumbnail_url")
        return resultado

    def buscar_sala(self, room_id):
        m = re.search(r"(\d+-\d+)", room_id or "")
        if not m:
            raise ErroAPI("ID invalido (ex: 12345678-1)")
        rid = m.group(1)
        texto = self._req(
            f"https://www.imvu.com/rooms/show-room-details.php?room_instance_id={rid}",
            json_sim=False,
        )
        mi = re.search(r"var roomInfo = (\{.*?\n\})\s*\n</script>", texto, re.S)
        if not mi:
            raise ErroAPI("Sala nao encontrada (so salas publicas do diretorio)")
        bloco = mi.group(1)
        bloco_py = re.sub(r"\btrue\b", "True", bloco)
        bloco_py = re.sub(r"\bfalse\b", "False", bloco_py)
        bloco_py = re.sub(r"\bnull\b", "None", bloco_py)
        try:
            info = eval(bloco_py, {"__builtins__": {}}, {})  # noqa: S307 (literal controlado)
        except Exception:
            campos = {
                "room_name": (re.search(r"'room_name'\s*:\s*'([^']*)'", bloco) or [None, "?"])[1],
                "owner": (re.search(r"'owner'\s*:\s*'([^']*)'", bloco) or [None, "?"])[1],
                "count": (re.search(r"'count'\s*:\s*'(\d+)'", bloco) or [None, "?"])[1],
                "max": (re.search(r"'max'\s*:\s*'(\d+)'", bloco) or [None, "?"])[1],
                "desc": "",
                "participants": [],
            }
            info = campos
        partes = []
        for p in info.get("participants") or []:
            if isinstance(p, dict):
                partes.append((_limpa(p.get("name", "?")), p.get("size", "")))
            else:
                partes.append((_limpa(p), ""))
        return {
            "id": rid,
            "nome": _limpa(info.get("room_name", "?")),
            "owner": _limpa(info.get("owner", "?")),
            "count": info.get("count", "?"),
            "max": info.get("max", "?"),
            "desc": urllib.parse.unquote(str(info.get("desc") or "")),
            "participantes": partes,
        }

    def login(self, usuario, senha, codigo=""):
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
                return False, "2FA " + msg
            return False, msg
        try:
            js = self._req(f"{API}/login/me")
            node = (js.get("denormalized") or {}).get(js.get("id"), {})
            self.sauce = (node.get("data") or {}).get("sauce")
            self.logado = True
            self.nick = usuario
            return True, f"Logado como {usuario}"
        except ErroAPI as e:
            return False, f"login ok, mas /login/me falhou: {e}"


def fmt_checagem(res):
    d = res["data"]
    nome = _limpa(d.get("display_name") or res["nick"])
    cor = VERDE if res["online"] else VERMELHO
    estado = "ONLINE" if res["online"] else "OFFLINE"
    linhas = [
        f"[b]{nome}[/b]  ({_limpa(res['nick'])} | CID {res['cid']})",
        f"Status: [color={cor}][b]{estado}[/b][/color]",
    ]
    av = res.get("availability") or ""
    rotulos = {"available": "Disponivel", "online": "Online", "away": "Ausente",
               "dnd": "Nao perturbe (DND)", "offline": "Offline"}
    if av:
        linhas.append(f"Disponibilidade: {rotulos.get(av, _limpa(av))}")
    sala = res.get("sala")
    if sala:
        privacy = str(sala.get("privacy") or "?").lower()
        if "priv" in privacy:
            tipo = f"[color={AMARELO}][b]PRIVADA[/b][/color]"
        elif "pub" in privacy:
            tipo = "publica"
        else:
            tipo = _limpa(privacy)
        linhas.append(f"Sala atual: \"{_limpa(sala.get('name', '?'))}\" ({tipo})")
        if not res["online"]:
            linhas.append("[i](status offline, mas o perfil aponta essa sala — pode ser modo \"aparecer offline\")[/i]")
    elif res["online"]:
        linhas.append("Sala atual: nao exposta agora")
    linhas.append(f"Pais: {_limpa(d.get('country') or '?')} | Membro desde {_data_epoch(d.get('registered'))}")
    perfil = res.get("perfil") or {}
    if perfil.get("approx_follower_count") is not None:
        linhas.append(f"Seguidores: ~{perfil.get('approx_follower_count')}")
    look = res.get("look")
    if look:
        corpo = _limpa(look.get("corpo")) if look.get("corpo") else "look atual"
        linhas.append(f"Avatar/look: {corpo} ({look.get('total')} itens)")
    marcas = []
    if d.get("is_vip"):
        marcas.append("VIP")
    if d.get("is_ap") or d.get("is_ap_plus"):
        marcas.append("AccessPass")
    if d.get("is_creator"):
        marcas.append("Criador")
    if marcas:
        linhas.append("Perfil: " + ", ".join(marcas))
    if d.get("tagline"):
        linhas.append(f"[i]{_limpa(d['tagline'])[:140]}[/i]")
    return "\n".join(linhas)


def fmt_sala(info):
    linhas = [
        f"[b]{info['nome']}[/b]  ({info['id']})",
        f"Dono: {info['owner']}",
        f"Ocupacao: {info['count']}/{info['max']}",
    ]
    if info.get("desc"):
        linhas.append(f"[i]{_limpa(info['desc'])[:220]}[/i]")
    if info["participantes"]:
        linhas.append("[b]Participantes:[/b]")
        for nome, tam in info["participantes"]:
            linhas.append(f"  • {nome}" + (f" ({tam} KB)" if tam else ""))
    elif str(info.get("count")) not in ("0", "?", "None"):
        linhas.append("(lista de participantes nao exposta)")
    else:
        linhas.append("sala vazia")
    return "\n".join(linhas)


KV = """
<MainScreen>:
    MDBoxLayout:
        orientation: "vertical"

        MDTopAppBar:
            title: "IMVU Status"
            elevation: 3

        MDBottomNavigation:
            panel_color: 0.08, 0.09, 0.12, 1

            MDBottomNavigationItem:
                name: "tab_nick"
                text: "NICK"
                icon: "account-search"
                ScrollView:
                    do_scroll_x: False
                    MDBoxLayout:
                        orientation: "vertical"
                        size_hint_y: None
                        height: self.minimum_height
                        padding: dp(16)
                        spacing: dp(12)
                        MDTextField:
                            id: nick_field
                            hint_text: "nick do avatar"
                            mode: "rectangle"
                            on_text_validate: root.checar_nick()
                        MDBoxLayout:
                            orientation: "horizontal"
                            size_hint_y: None
                            height: dp(48)
                            spacing: dp(8)
                            MDRaisedButton:
                                id: btn_monitor
                                text: "MONITORAR"
                                on_release: root.toggle_monitor()
                            MDTextField:
                                id: intervalo_field
                                hint_text: "segundos"
                                text: "30"
                                mode: "rectangle"
                                size_hint_x: 0.4
                        MDCard:
                            orientation: "vertical"
                            padding: dp(14)
                            spacing: dp(10)
                            size_hint_y: None
                            height: self.minimum_height
                            radius: [dp(14)]
                            Image:
                                id: foto_avatar
                                size_hint_y: None
                                height: 0
                                opacity: 0
                                fit_mode: "contain"
                            MDSpinner:
                                id: spinner
                                size_hint: None, None
                                size: dp(36), dp(36)
                                pos_hint: {"center_x": .5}
                                active: False
                            MDLabel:
                                id: resultado
                                markup: True
                                text: "Digite um nick e toque em MONITORAR ou no botao CHECAR abaixo."
                                size_hint_y: None
                                height: self.texture_size[1]
                                text_size: self.width, None
                        MDRaisedButton:
                            text: "CHECAR"
                            on_release: root.checar_nick()

            MDBottomNavigationItem:
                name: "tab_sala"
                text: "SALA"
                icon: "magnify"
                ScrollView:
                    do_scroll_x: False
                    MDBoxLayout:
                        orientation: "vertical"
                        size_hint_y: None
                        height: self.minimum_height
                        padding: dp(16)
                        spacing: dp(12)
                        MDTextField:
                            id: sala_field
                            hint_text: "ID da sala (ex: 12345678-1) ou link"
                            mode: "rectangle"
                            on_text_validate: root.buscar_sala()
                        MDRaisedButton:
                            text: "BUSCAR SALA"
                            on_release: root.buscar_sala()
                        MDCard:
                            orientation: "vertical"
                            padding: dp(14)
                            size_hint_y: None
                            height: self.minimum_height
                            radius: [dp(14)]
                            MDLabel:
                                id: sala_result
                                markup: True
                                text: "Cole o ID de uma sala publica para ver dono, lotacao e participantes."
                                size_hint_y: None
                                height: self.texture_size[1]
                                text_size: self.width, None

            MDBottomNavigationItem:
                name: "tab_conta"
                text: "CONTA"
                icon: "login"
                ScrollView:
                    do_scroll_x: False
                    MDBoxLayout:
                        orientation: "vertical"
                        size_hint_y: None
                        height: self.minimum_height
                        padding: dp(16)
                        spacing: dp(12)
                        MDLabel:
                            markup: True
                            text: "Login opcional na SUA conta. A senha vai somente para o IMVU; nada e salvo no aparelho."
                            size_hint_y: None
                            height: self.texture_size[1]
                            text_size: self.width, None
                        MDTextField:
                            id: login_user
                            hint_text: "usuario IMVU"
                            mode: "rectangle"
                        MDTextField:
                            id: login_pass
                            hint_text: "senha"
                            password: True
                            mode: "rectangle"
                        MDTextField:
                            id: login_code
                            hint_text: "codigo 2FA (so se o IMVU pedir)"
                            mode: "rectangle"
                        MDBoxLayout:
                            orientation: "horizontal"
                            size_hint_y: None
                            height: dp(48)
                            spacing: dp(8)
                            MDRaisedButton:
                                text: "ENTRAR"
                                on_release: root.fazer_login()
                            MDFlatButton:
                                text: "SAIR"
                                on_release: root.sair_conta()
                        MDCard:
                            orientation: "vertical"
                            padding: dp(14)
                            size_hint_y: None
                            height: self.minimum_height
                            radius: [dp(14)]
                            MDLabel:
                                id: conta_result
                                markup: True
                                text: "sessao: anonimo"
                                size_hint_y: None
                                height: self.texture_size[1]
                                text_size: self.width, None

            MDBottomNavigationItem:
                name: "tab_presente"
                text: "PRESENTE"
                icon: "gift"
                ScrollView:
                    do_scroll_x: False
                    MDBoxLayout:
                        orientation: "vertical"
                        size_hint_y: None
                        height: self.minimum_height
                        padding: dp(16)
                        spacing: dp(12)
                        MDLabel:
                            markup: True
                            text: "Presente gratis: 100 CREDITOS a cada 24 horas para cada avatar! Digite seu nick, confirme que a conta e sua e resgate. Toque em VER WISHLIST para ver a lista de desejos do nick."
                            size_hint_y: None
                            height: self.texture_size[1]
                            text_size: self.width, None
                        MDTextField:
                            id: presente_nick
                            hint_text: "seu nick no IMVU"
                            mode: "rectangle"
                            on_text_validate: root.verificar_presente()
                        MDTextField:
                            id: presente_msg
                            hint_text: "mensagem (opcional)"
                            mode: "rectangle"
                        MDBoxLayout:
                            orientation: "horizontal"
                            size_hint_y: None
                            height: dp(44)
                            spacing: dp(0)
                            MDCheckbox:
                                id: presente_confirmo
                                size_hint: None, None
                                size: dp(44), dp(44)
                                pos_hint: {"center_y": .5}
                            MDLabel:
                                markup: True
                                text: "Confirmo que este nick e a [b]minha conta[/b] no IMVU."
                                size_hint_y: None
                                height: self.texture_size[1]
                                text_size: self.width, None
                        MDBoxLayout:
                            orientation: "horizontal"
                            size_hint_y: None
                            height: dp(48)
                            spacing: dp(8)
                            MDRaisedButton:
                                text: "RESGATAR 100 CREDITOS"
                                on_release: root.pegar_presente()
                            MDFlatButton:
                                text: "VER WISHLIST"
                                on_release: root.verificar_presente()
                        MDSpinner:
                            id: presente_spinner
                            size_hint: None, None
                            size: dp(36), dp(36)
                            pos_hint: {"center_x": .5}
                            active: False
                        MDCard:
                            orientation: "vertical"
                            padding: dp(14)
                            spacing: dp(8)
                            size_hint_y: None
                            height: self.minimum_height
                            radius: [dp(14)]
                            MDLabel:
                                id: presente_result
                                markup: True
                                text: "Digite seu nick, confirme que a conta e sua e toque em RESGATAR 100 CREDITOS."
                                size_hint_y: None
                                height: self.texture_size[1]
                                text_size: self.width, None

            MDBottomNavigationItem:
                name: "tab_app"
                text: "APP"
                icon: "update"
                ScrollView:
                    do_scroll_x: False
                    MDBoxLayout:
                        orientation: "vertical"
                        size_hint_y: None
                        height: self.minimum_height
                        padding: dp(16)
                        spacing: dp(12)
                        MDLabel:
                            id: app_versao
                            markup: True
                            text: "Versao instalada: ?"
                            size_hint_y: None
                            height: self.texture_size[1]
                            text_size: self.width, None
                        MDRaisedButton:
                            text: "VERIFICAR ATUALIZACAO"
                            on_release: root.verificar_update(True)
                        MDCard:
                            orientation: "vertical"
                            padding: dp(14)
                            spacing: dp(8)
                            size_hint_y: None
                            height: self.minimum_height
                            radius: [dp(14)]
                            MDLabel:
                                id: update_titulo
                                markup: True
                                text: "Toque em VERIFICAR ATUALIZACAO para ver se ha versao nova."
                                size_hint_y: None
                                height: self.texture_size[1]
                                text_size: self.width, None
                            MDLabel:
                                id: update_notas
                                markup: False
                                text: ""
                                size_hint_y: None
                                height: self.texture_size[1]
                                text_size: self.width, None
                            MDRaisedButton:
                                id: btn_baixar
                                text: "BAIXAR ATUALIZACAO"
                                opacity: 0
                                disabled: True
                                on_release: root.abrir_download()
"""


class MainScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cliente = ClienteIMVU()
        self._monitor_event = None
        self._ocupado = False
        self._update_url = None
        self._manual_update = False
        self._presente_ocupado = False

    def checar_nick(self):
        nick = (self.ids.nick_field.text or "").strip()
        if not nick:
            self.ids.resultado.text = f"[color={AMARELO}]Digite um nick primeiro.[/color]"
            return
        if self._ocupado:
            return
        self._ocupado = True
        self.ids.spinner.active = True
        self.ids.resultado.text = "Consultando..."
        threading.Thread(target=self._t_checar, args=(nick,), daemon=True).start()

    def _t_checar(self, nick):
        try:
            res = self.cliente.checar(nick)
            erro = None
        except Exception as e:
            res, erro = None, str(e)
        if res:
            res["img_local"] = self._obter_imagem(res)
        Clock.schedule_once(lambda dt: self._mostrar_checagem(res, erro), 0)

    def _obter_imagem(self, res):
        candidatos = []
        if self.cliente.logado:
            if res.get("imagem_3d"):
                candidatos.append(res["imagem_3d"])
            if res.get("retrato_3d"):
                candidatos.append(res["retrato_3d"])
        if res.get("foto_perfil"):
            candidatos.append(res["foto_perfil"])
        base = None
        try:
            base = MDApp.get_running_app().user_data_dir
        except Exception:
            base = None
        for url in candidatos:
            dados = self.cliente.baixar_imagem(url)
            if not dados:
                continue
            if not base:
                return None
            nome = "avatar_%s_%s.png" % (res.get("cid", "x"),
                                         hashlib.md5(url.encode("utf-8")).hexdigest()[:10])
            caminho = os.path.join(base, nome)
            try:
                with open(caminho, "wb") as f:
                    f.write(dados)
                return caminho
            except Exception:
                continue
        return None

    def _mostrar_imagem(self, caminho):
        img = self.ids.foto_avatar
        if caminho:
            img.source = caminho
            img.height = dp(220)
            img.opacity = 1
            try:
                img.reload()
            except Exception:
                pass
        else:
            img.source = ""
            img.height = 0
            img.opacity = 0

    def _mostrar_checagem(self, res, erro):
        self._ocupado = False
        self.ids.spinner.active = False
        self._mostrar_imagem(res.get("img_local") if res else None)
        if erro:
            self.ids.resultado.text = f"[color={VERMELHO}]Erro: {_limpa(erro)}[/color]"
        elif res is None:
            self.ids.resultado.text = f"[color={AMARELO}]Nao encontrado no IMVU.[/color]"
        else:
            self.ids.resultado.text = fmt_checagem(res)

    def toggle_monitor(self):
        if self._monitor_event is not None:
            self._monitor_event.cancel()
            self._monitor_event = None
            self.ids.btn_monitor.text = "MONITORAR"
            return
        nick = (self.ids.nick_field.text or "").strip()
        if not nick:
            self.ids.resultado.text = f"[color={AMARELO}]Digite um nick para monitorar.[/color]"
            return
        try:
            intervalo = float((self.ids.intervalo_field.text or "30").replace(",", "."))
        except ValueError:
            intervalo = 30.0
        intervalo = min(600.0, max(15.0, intervalo))
        self.ids.btn_monitor.text = "PARAR"
        self.checar_nick()
        self._monitor_event = Clock.schedule_interval(lambda dt: self.checar_nick(), intervalo)

    def buscar_sala(self):
        rid = (self.ids.sala_field.text or "").strip()
        if not rid:
            self.ids.sala_result.text = f"[color={AMARELO}]Cole o ID da sala (ex: 12345678-1).[/color]"
            return
        self.ids.sala_result.text = "Buscando..."
        threading.Thread(target=self._t_sala, args=(rid,), daemon=True).start()

    def _t_sala(self, rid):
        try:
            info = self.cliente.buscar_sala(rid)
            erro = None
        except Exception as e:
            info, erro = None, str(e)
        Clock.schedule_once(lambda dt: self._mostrar_sala(info, erro), 0)

    def _mostrar_sala(self, info, erro):
        if erro:
            self.ids.sala_result.text = f"[color={VERMELHO}]Erro: {_limpa(erro)}[/color]"
        else:
            self.ids.sala_result.text = fmt_sala(info)

    def fazer_login(self):
        usuario = (self.ids.login_user.text or "").strip()
        senha = self.ids.login_pass.text or ""
        codigo = (self.ids.login_code.text or "").strip()
        if not usuario or not senha:
            self.ids.conta_result.text = f"[color={AMARELO}]Preencha usuario e senha.[/color]"
            return
        self.ids.conta_result.text = "Entrando..."
        threading.Thread(target=self._t_login, args=(usuario, senha, codigo), daemon=True).start()

    def _t_login(self, usuario, senha, codigo):
        try:
            ok, msg = self.cliente.login(usuario, senha, codigo)
        except Exception as e:
            ok, msg = False, f"falha: {e}"
        Clock.schedule_once(lambda dt: self._mostrar_login(ok, msg), 0)

    def _mostrar_login(self, ok, msg):
        if ok:
            self.ids.conta_result.text = f"[color={VERDE}]{_limpa(msg)}[/color]"
        elif str(msg).startswith("2FA"):
            self.ids.conta_result.text = (
                f"[color={AMARELO}]O IMVU pediu codigo de verificacao. "
                "Preencha o campo 2FA e toque em ENTRAR de novo.[/color]"
            )
        else:
            self.ids.conta_result.text = f"[color={VERMELHO}]{_limpa(msg)}[/color]"

    def sair_conta(self):
        self.cliente = ClienteIMVU()
        self.ids.conta_result.text = "sessao: anonimo"

    def verificar_presente(self):
        if "SEU-BACKEND-AQUI" in PRESENTE_URL:
            self.ids.presente_result.text = (
                f"[color={AMARELO}]O servidor do presente ainda nao foi configurado "
                "pelo dono do app.[/color]"
            )
            return
        nick = (self.ids.presente_nick.text or "").strip()
        if not nick:
            self.ids.presente_result.text = f"[color={AMARELO}]Digite o nick primeiro.[/color]"
            return
        if self._presente_ocupado:
            return
        self._presente_ocupado = True
        self.ids.presente_spinner.active = True
        self.ids.presente_result.text = "Consultando nick e wishlist..."
        threading.Thread(target=self._t_verificar, args=(nick,), daemon=True).start()

    def _t_verificar(self, nick):
        try:
            url = PRESENTE_URL.rstrip("/") + "/checar?" + urllib.parse.urlencode({"nick": nick})
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                js = json.loads(resp.read().decode("utf-8", errors="replace"))
            erro = None
        except urllib.error.HTTPError as e:
            try:
                js = json.loads(e.read().decode("utf-8", errors="replace"))
            except Exception:
                js = {}
            erro = (js or {}).get("erro") or f"HTTP {e.code}"
        except Exception as e:
            js, erro = None, f"falha de rede: {e}"
        Clock.schedule_once(lambda dt: self._mostrar_verificar(js, erro), 0)

    def _mostrar_verificar(self, js, erro):
        self._presente_ocupado = False
        self.ids.presente_spinner.active = False
        if erro:
            self.ids.presente_result.text = f"[color={VERMELHO}]{_limpa(erro)}[/color]"
            return
        linhas = [f"[b]{_limpa(js.get('nick'))}[/b]  (user-{_limpa(js.get('cid'))})"]
        if js.get("ja_recebeu"):
            quando = str(js.get("quando") or "")[:16].replace("T", " ")
            volta = _limpa(js.get("volta_em") or "")
            extra = f" Pode receber de novo {volta}." if volta else " Pode receber de novo depois de 24h."
            linhas.append(
                f"[color={AMARELO}]Esse avatar ja recebeu os 100 creditos"
                + (f" ({quando})" if quando else "") + "." + extra + "[/color]"
            )
        elif js.get("apto") is False:
            motivo = _limpa(js.get("motivo") or "conta nao elegivel")
            linhas.append(f"[color={VERMELHO}]Nao pode receber agora: {motivo}.[/color]")
        else:
            linhas.append(f"[color={VERDE}]Pode resgatar 100 creditos agora![/color]")
        wishlist = js.get("wishlist") or {}
        if wishlist.get("disponivel"):
            itens = wishlist.get("itens") or []
            nomes = []
            for item in itens:
                nome = _limpa(item.get("nome") or "item")
                preco = item.get("preco")
                nomes.append(f"{nome} ({preco} cr)" if preco else nome)
            linhas.append(
                f"Wishlist ({wishlist.get('total', len(itens))} itens): "
                + (", ".join(nomes) if nomes else "vazia")
            )
        else:
            linhas.append("[i]Wishlist: indisponivel agora.[/i]")
        if js.get("restantes_hoje") is not None:
            linhas.append(f"[i]Envios restantes hoje: {js.get('restantes_hoje')}[/i]")
        self.ids.presente_result.text = "\n".join(linhas)

    def pegar_presente(self):
        if "SEU-BACKEND-AQUI" in PRESENTE_URL:
            self.ids.presente_result.text = (
                f"[color={AMARELO}]O servidor do presente ainda nao foi configurado "
                "pelo dono do app.[/color]"
            )
            return
        nick = (self.ids.presente_nick.text or "").strip()
        if not nick:
            self.ids.presente_result.text = f"[color={AMARELO}]Digite seu nick primeiro.[/color]"
            return
        if not self.ids.presente_confirmo.active:
            self.ids.presente_result.text = (
                f"[color={AMARELO}]Marque a caixinha confirmando que o nick e a sua "
                "conta no IMVU antes de resgatar.[/color]"
            )
            return
        if self._presente_ocupado:
            return
        self._presente_ocupado = True
        self.ids.presente_spinner.active = True
        self.ids.presente_result.text = "Enviando 100 creditos..."
        mensagem = (self.ids.presente_msg.text or "").strip()
        threading.Thread(target=self._t_presente, args=(nick, mensagem), daemon=True).start()

    def _t_presente(self, nick, mensagem):
        try:
            corpo = json.dumps({
                "nick": nick,
                "mensagem": mensagem,
                "aparelho": _id_aparelho(),
            }).encode("utf-8")
            req = urllib.request.Request(
                PRESENTE_URL.rstrip("/") + "/gift",
                data=corpo,
                headers={"User-Agent": UA, "Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=40, context=_SSL_CTX) as resp:
                js = json.loads(resp.read().decode("utf-8", errors="replace"))
            erro = None
        except urllib.error.HTTPError as e:
            try:
                js = json.loads(e.read().decode("utf-8", errors="replace"))
            except Exception:
                js = {}
            erro = (js or {}).get("erro") or f"HTTP {e.code}"
        except Exception as e:
            js, erro = None, f"falha de rede: {e}"
        Clock.schedule_once(lambda dt: self._mostrar_presente(js, erro), 0)

    def _mostrar_presente(self, js, erro):
        self._presente_ocupado = False
        self.ids.presente_spinner.active = False
        if erro:
            self.ids.presente_result.text = f"[color={VERMELHO}]{_limpa(erro)}[/color]"
        elif js and js.get("ok"):
            self.ids.presente_result.text = (
                f"[color={VERDE}][b]100 creditos enviados![/b][/color]\n"
                "Confira sua carteira (Wallet) no IMVU. Aproveite!"
            )
        else:
            self.ids.presente_result.text = (
                f"[color={AMARELO}]Nao deu agora. Tente de novo mais tarde.[/color]"
            )

    def verificar_update(self, manual=False):
        self._manual_update = bool(manual)
        self.ids.update_titulo.text = "Verificando..."
        self.ids.update_notas.text = ""
        threading.Thread(target=self._t_update, daemon=True).start()

    def _t_update(self):
        try:
            req = urllib.request.Request(
                RELEASES_API,
                headers={"User-Agent": UA, "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
                js = json.loads(resp.read().decode("utf-8", errors="replace"))
            erro = None
        except Exception as e:
            js, erro = None, str(e)
        Clock.schedule_once(lambda dt: self._mostrar_update(js, erro), 0)

    def _mostrar_update(self, js, erro):
        if erro or not isinstance(js, dict):
            if self._manual_update:
                self.ids.update_titulo.text = (
                    f"[color={VERMELHO}]Nao foi possivel verificar agora: "
                    f"{_limpa(str(erro))[:120]}[/color]"
                )
            else:
                self.ids.update_titulo.text = (
                    "Toque em VERIFICAR ATUALIZACAO para ver se ha versao nova."
                )
            return
        tag = str(js.get("tag_name") or "").strip() or f"v{APP_VERSAO}"
        novo = _versao_tupla(tag) > _versao_tupla(APP_VERSAO)
        self._update_url = None
        for a in js.get("assets") or []:
            u = str(a.get("browser_download_url") or "")
            if u.lower().endswith(".apk"):
                self._update_url = u
        if novo:
            self.ids.update_titulo.text = (
                f"[color={AMARELO}][b]Atualizacao disponivel: {_limpa(tag)}[/b][/color]  "
                f"(instalada: v{APP_VERSAO})"
            )
            self.ids.update_notas.text = (js.get("body") or "").strip() or "Sem notas nesta versao."
            self.ids.btn_baixar.opacity = 1
            self.ids.btn_baixar.disabled = not bool(self._update_url)
        else:
            self.ids.update_titulo.text = (
                f"[color={VERDE}]Voce esta na versao mais recente (v{APP_VERSAO}).[/color]"
            )
            self.ids.update_notas.text = ""
            self.ids.btn_baixar.opacity = 0
            self.ids.btn_baixar.disabled = True

    def abrir_download(self):
        if not self._update_url:
            return
        try:
            from android import open_url
            open_url(self._update_url)
            return
        except Exception:
            pass
        try:
            webbrowser.open(self._update_url)
        except Exception:
            self.ids.update_notas.text = self._update_url


class IMVUStatusApp(MDApp):
    def build(self):
        self.title = "IMVU Status"
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Teal"
        self.theme_cls.accent_palette = "Cyan"
        Builder.load_string(KV)
        return MainScreen()

    def on_start(self):
        try:
            from kivy.core.window import Window
            Window.softinput_mode = "below_target"
        except Exception:
            pass
        try:
            self.root.ids.app_versao.text = f"Versao instalada: [b]v{APP_VERSAO}[/b]"
        except Exception:
            pass
        Clock.schedule_once(lambda dt: self.root.verificar_update(False), 3)


if __name__ == "__main__":
    IMVUStatusApp().run()
