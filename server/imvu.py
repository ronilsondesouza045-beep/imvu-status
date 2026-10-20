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
        nick = (nick or "").strip()
        chave = f"user:{nick.lower()}"
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return None if guardado is False else guardado
        url = f"{API}/user?" + urllib.parse.urlencode({"username": nick})
        dados = await self._get(url)
        raiz = dados.get("denormalized", {})
        colecao = raiz.get(dados.get("id"), {})
        itens = (colecao.get("data") or {}).get("items") or []
        if not itens:
            self._cache_guardar(chave, False, 20)
            return None
        node_url = itens[0]
        node = raiz.get(node_url, {})
        achado = re.search(r"user-(\d+)$", node_url)
        resultado = {
            "cid": achado.group(1) if achado else None,
            "data": node.get("data", {}),
            "rel": node.get("relations", {}),
        }
        self._cache_guardar(chave, resultado, 20)
        return resultado

    async def _rel(self, rel: dict, chave: str):
        url = rel.get(chave)
        if not url:
            return {}
        chave_cache = f"rel:{url}"
        guardado = self._cache_pegar(chave_cache)
        if guardado is not None:
            return guardado
        dados = await self._get(url)
        node = (dados.get("denormalized") or {}).get(url) or {}
        valor = node.get("data", {}) or {}
        ttl = {"profile": 60.0, "profile_outfit": 30.0, "presence": 6.0, "current_room": 6.0}.get(chave, 15.0)
        self._cache_guardar(chave_cache, valor, ttl)
        return valor

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

    async def produto(self, product_id: int):
        chave = f"produto:{product_id}"
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return guardado
        url = f"{API}/product/product-{product_id}"
        dados = await self._get(url)
        node = (dados.get("denormalized") or {}).get(url) or {}
        info = node.get("data") or {}
        if not info:
            return None
        resultado = {
            "id": product_id,
            "nome": html.unescape(str(info.get("product_name") or "Produto sem nome")),
            "imagem": _url_https(info.get("product_image") or info.get("preview_image")),
            "preview": _url_https(info.get("preview_image")),
            "criador": html.unescape(str(info.get("creator_name") or "?")),
            "criador_cid": info.get("creator_cid"),
            "preco": _inteiro(info.get("product_price")),
            "preco_desconto": _inteiro(info.get("discount_price")),
            "rating": info.get("rating"),
            "genero": info.get("gender"),
            "categorias": info.get("categories") or [],
            "tags": (info.get("tags") or [])[:12],
            "pagina": info.get("product_page"),
            "criador_pagina": info.get("creator_page"),
            "bundle": bool(info.get("is_bundle")),
            "visivel": bool(info.get("is_visible", True)),
        }
        self._cache_guardar(chave, resultado, 3600)
        return resultado

    async def outfit(self, rel: dict, limite: int = 40):
        chave = f"outfit:{rel.get('profile_outfit')}:{limite}"
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return guardado
        dados = await self._rel(rel, "profile_outfit")
        produtos = []
        vistos = set()
        for item in dados.get("products") or []:
            if not isinstance(item, dict):
                continue
            pid = _inteiro(item.get("product_id"))
            if not pid or pid in vistos:
                continue
            vistos.add(pid)
            produtos.append(item)
            if len(produtos) >= max(1, min(limite, 60)):
                break
        resultados = await asyncio.gather(
            *[self.produto(item["product_id"]) for item in produtos],
            return_exceptions=True,
        )
        itens = []
        for item, produto in zip(produtos, resultados):
            pid = _inteiro(item.get("product_id"))
            if isinstance(produto, Exception) or not produto:
                itens.append({
                    "id": pid,
                    "nome": "Produto " + str(pid),
                    "imagem": None,
                    "criador": "?",
                    "preco": None,
                    "rating": item.get("rating"),
                    "pagina": f"https://www.imvu.com/shop/product.php?products_id={pid}",
                })
                continue
            produto["owned"] = bool(item.get("owned"))
            produto["published"] = bool(item.get("published"))
            produto["rating"] = item.get("rating") or produto.get("rating")
            itens.append(produto)
        resultado = {
            "total": len(produtos),
            "itens": itens,
            "look_url": dados.get("look_url"),
            "asset_url": dados.get("asset_url"),
            "atualizado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._cache_guardar(chave, resultado, 60)
        return resultado

    async def perfil_completo(self, nick: str, limite_outfit: int = 40):
        chave = f"perfil:{nick.lower()}:{limite_outfit}"
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return guardado
        base = await self.check(nick, detalhes=True)
        if not base:
            return None
        usuario = await self.resolver_nick(base["nick"])
        if not usuario:
            return None
        dados = usuario["data"]
        rel = usuario["rel"]
        perfil = {}
        presenca = {}
        quarto = {}
        for campo, destino in (("profile", "perfil"), ("presence", "presenca"), ("current_room", "quarto")):
            if campo not in rel:
                continue
            try:
                valor = await self._rel(rel, campo)
            except IMVUErro:
                valor = {}
            if destino == "perfil":
                perfil = valor
            elif destino == "presenca":
                presenca = valor
            else:
                quarto = valor
        try:
            roupa = await self.outfit(rel, limite=limite_outfit)
        except IMVUErro:
            roupa = {"total": 0, "itens": [], "aviso": "outfit indisponivel agora"}
        sala = base.get("sala")
        if quarto:
            sala = {
                "rid": _rid_de_url(rel.get("current_room")),
                "nome": html.unescape(str(quarto.get("name") or (sala or {}).get("nome") or "?")),
                "privacidade": str(quarto.get("privacy") or (sala or {}).get("privacidade") or "?").lower(),
                "online": _inteiro(quarto.get("occupancy")),
                "capacidade": _inteiro(quarto.get("capacity")),
                "imagem": _url_https(quarto.get("image_url") or quarto.get("rendered_image")),
                "rating": quarto.get("rating"),
                "idioma": quarto.get("language"),
                "join_url": quarto.get("join_room_url"),
                "descricao": html.unescape(str(quarto.get("description") or ""))[:300],
            }
        resultado = {
            **base,
            "sala": sala,
            "online": bool(presenca.get("online", base.get("online"))),
            "perfil": {
                "titulo": perfil.get("title") or base.get("display_name"),
                "imagem": _url_https(perfil.get("image") or base.get("foto_perfil")),
                "avatar_nome": perfil.get("avatar_name"),
                "seguidores": perfil.get("approx_follower_count"),
                "seguindo": perfil.get("approx_following_count"),
                "online": perfil.get("online"),
            },
            "detalhes": {
                "idade": dados.get("age"),
                "genero": _genero(dados.get("gender")),
                "pais": dados.get("country") or base.get("pais"),
                "estado": dados.get("state"),
                "interesses": (dados.get("interests") or "")[:400] or None,
                "disponibilidade": base.get("availability") or None,
                "tagline": base.get("tagline"),
                "membro_desde": base.get("registrado"),
                "nft": bool(dados.get("has_nft")),
                "badge_level": dados.get("badge_level") or 0,
                "idade_verificada": bool(dados.get("is_ageverified")),
            },
            "outfit": roupa,
            "atualizado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._cache_guardar(chave, resultado, 20 if (roupa.get("itens") or roupa.get("total")) else 4)
        return resultado

    async def catalogo(self, termo: str, pagina: int = 1, ordenar: str = "relevancia",
                       tipo: str = "produtos"):
        termo = (termo or "").strip()[:120]
        if not termo:
            raise IMVUErro("informe um termo para buscar no catalogo", 400)
        pagina = max(1, min(_inteiro(pagina) or 1, 200))
        ordenar = (ordenar or "relevancia").strip().lower()
        tipo = (tipo or "produtos").strip().lower()
        chave = f"catalogo:{termo.lower()}:{pagina}:{ordenar}:{tipo}"
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return guardado
        params = {"search_text": termo, "page": pagina}
        if tipo == "criador":
            params["within"] = "creator_name"
        elif tipo == "id":
            params["within"] = "id"
        if ordenar == "novidades":
            params["sort"] = "id"
        elif ordenar == "preco_asc":
            params.update({"sort": "price", "sortorder": "asc"})
        elif ordenar == "preco_desc":
            params.update({"sort": "price", "sortorder": "desc"})
        elif ordenar == "avaliacao":
            params["sort"] = "user_rating"
        url = "https://www.imvu.com/shop/web_search.php?" + urllib.parse.urlencode(params)
        corpo = await self._get(url, texto=True)
        itens = _parse_catalogo(corpo)
        paginas = [int(n) for n in re.findall(r"(?:[?&]|&amp;)page=(\d+)", corpo)]
        resultado = {
            "termo": termo,
            "pagina": pagina,
            "paginas": max(paginas) if paginas else pagina,
            "ordenar": ordenar,
            "tipo": tipo,
            "itens": itens,
        }
        self._cache_guardar(chave, resultado, 120)
        return resultado

    async def sala_publica(self, rid: str):
        achado = re.search(r"(\d+-\d+)", rid or "")
        if not achado:
            raise IMVUErro("ID invalido (ex: 12345678-1)", 400)
        rid = achado.group(1)
        chave = f"sala_api:{rid}"
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return guardado
        url = f"{API}/room/room-{rid}"
        dados = await self._get(url)
        node = (dados.get("denormalized") or {}).get(url) or {}
        info = node.get("data") or {}
        if not info:
            raise IMVUErro("sala publica nao encontrada", 404)
        resultado = {
            "rid": rid,
            "nome": html.unescape(str(info.get("name") or "?")),
            "dono": html.unescape(str(info.get("owner_avatarname") or "?")),
            "descricao": html.unescape(str(info.get("description") or ""))[:400],
            "privacidade": info.get("privacy") or "public",
            "tipo": info.get("type"),
            "online": _inteiro(info.get("occupancy")) or 0,
            "capacidade": _inteiro(info.get("capacity")) or 0,
            "imagem": _url_https(info.get("image_url") or info.get("rendered_image")),
            "rating": info.get("rating"),
            "idioma": info.get("language"),
            "rating_conteudo": info.get("content_rating"),
            "ap": bool(info.get("is_ap")),
            "vip": bool(info.get("is_vip")),
            "audio": bool(info.get("has_audio")),
            "join_url": info.get("join_room_url"),
        }
        self._cache_guardar(chave, resultado, 20)
        return resultado

    async def salas_ao_vivo(self, letra: str = "A", limite: int = 12):
        letra = (letra or "A").strip().upper()[:1] or "A"
        limite = max(1, min(_inteiro(limite) or 12, 30))
        chave = f"salas_vivo:{letra}:{limite}"
        guardado = self._cache_pegar(chave)
        if guardado is not None:
            return guardado
        lista = await self.diretorio(letra)
        selecionadas = lista[:limite]
        detalhes = await asyncio.gather(
            *[self.sala_publica(item["rid"]) for item in selecionadas],
            return_exceptions=True,
        )
        salas = []
        for item, detalhe in zip(selecionadas, detalhes):
            if isinstance(detalhe, Exception) or not detalhe:
                salas.append({
                    "rid": item["rid"],
                    "nome": item["nome"],
                    "dono": "?",
                    "online": None,
                    "capacidade": None,
                    "imagem": None,
                    "privacidade": "public",
                })
            else:
                salas.append(detalhe)
        salas.sort(key=lambda s: (s.get("online") is None, -(s.get("online") or 0)))
        resultado = {"letra": letra, "total": len(lista), "salas": salas}
        self._cache_guardar(chave, resultado, 25)
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


def _url_https(valor):
    if not valor:
        return None
    texto = str(valor).strip()
    if texto.startswith("//"):
        return "https:" + texto
    if texto.startswith("http://"):
        return "https://" + texto[7:]
    if texto.startswith("https://"):
        return texto
    return None


def _genero(valor):
    return {
        "m": "Masculino",
        "f": "Feminino",
        "male": "Masculino",
        "female": "Feminino",
        "other": "Outro",
    }.get(str(valor or "").strip().lower(), valor)


def _rid_de_url(valor):
    achado = re.search(r"room-(\d+-\d+)", str(valor or ""))
    return achado.group(1) if achado else None


def _parse_catalogo(corpo: str):
    blocos = list(re.finditer(r'<div class="widget-product-wide[^"]*"\s+id="(\d+)"', corpo, re.I))
    itens = []
    vistos = set()
    for indice, bloco in enumerate(blocos):
        pid = _inteiro(bloco.group(1))
        if not pid or pid in vistos:
            continue
        vistos.add(pid)
        trecho = corpo[bloco.end(): blocos[indice + 1].start() if indice + 1 < len(blocos) else len(corpo)]
        nome = re.search(
            r'href="/shop/product\.php\?products_id=' + str(pid) + r'"\s+title="([^"]+)"',
            trecho,
            re.I,
        )
        imagem = re.search(r'<img[^>]+src="([^"]+)"[^>]*class="thumbnail"', trecho, re.I)
        criador = re.search(r'manufacturers_id=(\d+)"\s+title="([^"]+)"', trecho, re.I)
        preco = re.search(r'class="price"><span[^>]*>([^<]+)</span>', trecho, re.I)
        itens.append({
            "id": pid,
            "nome": html.unescape(nome.group(1)) if nome else "Produto " + str(pid),
            "imagem": _url_https(imagem.group(1)) if imagem else None,
            "criador": html.unescape(criador.group(2)) if criador else "?",
            "criador_cid": _inteiro(criador.group(1)) if criador else None,
            "preco": _preco(preco.group(1)) if preco else None,
            "pagina": f"https://www.imvu.com/shop/product.php?products_id={pid}",
            "criador_pagina": (
                f"https://www.imvu.com/shop/web_search.php?manufacturers_id={criador.group(1)}"
                if criador else None
            ),
        })
    return itens


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


def _preco(valor):
    try:
        limpo = re.sub(r"\D", "", str(valor))
        return int(limpo) if limpo else None
    except Exception:
        return None


cliente = ClienteIMVU()
