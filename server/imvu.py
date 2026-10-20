import ast
import asyncio
import datetime
import hashlib
import html
import json
import re
import time
import urllib.parse
from typing import Any

import httpx

API = "https://api.imvu.com"
UA = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
)


class IMVUErro(Exception):
    def __init__(self, mensagem: str, status: int = 502):
        super().__init__(mensagem)
        self.status = status


class ClienteIMVU:
    def __init__(self, gap: float = 0.3, concorrencia: int = 3):
        self._http = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*"},
            timeout=25.0,
            follow_redirects=True,
        )
        self._gap = gap
        self._sem = asyncio.Semaphore(concorrencia)
        self._trava = asyncio.Lock()
        self._ultimo = 0.0
        self._pausado_ate = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}

    async def fechar(self):
        await self._http.aclose()

    async def _esperar(self):
        agora = time.monotonic()
        if agora < self._pausado_ate:
            raise IMVUErro("IMVU pediu para desacelerar; tentando de novo em instantes", 429)
        async with self._trava:
            agora = time.monotonic()
            espera = self._gap - (agora - self._ultimo)
            if espera > 0:
                await asyncio.sleep(espera)
            self._ultimo = time.monotonic()

    def _cache_pegar(self, chave: str):
        item = self._cache.get(chave)
        if item and item[0] > time.monotonic():
            return item[1]
        if item:
            self._cache.pop(chave, None)
        return None

    def _cache_guardar(self, chave: str, valor: Any, ttl: float):
        self._cache[chave] = (time.monotonic() + ttl, valor)
        if len(self._cache) > 2000:
            agora = time.monotonic()
            for k in [k for k, v in self._cache.items() if v[0] <= agora]:
                self._cache.pop(k, None)

    async def _get(self, url: str, texto: bool = False):
        await self._esperar()
        async with self._sem:
            try:
                resposta = await self._http.get(url)
            except httpx.HTTPError as exc:
                raise IMVUErro(f"falha de rede: {exc}") from exc
        corpo = resposta.text
        if resposta.status_code == 429 or "too many requests" in corpo.lower():
            self._pausado_ate = time.monotonic() + 120
            raise IMVUErro("IMVU bloqueou temporariamente (muitas requisicoes)", 429)
        if resposta.status_code >= 400:
            raise IMVUErro(f"IMVU respondeu HTTP {resposta.status_code}", 502)
        if texto:
            return corpo
        try:
            dados = json.loads(corpo)
        except Exception as exc:
            raise IMVUErro("resposta inesperada do IMVU") from exc
        if isinstance(dados, dict) and dados.get("status") == "failure":
            raise IMVUErro(f"{dados.get('error', '?')}: {dados.get('message', '?')}", 502)
        return dados

    async def resolver_nick(self, nick: str):
        url = f"{API}/user?" + urllib.parse.urlencode({"username": nick})
        dados = await self._get(url)
        raiz = dados.get("denormalized", {})
        colecao = raiz.get(dados.get("id"), {})
        itens = (colecao.get("data") or {}).get("items") or []
        if not itens:
            return None
        node_url = itens[0]
        node = raiz.get(node_url, {})
        achado = re.search(r"user-(\d+)$", node_url)
        return {
            "cid": achado.group(1) if achado else None,
            "data": node.get("data", {}),
            "rel": node.get("relations", {}),
        }

    async def _rel(self, rel: dict, chave: str):
        url = rel.get(chave)
        if not url:
            return {}
        dados = await self._get(url)
        node = (dados.get("denormalized") or {}).get(url) or {}
        return node.get("data", {}) or {}

    async def check(self, nick: str, detalhes: bool = False):
        nick = (nick or "").strip()
        if not nick:
            return None
        chave = "check:%s:%s" % (nick.lower(), "full" if detalhes else "light")
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return guardado
        usuario = await self.resolver_nick(nick)
        if not usuario:
            self._cache_guardar(chave, None, 30)
            return None

        dados = usuario["data"]
        rel = usuario["rel"]
        presenca = {}
        try:
            presenca = await self._rel(rel, "presence")
        except IMVUErro:
            presenca = {}

        online = bool(presenca.get("online", dados.get("online", False)))
        sala = None
        rid = None
        if "current_room" in rel:
            try:
                bruto = await self._rel(rel, "current_room")
            except IMVUErro:
                bruto = {}
            if bruto:
                achado = re.search(r"room-([0-9]+-[0-9]+)", str(rel.get("current_room") or ""))
                rid = achado.group(1) if achado else None
                sala = {
                    "rid": rid,
                    "nome": str(bruto.get("name") or "?"),
                    "privacidade": str(bruto.get("privacy") or "?").lower(),
                }

        perfil = {}
        look = None
        if detalhes:
            try:
                perfil = await self._rel(rel, "profile")
            except IMVUErro:
                perfil = {}
            try:
                outfit = await self._rel(rel, "profile_outfit")
                produtos = [
                    str(p.get("product_id"))
                    for p in (outfit.get("products") or [])
                    if isinstance(p, dict) and p.get("product_id")
                ]
                if produtos:
                    assinatura = hashlib.sha1(",".join(produtos).encode("utf-8")).hexdigest()[:20]
                    corpo = None
                    try:
                        pr = await self._get(f"{API}/product/product-{produtos[0]}")
                        node = (pr.get("denormalized") or {}).get(f"{API}/product/product-{produtos[0]}") or {}
                        corpo = (node.get("data") or {}).get("product_name")
                    except IMVUErro:
                        corpo = None
                    look = {
                        "assinatura": assinatura,
                        "total": len(produtos),
                        "corpo": corpo,
                    }
            except IMVUErro:
                look = None

        foto = perfil.get("image") or dados.get("thumbnail_url")
        if foto and not str(foto).startswith(("http://", "https://")):
            foto = None
        marcas = []
        if dados.get("is_vip"):
            marcas.append("VIP")
        if dados.get("is_ap") or dados.get("is_ap_plus"):
            marcas.append("AccessPass")
        if dados.get("is_creator"):
            marcas.append("Criador")
        if dados.get("is_staff"):
            marcas.append("Staff")

        resultado = {
            "nick": dados.get("username") or nick,
            "display_name": dados.get("display_name") or dados.get("username") or nick,
            "cid": usuario["cid"],
            "online": online,
            "availability": str(dados.get("availability") or "").strip().lower(),
            "sala": sala,
            "pais": dados.get("country") or "?",
            "registrado": _data_epoch(dados.get("registered")),
            "seguidores": perfil.get("approx_follower_count"),
            "marcas": marcas,
            "tagline": (dados.get("tagline") or "")[:200] or None,
            "foto_perfil": foto,
            "avatar_3d": dados.get("avatar_portrait_image") or dados.get("avatar_image"),
            "look": look,
            "aparente_offline": (not online) and bool(sala),
            "atualizado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._cache_guardar(chave, resultado, 4 if not detalhes else 15)
        return resultado

    async def sala(self, rid: str):
        achado = re.search(r"(\d+-\d+)", rid or "")
        if not achado:
            raise IMVUErro("ID invalido (ex: 12345678-1)", 400)
        rid = achado.group(1)
        chave = f"sala:{rid}"
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return guardado
        corpo = await self._get(
            f"https://www.imvu.com/rooms/show-room-details.php?room_instance_id={rid}",
            texto=True,
        )
        bloco = re.search(r"var roomInfo = (\{.*?\n\})\s*\n</script>", corpo, re.S)
        if not bloco:
            raise IMVUErro("sala nao encontrada (so salas publicas do diretorio)", 404)
        texto = bloco.group(1)
        info = None
        for tentativa in (
            texto,
            re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", texto))),
        ):
            try:
                info = ast.literal_eval(tentativa)
                if isinstance(info, dict):
                    break
            except Exception:
                info = None
        if not isinstance(info, dict):
            info = {
                "room_name": _primeiro(texto, "room_name"),
                "owner": _primeiro(texto, "owner"),
                "count": _primeiro(texto, "count"),
                "max": _primeiro(texto, "max"),
                "desc": "",
                "participants": [],
            }
        participantes = []
        for p in info.get("participants") or []:
            if isinstance(p, dict):
                participantes.append({"nome": str(p.get("name", "?")), "tamanho": str(p.get("size", "") or "")})
            else:
                participantes.append({"nome": str(p), "tamanho": ""})
        resultado = {
            "rid": rid,
            "nome": html.unescape(str(info.get("room_name", "?"))),
            "dono": html.unescape(str(info.get("owner", "?"))),
            "online": _inteiro(info.get("count")),
            "capacidade": _inteiro(info.get("max")),
            "descricao": html.unescape(urllib.parse.unquote(str(info.get("desc") or "")))[:300],
            "participantes": participantes,
        }
        self._cache_guardar(chave, resultado, 20)
        return resultado

    async def diretorio(self, letra: str = "A"):
        letra = (letra or "A").strip().upper()[:1]
        if not letra.isalnum():
            letra = "A"
        chave = f"dir:{letra}"
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return guardado
        corpo = await self._get(
            "https://www.imvu.com/rooms/index.php?" + urllib.parse.urlencode({"letter": letra}),
            texto=True,
        )
        achados = re.findall(r"room_instance_id=(\d+-\d+)'>([^<]*)</a>", corpo)
        vistos = {}
        for rid, nome in achados:
            nome = html.unescape(nome).strip()
            if rid and nome:
                vistos[rid] = nome
        lista = [{"rid": rid, "nome": nome} for rid, nome in vistos.items()]
        self._cache_guardar(chave, lista, 120)
        return lista


def _data_epoch(valor):
    try:
        return datetime.datetime.fromtimestamp(int(valor), tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def _primeiro(texto: str, campo: str):
    achado = re.search(r"'%s'\s*:\s*'([^']*)'" % re.escape(campo), texto)
    return achado.group(1) if achado else "?"


def _inteiro(valor):
    try:
        return int(str(valor).strip())
    except Exception:
        return None


cliente = ClienteIMVU()
