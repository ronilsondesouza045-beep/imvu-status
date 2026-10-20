# -*- coding: utf-8 -*-
import datetime
import json
import os
import secrets
import threading
import time
import webbrowser
from urllib.parse import quote

import requests
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.image import Image
from kivy.uix.screenmanager import NoTransition, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.utils import escape_markup, platform

from kivymd.app import MDApp
from kivymd.uix.bottomnavigation import MDBottomNavigation, MDBottomNavigationItem
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDFlatButton, MDIconButton, MDRaisedButton
from kivymd.uix.card import MDCard
from kivymd.uix.label import MDLabel
from kivymd.uix.screen import MDScreen
from kivymd.uix.selectioncontrol import MDSwitch
from kivymd.uix.snackbar import Snackbar
from kivymd.uix.textfield import MDTextField
from kivymd.uix.toolbar import MDTopAppBar

APP_VERSAO = "3.0.0"
DEFAULT_API = os.environ.get("IMVU_API_URL", "https://imvu-status.onrender.com").rstrip("/")
NOME_ARQUIVO = "config.json"

FUNDO = (0.027, 0.043, 0.071, 1)
CARTAO = (0.045, 0.075, 0.118, 1)
CARTAO_CLARO = (0.063, 0.102, 0.157, 1)
TEXTO = (0.92, 0.95, 0.98, 1)
MUTED = (0.55, 0.66, 0.76, 1)
MUTED_ESCURO = (0.38, 0.48, 0.58, 1)
ACCENT = (0.18, 0.83, 0.75, 1)
OK = (0.24, 0.84, 0.62, 1)
ERRO = (0.97, 0.45, 0.45, 1)
AVISO = (0.98, 0.75, 0.35, 1)

TIPOS = {
    "online": "entrou online",
    "offline": "ficou offline",
    "sala": "mudou de sala",
    "look": "trocou de look",
    "disponibilidade": "mudou a disponibilidade",
    "suspeita": "possivel modo offline",
}


def formatar_data(valor):
    if not valor:
        return "--"
    try:
        texto = str(valor).replace("Z", "+00:00")
        momento = datetime.datetime.fromisoformat(texto)
        if momento.tzinfo is not None:
            momento = momento.astimezone()
        return momento.strftime("%d/%m %H:%M")
    except Exception:
        return str(valor)


def formatar_minutos(valor):
    total = int(valor or 0)
    if total < 60:
        return f"{total} min"
    horas, resto = divmod(total, 60)
    return f"{horas}h{(' ' + str(resto) + 'min') if resto else ''}"


def formatar_previsao(previsao):
    if not previsao or not previsao.get("proxima"):
        return "Sem dados suficientes para prever."
    confianca = int(round(float(previsao.get("confianca") or 0) * 100))
    return f"Provavel online em {formatar_data(previsao.get('proxima'))} (confianca {confianca}%)"


class ApiError(Exception):
    pass


class Api:
    def __init__(self, base):
        self.base = (base or DEFAULT_API).rstrip("/")

    def _pedir(self, metodo, caminho, params=None, corpo=None):
        try:
            resposta = requests.request(
                metodo,
                self.base + caminho,
                params=params,
                json=corpo,
                timeout=25,
                headers={"Accept": "application/json", "User-Agent": "IMVUStatus/" + APP_VERSAO},
            )
        except requests.RequestException as exc:
            raise ApiError("sem conexao com o servidor; verifique a internet e a URL da API") from exc
        if resposta.status_code >= 400:
            detalhe = None
            try:
                dados = resposta.json()
                detalhe = dados.get("detail") or dados.get("erro")
            except Exception:
                detalhe = None
            if isinstance(detalhe, list) and detalhe:
                detalhe = detalhe[0].get("msg") if isinstance(detalhe[0], dict) else str(detalhe[0])
            raise ApiError(str(detalhe) if detalhe else f"o servidor respondeu {resposta.status_code}")
        try:
            return resposta.json()
        except Exception as exc:
            raise ApiError("resposta invalida do servidor") from exc

    def health(self):
        return self._pedir("GET", "/api/health")

    def config(self):
        return self._pedir("GET", "/api/config")

    def status(self, nick):
        return self._pedir("GET", "/api/status/" + quote(nick, safe=""))

    def historico(self, nick, horas=168):
        return self._pedir("GET", "/api/avatar/" + quote(nick, safe="") + "/history", params={"horas": horas})

    def looks(self, nick):
        return self._pedir("GET", "/api/avatar/" + quote(nick, safe="") + "/looks")

    def salas(self, horas=24, limite=20):
        return self._pedir("GET", "/api/rooms/top", params={"horas": horas, "limite": limite})

    def sala(self, rid, horas=24):
        return self._pedir("GET", "/api/rooms/" + quote(str(rid), safe="") + "/history", params={"horas": horas})

    def diretorio(self, letra):
        return self._pedir("GET", "/api/rooms/directory", params={"letra": letra})

    def watch(self, device):
        return self._pedir("GET", "/api/watch", params={"device": device})

    def adicionar_watch(self, device, nick):
        return self._pedir("POST", "/api/watch", corpo={"device": device, "nick": nick, "notificar": True})

    def remover_watch(self, device, nick):
        return self._pedir("DELETE", "/api/watch/" + quote(nick, safe=""), params={"device": device})

    def alertas(self, device):
        return self._pedir("GET", "/api/alerts", params={"device": device, "limite": 50})

    def marcar_vistos(self, device, ids):
        return self._pedir("POST", "/api/alerts/seen", corpo={"device": device, "ids": ids})


class Live(threading.Thread):
    def __init__(self, api, device, ao_evento):
        super().__init__(daemon=True)
        self.api = api
        self.device = device
        self.ao_evento = ao_evento
        self.parar_flag = threading.Event()

    def parar(self):
        self.parar_flag.set()

    def run(self):
        while not self.parar_flag.is_set():
            try:
                resposta = requests.get(
                    self.api.base + "/api/events",
                    params={"device": self.device},
                    stream=True,
                    timeout=(10, 60),
                    headers={"Accept": "text/event-stream", "User-Agent": "IMVUStatus/" + APP_VERSAO},
                )
                with resposta:
                    if resposta.status_code == 200:
                        for linha in resposta.iter_lines(decode_unicode=True):
                            if self.parar_flag.is_set():
                                return
                            if not linha or not linha.startswith("data:"):
                                continue
                            bruto = linha[5:].strip()
                            if not bruto:
                                continue
                            try:
                                evento = json.loads(bruto)
                            except Exception:
                                continue
                            Clock.schedule_once(lambda dt, e=evento: self.ao_evento(e), 0)
            except Exception:
                pass
            if not self.parar_flag.is_set():
                time.sleep(4)


