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

API = "https://api.imvu.com"
UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36")

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
"""


class MainScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cliente = ClienteIMVU()
        self._monitor_event = None
        self._ocupado = False

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
        Clock.schedule_once(lambda dt: self._mostrar_checagem(res, erro), 0)

    def _mostrar_checagem(self, res, erro):
        self._ocupado = False
        self.ids.spinner.active = False
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


if __name__ == "__main__":
    IMVUStatusApp().run()