class CartaoClicavel(MDCard, ButtonBehavior):
    pass


class GraficoBarras(Widget):
    def __init__(self, valores=None, cor=ACCENT, **kwargs):
        super().__init__(**kwargs)
        self.valores = list(valores or [])
        self.cor = cor
        self.bind(pos=self._desenhar, size=self._desenhar)

    def definir(self, valores, cor=None):
        self.valores = list(valores or [])
        if cor is not None:
            self.cor = cor
        self._desenhar()

    def _desenhar(self, *_):
        self.canvas.clear()
        if not self.valores:
            return
        maximo = max(self.valores) or 1
        quantidade = len(self.valores)
        espaco = dp(2)
        largura = max(dp(2), (self.width - espaco * (quantidade + 1)) / quantidade)
        altura_util = max(dp(10), self.height - dp(6))
        with self.canvas:
            Color(0.12, 0.17, 0.24, 1)
            Rectangle(pos=(self.x, self.y), size=(self.width, dp(1)))
            Color(*self.cor)
            for indice, valor in enumerate(self.valores):
                altura = max(dp(2), (float(valor or 0) / maximo) * altura_util)
                x = self.x + espaco + indice * (largura + espaco)
                Rectangle(pos=(x, self.y), size=(largura, altura))


class ImvuStatusApp(MDApp):
    def build(self):
        self.title = "IMVU Status"
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Teal"
        self.theme_cls.accent_palette = "Cyan"
        Window.clearcolor = FUNDO

        self.caminho_config = os.path.join(self.user_data_dir, NOME_ARQUIVO)
        self.config = self._ler_config()
        if not self.config.get("device"):
            self.config["device"] = secrets.token_hex(16)
        self.device = self.config["device"]
        self.api = Api(self.config.get("api") or DEFAULT_API)
        self.nick = None
        self.status = None
        self.watch = []
        self.alertas = []
        self.live = None
        self._webview = None
        self._webview_raiz = None
        self._webview_listeners = []

        self._salvar_config()

        raiz = MDBoxLayout(orientation="vertical")
        self.topbar = MDTopAppBar(
            title="IMVU Status",
            elevation=2,
            md_bg_color=FUNDO,
            right_action_items=[
                ["web", lambda _x: self.abrir_painel()],
                ["refresh", lambda _x: self.atualizar_tudo()],
                ["cog", lambda _x: self.abrir_config()],
            ],
        )
        self.sm = ScreenManager(transition=NoTransition())
        principal = MDScreen(name="principal")
        self.nav = MDBottomNavigation(panel_color=FUNDO)
        self.item_inicio = MDBottomNavigationItem(name="inicio", text="Inicio", icon="home")
        self.item_salas = MDBottomNavigationItem(name="salas", text="Salas", icon="magnify")
        self.item_historico = MDBottomNavigationItem(name="historico", text="Historico", icon="chart-bar")
        self.item_alertas = MDBottomNavigationItem(name="alertas", text="Alertas", icon="bell")
        self.item_inicio.add_widget(self._tela_inicio())
        self.item_salas.add_widget(self._tela_salas())
        self.item_historico.add_widget(self._tela_historico())
        self.item_alertas.add_widget(self._tela_alertas())
        for item in (self.item_inicio, self.item_salas, self.item_historico, self.item_alertas):
            self.nav.add_widget(item)
        principal.add_widget(self.nav)
        self.sm.add_widget(principal)
        self.sm.add_widget(self._tela_config())
        raiz.add_widget(self.topbar)
        raiz.add_widget(self.sm)
        Window.bind(on_keyboard=self._ao_tecla_painel)
        return raiz

    @property
    def watched(self):
        return {str(item.get("nick", "")).lower() for item in self.watch}

    def _ler_config(self):
        try:
            with open(self.caminho_config, "r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
            return dados if isinstance(dados, dict) else {}
        except Exception:
            return {}

    def _salvar_config(self):
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            with open(self.caminho_config, "w", encoding="utf-8") as arquivo:
                json.dump(self.config, arquivo, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def paragrafo(self, texto="", cor=TEXTO, tamanho=None, markup=False, negrito=False):
        rotulo = MDLabel(text=texto, markup=markup, size_hint_y=None, height=dp(20))
        rotulo.theme_text_color = "Custom"
        rotulo.text_color = cor
        if tamanho:
            rotulo.font_size = tamanho
        if negrito:
            rotulo.bold = True
        rotulo.bind(width=lambda inst, largura: setattr(inst, "text_size", (largura, None)))
        rotulo.bind(texture_size=lambda inst, tamanho_texto: setattr(inst, "height", tamanho_texto[1] + dp(2)))
        return rotulo

    def titulo(self, texto):
        rotulo = self.paragrafo(texto, cor=TEXTO, tamanho="18sp", negrito=True)
        return rotulo

    def novo_cartao(self, clicavel=False, **extra):
        opcoes = dict(
            orientation="vertical",
            padding=dp(14),
            spacing=dp(8),
            size_hint_y=None,
            radius=[dp(14)],
            elevation=0,
            md_bg_color=CARTAO,
        )
        opcoes.update(extra)
        classe = CartaoClicavel if clicavel else MDCard
        cartao = classe(**opcoes)
        cartao.bind(minimum_height=cartao.setter("height"))
        return cartao

    def novo_conteudo(self):
        rolagem = ScrollView(do_scroll_x=False, bar_width=dp(2))
        caixa = MDBoxLayout(orientation="vertical", size_hint_y=None, padding=dp(14), spacing=dp(12))
        caixa.bind(minimum_height=caixa.setter("height"))
        rolagem.add_widget(caixa)
        return rolagem, caixa

    def botao(self, texto, acao, levantado=False, largura=None, icone=None):
        classe = MDRaisedButton if levantado else MDFlatButton
        opcoes = dict(text=texto, size_hint=(None, None), height=dp(42))
        if icone:
            opcoes["icon"] = icone
        if largura:
            opcoes["width"] = dp(largura)
        botao = classe(**opcoes)
        botao.bind(on_release=lambda _inst: acao())
        return botao

    def aviso(self, texto, erro=False):
        mensagem = str(texto)
        try:
            Snackbar(text=mensagem, duration=3).open()
        except Exception:
            print(mensagem)

    def em_segundo_plano(self, funcao, ao_concluir, ao_falhar=None):
        def executar():
            try:
                resultado = funcao()
            except Exception as exc:
                mensagem = str(exc) or "falha inesperada"
                if ao_falhar is not None:
                    Clock.schedule_once(lambda dt, m=mensagem: ao_falhar(m), 0)
                else:
                    Clock.schedule_once(lambda dt, m=mensagem: self.aviso(m, True), 0)
            else:
                Clock.schedule_once(lambda dt, r=resultado: ao_concluir(r), 0)

        threading.Thread(target=executar, daemon=True).start()

    def _tela_inicio(self):
        rolagem, caixa = self.novo_conteudo()

        cartao_busca = self.novo_cartao()
        cartao_busca.add_widget(self.titulo("Consultar avatar"))
        cartao_busca.add_widget(
            self.paragrafo(
                "Status publico em tempo real, sala exposta, previsao de horarios e acompanhamento.",
                cor=MUTED,
            )
        )
        linha = MDBoxLayout(
            orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(54)
        )
        self.campo_nick = MDTextField(
            hint_text="nick do IMVU",
            mode="rectangle",
            size_hint_x=1,
            size_hint_y=None,
            height=dp(48),
            on_text_validate=lambda _inst: self.consultar_nick(),
        )
        linha.add_widget(self.campo_nick)
        linha.add_widget(self.botao("Consultar", self.consultar_nick, levantado=True, largura=110))
        cartao_busca.add_widget(linha)
        caixa.add_widget(cartao_busca)

        self.card_status = self.novo_cartao()
        topo = MDBoxLayout(orientation="horizontal", spacing=dp(12), size_hint_y=None)
        self.img_status = Image(
            source="assets/icon.png",
            size_hint=(None, None),
            size=(dp(84), dp(84)),
            fit_mode="cover",
        )
        info = MDBoxLayout(orientation="vertical", spacing=dp(3), size_hint_y=None)
        info.bind(minimum_height=info.setter("height"))
        self.lbl_status_nome = self.paragrafo("Nenhum avatar consultado", cor=TEXTO, tamanho="17sp", negrito=True)
        self.lbl_status_nick = self.paragrafo("", cor=MUTED)
        self.lbl_status_estado = self.paragrafo("", cor=MUTED, tamanho="13sp", negrito=True)
        info.add_widget(self.lbl_status_nome)
        info.add_widget(self.lbl_status_nick)
        info.add_widget(self.lbl_status_estado)
        topo.add_widget(self.img_status)
        topo.add_widget(info)
        topo.bind(minimum_height=topo.setter("height"))
        self.card_status.add_widget(topo)
        self.lbl_status_sala = self.paragrafo("", cor=TEXTO)
        self.lbl_status_detalhe = self.paragrafo("", cor=MUTED)
        self.lbl_status_previsao = self.paragrafo("", cor=ACCENT)
        self.card_status.add_widget(self.lbl_status_sala)
        self.card_status.add_widget(self.lbl_status_detalhe)
        self.card_status.add_widget(self.lbl_status_previsao)
        botoes = MDBoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(44))
        self.btn_historico = self.botao("Ver historico", self.ir_para_historico)
        self.btn_watch = self.botao("Acompanhar", self.alternar_watch, levantado=True)
        botoes.add_widget(self.btn_historico)
        botoes.add_widget(self.btn_watch)
        self.card_status.add_widget(botoes)
        caixa.add_widget(self.card_status)

        card_feed = self.novo_cartao()
        card_feed.add_widget(self.titulo("Ao vivo"))
        card_feed.add_widget(
            self.paragrafo("Eventos recebidos do observatorio em tempo real.", cor=MUTED)
        )
        self.caixa_feed = MDBoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None)
        self.caixa_feed.bind(minimum_height=self.caixa_feed.setter("height"))
        self.caixa_feed.add_widget(self.paragrafo("Aguardando eventos...", cor=MUTED_ESCURO))
        card_feed.add_widget(self.caixa_feed)
        caixa.add_widget(card_feed)

        return rolagem

    def _tela_salas(self):
        rolagem, caixa = self.novo_conteudo()

        card_topo = self.novo_cartao()
        card_topo.add_widget(self.titulo("Salas publicas"))
        card_topo.add_widget(
            self.paragrafo("Ranking por ocupacao media nas ultimas 24 horas.", cor=MUTED)
        )
        card_topo.add_widget(
            self.botao("Atualizar ranking", self.carregar_salas, levantado=True, largura=160)
        )
        caixa.add_widget(card_topo)

        card_lista = self.novo_cartao()
        self.lista_salas = MDBoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.lista_salas.bind(minimum_height=self.lista_salas.setter("height"))
        self.lista_salas.add_widget(self.paragrafo("Carregando salas...", cor=MUTED_ESCURO))
        card_lista.add_widget(self.lista_salas)
        caixa.add_widget(card_lista)

        card_consulta = self.novo_cartao()
        card_consulta.add_widget(self.titulo("Consultar sala"))
        linha = MDBoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(54))
        self.campo_sala = MDTextField(
            hint_text="ID da sala (ex: 12345678-1)",
            mode="rectangle",
            size_hint_x=1,
            size_hint_y=None,
            height=dp(48),
            on_text_validate=lambda _inst: self.consultar_sala(),
        )
        linha.add_widget(self.campo_sala)
        linha.add_widget(self.botao("Ver", self.consultar_sala, levantado=True, largura=70))
        card_consulta.add_widget(linha)
        self.lbl_sala_resumo = self.paragrafo("", cor=TEXTO)
        self.lbl_sala_extra = self.paragrafo("", cor=MUTED)
        card_consulta.add_widget(self.lbl_sala_resumo)
        card_consulta.add_widget(self.lbl_sala_extra)
        self.grafico_sala = GraficoBarras(size_hint_y=None, height=dp(110), cor=ACCENT)
        card_consulta.add_widget(self.grafico_sala)
        self.lbl_sala_amostras = self.paragrafo("", cor=MUTED_ESCURO)
        card_consulta.add_widget(self.lbl_sala_amostras)
        caixa.add_widget(card_consulta)

        card_diretorio = self.novo_cartao()
        card_diretorio.add_widget(self.titulo("Diretorio publico"))
        linha = MDBoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(54))
        self.campo_letra = MDTextField(
            hint_text="letra inicial (A-Z ou 0-9)",
            mode="rectangle",
            size_hint_x=1,
            size_hint_y=None,
            height=dp(48),
            on_text_validate=lambda _inst: self.carregar_diretorio(),
        )
        linha.add_widget(self.campo_letra)
        linha.add_widget(self.botao("Buscar", self.carregar_diretorio, levantado=True, largura=90))
        card_diretorio.add_widget(linha)
        self.lista_diretorio = MDBoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
        self.lista_diretorio.bind(minimum_height=self.lista_diretorio.setter("height"))
        card_diretorio.add_widget(self.lista_diretorio)
        caixa.add_widget(card_diretorio)

        return rolagem

    def _tela_historico(self):
        rolagem, caixa = self.novo_conteudo()

        self.lbl_hist_nome = self.titulo("Historico do avatar")
        caixa.add_widget(self.lbl_hist_nome)

        card_resumo = self.novo_cartao()
        card_resumo.add_widget(self.titulo("Resumo"))
        self.lbl_hist_resumo = self.paragrafo(
            "Consulte um nick na aba Inicio para carregar o historico.", cor=MUTED
        )
        card_resumo.add_widget(self.lbl_hist_resumo)
        caixa.add_widget(card_resumo)

        card_dias = self.novo_cartao()
        card_dias.add_widget(self.titulo("Presenca por dia"))
        self.lbl_dias_periodo = self.paragrafo("", cor=MUTED)
        card_dias.add_widget(self.lbl_dias_periodo)
        self.grafico_dias = GraficoBarras(size_hint_y=None, height=dp(120), cor=ACCENT)
        card_dias.add_widget(self.grafico_dias)
        caixa.add_widget(card_dias)

        card_horas = self.novo_cartao()
        card_horas.add_widget(self.titulo("Atividade por hora"))
        self.grafico_horas = GraficoBarras(size_hint_y=None, height=dp(100), cor=(0.22, 0.72, 0.97, 1))
        card_horas.add_widget(self.grafico_horas)
        self.lbl_previsao_hist = self.paragrafo("", cor=ACCENT)
        card_horas.add_widget(self.lbl_previsao_hist)
        caixa.add_widget(card_horas)

        card_tempo = self.novo_cartao()
        card_tempo.add_widget(self.titulo("Linha do tempo"))
        self.lista_timeline = MDBoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None)
        self.lista_timeline.bind(minimum_height=self.lista_timeline.setter("height"))
        self.lista_timeline.add_widget(self.paragrafo("Sem eventos.", cor=MUTED_ESCURO))
        card_tempo.add_widget(self.lista_timeline)
        caixa.add_widget(card_tempo)

        card_looks = self.novo_cartao()
        card_looks.add_widget(self.titulo("Galeria de looks"))
        self.caixa_looks = MDBoxLayout(
            orientation="horizontal", spacing=dp(8), size_hint=(None, None), height=dp(190)
        )
        self.caixa_looks.bind(minimum_width=self.caixa_looks.setter("width"))
        rolagem_looks = ScrollView(do_scroll_y=False, size_hint_y=None, height=dp(190))
        rolagem_looks.add_widget(self.caixa_looks)
        self.rolagem_looks = rolagem_looks
        card_looks.add_widget(rolagem_looks)
        self.caixa_looks.add_widget(self.paragrafo("Sem looks registrados.", cor=MUTED_ESCURO))
        caixa.add_widget(card_looks)

        card_botao = self.novo_cartao()
        card_botao.add_widget(
            self.botao("Atualizar historico", self.recarregar_historico, levantado=True, largura=180)
        )
        caixa.add_widget(card_botao)

        return rolagem

    def _tela_alertas(self):
        rolagem, caixa = self.novo_conteudo()

        card_watch = self.novo_cartao()
        card_watch.add_widget(self.titulo("Minha lista"))
        card_watch.add_widget(
            self.paragrafo("Avatares acompanhados recebem alertas quando entram online.", cor=MUTED)
        )
        self.lista_watch = MDBoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.lista_watch.bind(minimum_height=self.lista_watch.setter("height"))
        self.lista_watch.add_widget(self.paragrafo("Nenhum avatar na lista.", cor=MUTED_ESCURO))
        card_watch.add_widget(self.lista_watch)
        caixa.add_widget(card_watch)

        card_alertas = self.novo_cartao()
        linha = MDBoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(44))
        linha.add_widget(self.titulo("Ultimos alertas"))
        linha.add_widget(self.botao("Marcar vistos", self.marcar_vistos, largura=130))
        card_alertas.add_widget(linha)
        self.lista_alertas = MDBoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.lista_alertas.bind(minimum_height=self.lista_alertas.setter("height"))
        self.lista_alertas.add_widget(self.paragrafo("Sem alertas.", cor=MUTED_ESCURO))
        card_alertas.add_widget(self.lista_alertas)
        caixa.add_widget(card_alertas)

        card_botao = self.novo_cartao()
        card_botao.add_widget(
            self.botao("Atualizar alertas", self.carregar_alertas, levantado=True, largura=160)
        )
        caixa.add_widget(card_botao)

        return rolagem

    def _tela_config(self):
        tela = MDScreen(name="config")
        rolagem, caixa = self.novo_conteudo()

        card_api = self.novo_cartao()
        card_api.add_widget(self.titulo("Servidor"))
        card_api.add_widget(
            self.paragrafo("Endereco do backend que hospeda a API e o painel web.", cor=MUTED)
        )
        self.campo_api = MDTextField(
            text=self.api.base,
            hint_text="https://seu-servidor.onrender.com",
            mode="rectangle",
            size_hint_y=None,
            height=dp(52),
        )
        card_api.add_widget(self.campo_api)
        linha = MDBoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(44))
        linha.add_widget(self.botao("Testar", self.testar_conexao, largura=90))
        linha.add_widget(self.botao("Salvar", self.salvar_config, levantado=True, largura=90))
        linha.add_widget(self.botao("Abrir painel", self.abrir_painel, largura=120))
        card_api.add_widget(linha)
        caixa.add_widget(card_api)

        card_alertas = self.novo_cartao()
        card_alertas.add_widget(self.titulo("Alertas"))
        linha = MDBoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(40))
        linha.add_widget(self.paragrafo("Notificacoes no aparelho", cor=TEXTO))
        self.switch_notificacoes = MDSwitch()
        self.switch_notificacoes.active = bool(self.config.get("notificacoes", True))
        linha.add_widget(self.switch_notificacoes)
        card_alertas.add_widget(linha)
        card_alertas.add_widget(
            self.paragrafo(
                "Ao ativar, o app pede permissao do Android e mostra avisos locais quando um "
                "avatar acompanhado entra online.",
                cor=MUTED,
            )
        )
        self.lbl_device = self.paragrafo("Aparelho: " + self.device, cor=MUTED_ESCURO)
        card_alertas.add_widget(self.lbl_device)
        caixa.add_widget(card_alertas)

        card_sobre = self.novo_cartao()
        card_sobre.add_widget(self.titulo("Sobre"))
        card_sobre.add_widget(
            self.paragrafo(
                "IMVU Status " + APP_VERSAO + "\n"
                "Observatorio de dados publicos: status, historico, previsao, salas e resumo.",
                cor=MUTED,
            )
        )
        card_sobre.add_widget(
            self.paragrafo(
                "Privacidade: somente informacoes publicas do IMVU, sem rastrear relacoes entre "
                "pessoas. Respeite os termos do IMVU e a LGPD. Nao use para perseguir ou vigiar.",
                cor=MUTED_ESCURO,
            )
        )
        card_sobre.add_widget(
            self.paragrafo(
                "Este aplicativo nao e afiliado ao IMVU. IMVU e uma marca de seus respectivos donos.",
                cor=MUTED_ESCURO,
            )
        )
        caixa.add_widget(card_sobre)

        tela.add_widget(rolagem)
        return tela

    def on_start(self):
        self._pedir_permissoes()
        self.iniciar_live()
        Clock.schedule_once(lambda _dt: self.carregar_inicial(), 0.4)

    def on_stop(self):
        if self.live is not None:
            self.live.parar()
        if self._webview is not None:
            try:
                self._fechar_painel_android()
            except Exception:
                pass

    def _pedir_permissoes(self):
        try:
            from android.permissions import Permission, request_permissions

            permissoes = [Permission.INTERNET]
            notificacao = getattr(Permission, "POST_NOTIFICATIONS", None)
            if notificacao:
                permissoes.append(notificacao)
            request_permissions(permissoes)
        except Exception:
            pass

    def iniciar_live(self):
        if self.live is not None:
            self.live.parar()
        self.live = Live(self.api, self.device, self.ao_evento)
        self.live.start()

    def carregar_inicial(self):
        self.carregar_salas(silencioso=True)
        self.carregar_watch(silencioso=True)
        self.carregar_alertas(silencioso=True)
        ultimo = self.config.get("ultimo_nick")
        if ultimo:
            self.campo_nick.text = ultimo
            self.consultar_nick(silencioso=True)

    def consultar_nick(self, silencioso=False):
        nick = (self.campo_nick.text or "").strip()
        if not nick:
            self.aviso("Digite um nick.", True)
            return
        if not silencioso:
            self.aviso("Consultando " + nick + "...")
        falha = (lambda _m: None) if silencioso else None
        self.em_segundo_plano(
            lambda: self.api.status(nick),
            lambda dados: self.exibir_status(dados, nick),
            falha,
        )

    def exibir_status(self, dados, nick_consultado):
        nick = dados.get("nick") or nick_consultado
        self.nick = nick
        self.status = dados
        self.campo_nick.text = nick
        self.lbl_status_nome.text = dados.get("display_name") or nick
        self.lbl_status_nick.text = "@" + nick + "  -  CID " + str(dados.get("cid") or "?")
        online = bool(dados.get("online"))
        self.lbl_status_estado.text = "ONLINE" if online else "OFFLINE"
        self.lbl_status_estado.text_color = OK if online else ERRO
        sala = dados.get("sala") or {}
        if sala.get("nome"):
            self.lbl_status_sala.text = "Sala: " + str(sala.get("nome")) + " (" + str(sala.get("privacidade") or "?") + ")"
        else:
            self.lbl_status_sala.text = "Sala: nao exposta agora"
        if dados.get("aparente_offline"):
            self.lbl_status_sala.text += "  -  possivel modo offline"
        self.lbl_status_detalhe.text = (
            "Pais: " + str(dados.get("pais") or "?")
            + "  -  Seguidores: " + str(dados.get("seguidores") if dados.get("seguidores") is not None else "?")
            + "  -  Visto: " + formatar_data(dados.get("ultima_vez") or dados.get("atualizado_em"))
        )
        previsao = dados.get("previsao") or {}
        if previsao.get("proxima"):
            self.lbl_status_previsao.text = formatar_previsao(previsao)
        elif previsao.get("aviso"):
            self.lbl_status_previsao.text = str(previsao.get("aviso"))
        else:
            self.lbl_status_previsao.text = "Historico ainda sendo formado."
        imagem = dados.get("foto_perfil") or dados.get("avatar_3d")
        self.img_status.source = imagem or "assets/icon.png"
        self.config["ultimo_nick"] = nick
        self._salvar_config()
        self.atualizar_botao_watch()
        self.em_segundo_plano(
            lambda: self.api.historico(nick),
            lambda historico: self.exibir_historico(historico, nick),
            lambda _m: None,
        )
        self.em_segundo_plano(
            lambda: self.api.looks(nick),
            lambda looks: self.exibir_looks(looks, nick),
            lambda _m: None,
        )

    def atualizar_botao_watch(self):
        if not self.nick:
            self.btn_watch.text = "Acompanhar"
            return
        self.btn_watch.text = "Acompanhando" if self.nick.lower() in self.watched else "Acompanhar"

    def alternar_watch(self):
        if not self.nick:
            self.aviso("Consulte um nick primeiro.", True)
            return
        nick = self.nick
        if nick.lower() in self.watched:
            self.em_segundo_plano(
                lambda: self.api.remover_watch(self.device, nick),
                lambda _r: self._watch_alterado(nick, False),
            )
        else:
            self.em_segundo_plano(
                lambda: self.api.adicionar_watch(self.device, nick),
                lambda _r: self._watch_alterado(nick, True),
            )

    def _watch_alterado(self, nick, adicionado):
        self.aviso(("Acompanhando " if adicionado else "Removido: ") + nick)
        self.carregar_watch()
        self.atualizar_botao_watch()
        if adicionado:
            self._pedir_permissoes()

    def carregar_watch(self, silencioso=False):
        falha = (lambda _m: None) if silencioso else None
        self.em_segundo_plano(
            lambda: self.api.watch(self.device),
            self.exibir_watch,
            falha,
        )

    def exibir_watch(self, dados):
        self.watch = dados.get("avatares") or []
        self.lista_watch.clear_widgets()
        if not self.watch:
            self.lista_watch.add_widget(self.paragrafo("Nenhum avatar na lista.", cor=MUTED_ESCURO))
        for avatar in self.watch:
            nick = str(avatar.get("nick") or "")
            sala = (avatar.get("sala") or {}).get("nome") or "sem sala exposta"
            linha = MDBoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(46))
            coluna = MDBoxLayout(orientation="vertical", spacing=dp(0), size_hint_y=None)
            coluna.bind(minimum_height=coluna.setter("height"))
            coluna.add_widget(self.paragrafo(nick, cor=TEXTO, negrito=True))
            coluna.add_widget(
                self.paragrafo(
                    ("online" if avatar.get("online") else "offline") + "  -  " + str(sala),
                    cor=MUTED,
                )
            )
            linha.add_widget(coluna)
            icone = MDIconButton(
                icon="delete",
                theme_text_color="Custom",
                text_color=MUTED,
                size_hint=(None, None),
                size=(dp(40), dp(40)),
                pos_hint={"center_y": 0.5},
            )
            icone.bind(on_release=lambda _inst, n=nick: self.remover_watch(n))
            linha.add_widget(icone)
            self.lista_watch.add_widget(linha)
        self.atualizar_botao_watch()

    def remover_watch(self, nick):
        self.em_segundo_plano(
            lambda: self.api.remover_watch(self.device, nick),
            lambda _r: self._watch_alterado(nick, False),
        )

    def carregar_alertas(self, silencioso=False):
        falha = (lambda _m: None) if silencioso else None
        self.em_segundo_plano(
            lambda: self.api.alertas(self.device),
            self.exibir_alertas,
            falha,
        )

    def exibir_alertas(self, dados):
        self.alertas = dados.get("alertas") or []
        self.lista_alertas.clear_widgets()
        if not self.alertas:
            self.lista_alertas.add_widget(self.paragrafo("Sem alertas.", cor=MUTED_ESCURO))
            return
        for alerta in self.alertas[:30]:
            item = self.novo_cartao(padding=dp(10), spacing=dp(2), md_bg_color=CARTAO_CLARO)
            titulo = self.paragrafo(
                str(alerta.get("titulo") or "Alerta"),
                cor=TEXTO if not alerta.get("visto") else MUTED,
                negrito=not alerta.get("visto"),
            )
            item.add_widget(titulo)
            corpo = alerta.get("corpo")
            if corpo:
                item.add_widget(self.paragrafo(str(corpo), cor=MUTED))
            item.add_widget(self.paragrafo(formatar_data(alerta.get("quando")), cor=MUTED_ESCURO))
            self.lista_alertas.add_widget(item)

    def marcar_vistos(self):
        ids = [alerta.get("id") for alerta in self.alertas if not alerta.get("visto") and alerta.get("id")]
        if not ids:
            self.aviso("Nada para marcar.")
            return
        self.em_segundo_plano(
            lambda: self.api.marcar_vistos(self.device, ids),
            lambda _r: (self.aviso("Alertas marcados como vistos."), self.carregar_alertas()),
        )

    def carregar_salas(self, silencioso=False):
        if silencioso is False:
            self.aviso("Carregando salas...")
        falha = (lambda _m: None) if silencioso else None
        self.em_segundo_plano(
            lambda: self.api.salas(),
            lambda dados: self.exibir_salas(dados.get("salas") or []),
            falha,
        )

    def exibir_salas(self, salas):
        self.lista_salas.clear_widgets()
        if not salas:
            self.lista_salas.add_widget(
                self.paragrafo("Nenhuma sala monitorada ainda.", cor=MUTED_ESCURO)
            )
            return
        for sala in salas:
            cartao = self.novo_cartao(
                clicavel=True, padding=dp(10), spacing=dp(2), md_bg_color=CARTAO_CLARO
            )
            cartao.add_widget(
                self.paragrafo(
                    str(sala.get("nome") or "Sala"), cor=TEXTO, negrito=True
                )
            )
            cartao.add_widget(
                self.paragrafo(
                    str(sala.get("dono") or "dono desconhecido")
                    + "  -  media " + str(sala.get("media"))
                    + "  -  pico " + str(sala.get("pico")),
                    cor=MUTED,
                )
            )
            cartao.add_widget(
                self.paragrafo(
                    "agora " + str(sala.get("online")) + "  -  tendencia " + str(sala.get("tendencia")),
                    cor=ACCENT,
                )
            )
            rid = sala.get("rid")
            if rid:
                cartao.bind(on_release=lambda _inst, r=rid: self.abrir_sala(r))
            self.lista_salas.add_widget(cartao)

    def abrir_sala(self, rid):
        self.campo_sala.text = str(rid)
        self.aviso("Abrindo sala " + str(rid) + "...")
        self.em_segundo_plano(
            lambda: self.api.sala(rid),
            self.exibir_sala,
        )

    def exibir_sala(self, dados):
        sala = dados.get("sala") or {}
        resumo = dados.get("resumo") or {}
        self.lbl_sala_resumo.text = (
            str(sala.get("nome") or "Sala") + "  (" + str(sala.get("rid") or "") + ")"
        )
        melhor = (resumo.get("melhor_horario") or {}).get("hora")
        self.lbl_sala_extra.text = (
            "Dono: " + str(sala.get("dono") or "?")
            + "  -  capacidade: " + str(sala.get("capacidade") or "?")
            + "  -  agora: " + str(resumo.get("online_agora") or 0)
            + "  -  media: " + str(resumo.get("media") or 0)
            + "  -  pico: " + str(resumo.get("pico") or 0)
            + (("  -  melhor horario: " + str(melhor) + "h") if melhor is not None else "")
        )
        pontos = dados.get("pontos") or []
        self.grafico_sala.definir([ponto.get("online") or 0 for ponto in pontos])
        if pontos:
            self.lbl_sala_amostras.text = (
                str(len(pontos)) + " amostras  -  "
                + formatar_data(pontos[0].get("ts")) + " ate " + formatar_data(pontos[-1].get("ts"))
            )
        else:
            self.lbl_sala_amostras.text = "Sala ainda sem amostras."

    def carregar_diretorio(self):
        letra = (self.campo_letra.text or "A").strip().upper()[:1] or "A"
        if not letra.isalnum():
            self.aviso("Use uma letra ou numero.", True)
            return
        self.campo_letra.text = letra
        self.aviso("Buscando salas com " + letra + "...")
        self.lista_diretorio.clear_widgets()
        self.lista_diretorio.add_widget(self.paragrafo("Consultando o IMVU...", cor=MUTED_ESCURO))
        self.em_segundo_plano(
            lambda: self.api.diretorio(letra),
            self.exibir_diretorio,
        )

    def exibir_diretorio(self, dados):
        self.lista_diretorio.clear_widgets()
        salas = dados.get("salas") or []
        if not salas:
            self.lista_diretorio.add_widget(
                self.paragrafo("Nenhuma sala encontrada.", cor=MUTED_ESCURO)
            )
            return
        for sala in salas:
            rid = sala.get("rid")
            if not rid:
                continue
            cartao = self.novo_cartao(
                clicavel=True, padding=dp(8), spacing=dp(1), md_bg_color=CARTAO_CLARO
            )
            cartao.add_widget(self.paragrafo(str(sala.get("nome") or "Sala"), cor=TEXTO))
            cartao.add_widget(self.paragrafo(str(rid), cor=MUTED))
            cartao.bind(on_release=lambda _inst, r=rid: self.abrir_sala(r))
            self.lista_diretorio.add_widget(cartao)
        self.aviso(str(len(salas)) + " sala(s) encontrada(s).")
        self.carregar_salas(silencioso=True)

    def consultar_sala(self):
        rid = (self.campo_sala.text or "").strip()
        if not rid:
            self.aviso("Informe o ID da sala.", True)
            return
        self.abrir_sala(rid)

    def ir_para_historico(self):
        if not self.nick:
            self.aviso("Consulte um nick primeiro.", True)
            return
        self.trocar_aba("historico")
        self.recarregar_historico()

    def trocar_aba(self, nome):
        try:
            self.nav.switch_tab(nome)
        except Exception:
            pass

    def recarregar_historico(self):
        if not self.nick:
            self.aviso("Consulte um nick primeiro.", True)
            return
        nick = self.nick
        self.em_segundo_plano(
            lambda: self.api.historico(nick),
            lambda dados: self.exibir_historico(dados, nick),
        )
        self.em_segundo_plano(
            lambda: self.api.looks(nick),
            lambda dados: self.exibir_looks(dados, nick),
        )

    def exibir_historico(self, dados, nick):
        if not self.nick or self.nick.lower() != str(nick).lower():
            return
        stats = dados.get("estatisticas") or {}
        self.lbl_hist_nome.text = "Historico de @" + nick
        favorita = stats.get("sala_mais_frequentada") or {}
        linhas = [
            "Tempo online (7 dias): " + formatar_minutos(stats.get("minutos_online")),
            "Recorde online: " + formatar_minutos(stats.get("recorde_online_min")),
            "Recorde offline: " + formatar_minutos(stats.get("recorde_offline_min")),
            "Ultima vez: " + formatar_data(stats.get("ultima_vez") or stats.get("ultimo_offline")),
            "Eventos na janela: " + str(stats.get("eventos") or 0),
        ]
        if favorita.get("nome"):
            linhas.append(
                "Sala favorita: " + str(favorita.get("nome"))
                + " (" + str(favorita.get("vezes")) + " eventos)"
            )
        self.lbl_hist_resumo.text = "\n".join(linhas)

        grafico = dados.get("grafico") or {}
        dias = grafico.get("dias") or []
        self.grafico_dias.definir([dia.get("minutos") or 0 for dia in dias])
        if dias:
            self.lbl_dias_periodo.text = (
                "minutos online por dia  -  " + str(dias[0].get("dia")) + " ate " + str(dias[-1].get("dia"))
            )
        horas = grafico.get("horas") or []
        self.grafico_horas.definir(horas)

        self.lbl_previsao_hist.text = formatar_previsao(dados.get("previsao"))

        self.lista_timeline.clear_widgets()
        eventos = dados.get("eventos") or []
        if not eventos:
            self.lista_timeline.add_widget(self.paragrafo("Sem eventos.", cor=MUTED_ESCURO))
        for evento in eventos[:30]:
            tipo = TIPOS.get(evento.get("tipo"), str(evento.get("tipo") or "evento"))
            sala = evento.get("sala")
            texto = formatar_data(evento.get("ts")) + "  -  " + tipo
            if sala:
                texto += "  -  " + str(sala)
            self.lista_timeline.add_widget(self.paragrafo(texto, cor=MUTED))

    def exibir_looks(self, dados, nick):
        if not self.nick or self.nick.lower() != str(nick).lower():
            return
        self.caixa_looks.clear_widgets()
        looks = dados.get("looks") or []
        if not looks:
            self.caixa_looks.add_widget(self.paragrafo("Sem looks registrados.", cor=MUTED_ESCURO))
            return
        for look in looks[:30]:
            imagem = look.get("imagem")
            if not imagem:
                continue
            caixa = MDBoxLayout(orientation="vertical", spacing=dp(2), size_hint=(None, None))
            caixa.height = dp(185)
            caixa.width = dp(115)
            foto = Image(
                source=imagem,
                size_hint=(None, None),
                size=(dp(115), dp(150)),
                fit_mode="cover",
            )
            caixa.add_widget(foto)
            caixa.add_widget(
                self.paragrafo(formatar_data(look.get("quando")), cor=MUTED, tamanho="11sp")
            )
            self.caixa_looks.add_widget(caixa)

    def ao_evento(self, evento):
        tipo = evento.get("tipo")
        if not tipo or tipo == "ping":
            return
        if tipo == "alerta":
            titulo = str(evento.get("titulo") or "IMVU Status")
            corpo = str(evento.get("corpo") or "")
            self.anexar_feed("[b]Alerta[/b] " + escape_markup(titulo + ((" - " + corpo) if corpo else "")))
            self.notificar(titulo, corpo)
            self.carregar_alertas(silencioso=True)
            return
        if tipo == "status":
            nick = str(evento.get("nick") or "")
            mudancas = evento.get("eventos") or []
            if "online" in mudancas:
                acao = "entrou online"
            elif "offline" in mudancas:
                acao = "ficou offline"
            elif "sala" in mudancas:
                acao = "mudou de sala"
            elif "look" in mudancas:
                acao = "trocou de look"
            else:
                acao = "atualizou o status"
            sala = (evento.get("sala") or {}).get("nome")
            texto = "[b]" + escape_markup(nick) + "[/b] " + acao
            if sala:
                texto += "  -  " + escape_markup(str(sala))
            self.anexar_feed(texto)
            if self.nick and nick.lower() == self.nick.lower():
                online = bool(evento.get("online"))
                self.lbl_status_estado.text = "ONLINE" if online else "OFFLINE"
                self.lbl_status_estado.text_color = OK if online else ERRO
                if sala:
                    self.lbl_status_sala.text = "Sala: " + str(sala)
            return
        if tipo == "sala":
            self.anexar_feed(
                "Sala [b]" + escape_markup(str(evento.get("nome") or evento.get("rid") or "?")) + "[/b]"
                + " agora com " + str(evento.get("online") or 0) + " pessoas"
            )

    def anexar_feed(self, texto):
        rotulo = self.paragrafo(texto, cor=TEXTO, markup=True, tamanho="12sp")
        self.caixa_feed.add_widget(rotulo, index=0)
        while len(self.caixa_feed.children) > 16:
            self.caixa_feed.remove_widget(self.caixa_feed.children[-1])

    def notificar(self, titulo, corpo):
        if not self.config.get("notificacoes", True):
            return
        try:
            from plyer import notification

            notification.notify(
                title=titulo,
                message=corpo or "",
                app_name="IMVU Status",
                timeout=10,
            )
        except Exception:
            pass

    def abrir_config(self):
        self.sm.current = "config"
        self.topbar.title = "Configuracoes"
        self.topbar.left_action_items = [["arrow-left", lambda _x: self.voltar_config()]]
        self.topbar.right_action_items = []

    def voltar_config(self):
        self.sm.current = "principal"
        self.topbar.title = "IMVU Status"
        self.topbar.left_action_items = []
        self.topbar.right_action_items = [
            ["web", lambda _x: self.abrir_painel()],
            ["refresh", lambda _x: self.atualizar_tudo()],
            ["cog", lambda _x: self.abrir_config()],
        ]

    def testar_conexao(self):
        url = (self.campo_api.text or "").strip().rstrip("/")
        if not url.startswith("http://") and not url.startswith("https://"):
            self.aviso("URL invalida: use http:// ou https://", True)
            return
        self.aviso("Testando conexao...")
        self.em_segundo_plano(
            lambda: Api(url).health(),
            lambda dados: self.aviso(
                "Conectado - v" + str(dados.get("versao") or "?")
                + (" - coletor ativo" if not (dados.get("coletor") or {}).get("desligado") else " - coletor desligado")
            ),
        )

    def salvar_config(self):
        url = (self.campo_api.text or "").strip().rstrip("/")
        if not url.startswith("http://") and not url.startswith("https://"):
            self.aviso("URL invalida: use http:// ou https://", True)
            return
        self.config["api"] = url
        self.config["notificacoes"] = bool(self.switch_notificacoes.active)
        self._salvar_config()
        self.api = Api(url)
        self.iniciar_live()
        self.aviso("Configuracoes salvas.")
        self.atualizar_tudo()

    def abrir_painel(self):
        url = self.api.base + "/?pagina=painel&app=1"
        if platform != "android":
            try:
                webbrowser.open(url)
            except Exception:
                self.aviso("Nao foi possivel abrir o navegador.", True)
            return
        try:
            self._abrir_painel_android(url)
        except Exception as exc:
            self.aviso("Falha ao abrir o painel: " + str(exc), True)
            try:
                webbrowser.open(url)
            except Exception:
                pass

    def _abrir_painel_android(self, url):
        from android.runnable import run_on_ui_thread
        from jnius import PythonJavaClass, autoclass, java_method

        if self._webview is not None:
            @run_on_ui_thread
            def recarregar():
                if self._webview is not None:
                    self._webview.loadUrl(url)
            recarregar()
            return

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        WebView = autoclass("android.webkit.WebView")
        WebViewClient = autoclass("android.webkit.WebViewClient")
        LinearLayout = autoclass("android.widget.LinearLayout")
        TextView = autoclass("android.widget.TextView")
        Button = autoclass("android.widget.Button")
        Gravity = autoclass("android.view.Gravity")
        Color = autoclass("android.graphics.Color")
        TypedValue = autoclass("android.util.TypedValue")
        Params = autoclass("android.widget.LinearLayout$LayoutParams")

        class Clique(PythonJavaClass):
            __javainterfaces__ = ["android/view/View$OnClickListener"]
            __javacontext__ = "app"

            def __init__(self, acao):
                super().__init__()
                self.acao = acao

            @java_method("(Landroid/view/View;)V")
            def onClick(self, _view):
                self.acao()

        app = self

        def voltar():
            web = app._webview
            if web is None:
                return
            if web.canGoBack():
                web.goBack()
            else:
                app._fechar_painel_android()

        listeners = [Clique(voltar), Clique(self._fechar_painel_android)]
        self._webview_listeners = listeners

        @run_on_ui_thread
        def montar():
            raiz = LinearLayout(activity)
            raiz.setOrientation(LinearLayout.VERTICAL)
            raiz.setBackgroundColor(Color.parseColor("#101216"))

            barra = LinearLayout(activity)
            barra.setOrientation(LinearLayout.HORIZONTAL)
            barra.setGravity(Gravity.CENTER_VERTICAL)
            barra.setBackgroundColor(Color.parseColor("#16181c"))
            barra.setPadding(int(dp(10)), int(dp(6)), int(dp(10)), int(dp(6)))

            titulo = TextView(activity)
            titulo.setText("IMVU Status - painel completo")
            titulo.setTextColor(Color.parseColor("#f3f4f6"))
            titulo.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14)
            barra.addView(titulo, Params(0, -2, 1.0))

            btn_voltar = Button(activity)
            btn_voltar.setText("Voltar")
            btn_voltar.setAllCaps(False)
            btn_voltar.setOnClickListener(listeners[0])
            barra.addView(btn_voltar, Params(-2, -2))

            btn_fechar = Button(activity)
            btn_fechar.setText("Fechar")
            btn_fechar.setAllCaps(False)
            btn_fechar.setOnClickListener(listeners[1])
            barra.addView(btn_fechar, Params(-2, -2))

            raiz.addView(barra, Params(-1, -2))

            web = WebView(activity)
            config = web.getSettings()
            config.setJavaScriptEnabled(True)
            config.setDomStorageEnabled(True)
            config.setLoadWithOverviewMode(True)
            config.setUseWideViewPort(True)
            config.setBuiltInZoomControls(False)
            config.setDisplayZoomControls(False)
            web.setBackgroundColor(Color.parseColor("#16181c"))
            web.setWebViewClient(WebViewClient())
            web.loadUrl(url)
            raiz.addView(web, Params(-1, 0, 1.0))

            activity.addContentView(raiz, Params(-1, -1))
            self._webview = web
            self._webview_raiz = raiz

        montar()

    def _fechar_painel_android(self):
        from android.runnable import run_on_ui_thread

        @run_on_ui_thread
        def fechar():
            raiz = self._webview_raiz
            web = self._webview
            self._webview = None
            self._webview_raiz = None
            self._webview_listeners = []
            if raiz is not None:
                pai = raiz.getParent()
                if pai is not None:
                    pai.removeView(raiz)
            if web is not None:
                try:
                    web.stopLoading()
                    web.destroy()
                except Exception:
                    pass

        fechar()

    def _ao_tecla_painel(self, _janela, tecla, *_args):
        if tecla == 27 and self._webview is not None:
            self._fechar_painel_android()
            return True
        return False

    def atualizar_tudo(self):
        self.carregar_salas(silencioso=True)
        self.carregar_watch(silencioso=True)
        self.carregar_alertas(silencioso=True)
        if self.nick:
            self.em_segundo_plano(
                lambda: self.api.status(self.nick),
                lambda dados: self.exibir_status(dados, self.nick),
                lambda _m: None,
            )


if __name__ == "__main__":
    ImvuStatusApp().run()
