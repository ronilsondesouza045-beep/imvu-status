#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IMVU Status - consulta de nick no terminal (usa endpoints nao oficiais do IMVU).

Uso:
  python imvu_status.py                        -> modo interativo (menu)
  python imvu_status.py NICK [NICK2 ...]       -> checa os nicks e sai
  python imvu_status.py --watch NICK -d 30     -> monitora o nick (padrao 30s)
  python imvu_status.py --sala 12345678-1      -> info de uma sala publica
  python imvu_status.py --login                -> faz login na sua conta antes (opcional)

Sem login da para ver: existe? esta online? disponibilidade (disponivel/ausente/DND),
e a sala atual quando o IMVU expoe essa informacao publicamente.
Com login (sua conta): tende a expor tambem a sala atual e se a sala e privada.

Aviso: endpoints nao oficiais; use com moderacao (o IMVU bloqueia excesso de requisicoes).
"""

import argparse
import ast
import datetime
import getpass
import html as htmlmod
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.imvu.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_cookies = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookies))
_sessao = {"ativo": False, "nick": None, "cid": None, "sauce": None}

DISPONIBILIDADE = {
    "": "-",
    "available": "Disponivel",
    "online": "Online",
    "away": "Ausente",
    "dnd": "Nao perturbe (DND)",
    "offline": "Offline",
}


class ErroAPI(Exception):
    pass


def _requisicao(url, metodo="GET", corpo=None, json_sim=True):
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if _sessao["sauce"] and metodo != "GET":
        headers["x-imvu-sauce"] = _sessao["sauce"]
        headers["x-imvu-application"] = "next_desktop/1"
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=dados, headers=headers, method=metodo)
    try:
        with _opener.open(req, timeout=20) as resp:
            texto = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            texto = e.read().decode("utf-8", errors="replace")
        except Exception:
            raise ErroAPI(f"HTTP {e.code}")
    except Exception as e:
        raise ErroAPI(f"falha de rede: {e}")
    if "too many requests" in texto.lower():
        raise ErroAPI("IMVU bloqueou temporariamente (muitas requisicoes). Espere alguns minutos.")
    if not json_sim:
        return texto
    try:
        js = json.loads(texto)
    except Exception:
        raise ErroAPI("resposta inesperada do IMVU (nao era JSON)")
    if isinstance(js, dict) and js.get("status") == "failure":
        raise ErroAPI(f"{js.get('error', '?')}: {js.get('message', '?')}")
    return js


def resolver_nick(nick):
    url = f"{API}/user?" + urllib.parse.urlencode({"username": nick})
    js = _requisicao(url)
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


def checar(nick):
    u = resolver_nick(nick)
    if not u:
        return None
    presenca = {}
    try:
        purl = u["rel"].get("presence") or f"{API}/presence/presence-{u['cid']}"
        pjs = _requisicao(purl)
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
            rjs = _requisicao(u["rel"]["current_room"])
            rnode = (rjs.get("denormalized") or {}).get(u["rel"]["current_room"]) or {}
            resultado["sala"] = rnode.get("data", {})
        except ErroAPI:
            resultado["sala"] = {}
    return resultado


def _data_epoch(seg):
    try:
        return datetime.datetime.fromtimestamp(int(seg), tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return "?"


def imprimir_detalhes(res, timestamp=True):
    d = res["data"]
    hora = time.strftime("%H:%M:%S")
    prefixo = f"[{hora}] " if timestamp else ""
    nome = d.get("display_name") or res["nick"]
    print(f"\n{prefixo}{nome}  (nick: {res['nick']} | CID: {res['cid']})")
    print(f"  Status..........: {'ONLINE' if res['online'] else 'OFFLINE'}")
    av = res["availability"]
    rotulo = DISPONIBILIDADE.get(av, av)
    print(f"  Disponibilidade.: {rotulo}")
    if res["sala"] is None:
        if res["online"]:
            print("  Sala atual......: nao exposta agora (amigos veem pelo cliente; :login pode ajudar)")
        else:
            print("  Sala atual......: - (offline)")
    elif res["sala"] == {}:
        print("  Sala atual......: existe, mas o IMVU negou/não devolveu os dados")
    else:
        sala = res["sala"]
        privacy = str(sala.get("privacy") or sala.get("privacy_type") or "?").lower()
        if "priv" in privacy:
            tipo = "PRIVADA (so entra quem recebe convite)"
        elif "pub" in privacy:
            tipo = "publica"
        else:
            tipo = f"desconhecida ({privacy})"
        nome_sala = sala.get("name") or "?"
        extra = f" | lotacao: {sala.get('capacity')}" if sala.get("capacity") else ""
        print(f"  Sala atual......: \"{nome_sala}\" ({tipo}{extra})")
        if not res["online"]:
            print("                    (status offline, mas o perfil ainda aponta essa sala — pode ser modo \"aparecer offline\")")
        if sala.get("description"):
            print(f"  Descricao sala..: {sala['description'][:120]}")
    extras = {k: v for k, v in res["presence"].items() if k != "online"}
    if extras:
        print(f"  Presence extra..: {json.dumps(extras, ensure_ascii=False)}")
    print(f"  Membro desde....: {_data_epoch(d.get('registered'))} | Pais: {d.get('country') or '?'}")
    marcas = []
    if d.get("is_vip"):
        marcas.append("VIP")
    if d.get("is_ap") or d.get("is_ap_plus"):
        marcas.append("AccessPass")
    if d.get("is_creator"):
        marcas.append("Criador")
    if d.get("is_staff"):
        marcas.append("Staff IMVU")
    if d.get("is_greeter"):
        marcas.append("Greeter")
    print(f"  Perfil..........: {', '.join(marcas) if marcas else 'comum'}")
    if d.get("tagline"):
        print(f"  Tagline.........: {d['tagline'][:120]}")
    if d.get("avatar_portrait_image"):
        print(f"  Avatar (imagem).: {d['avatar_portrait_image']}")


def modo_watch(nick, intervalo):
    print(f"\nMonitorando '{nick}' a cada {intervalo:.0f}s — Ctrl+C para parar.")
    ultima_chave = object()
    try:
        while True:
            try:
                res = checar(nick)
            except ErroAPI as e:
                print(f"[{time.strftime('%H:%M:%S')}] erro: {e}")
                res = None
            if res is None:
                print(f"[{time.strftime('%H:%M:%S')}] '{nick}' nao encontrado")
            else:
                sala_nome = (res["sala"] or {}).get("name") if isinstance(res["sala"], dict) else None
                chave = (res["online"], res["availability"], sala_nome)
                if chave != ultima_chave:
                    imprimir_detalhes(res)
                    ultima_chave = chave
                else:
                    estado = "ONLINE" if res["online"] else "OFFLINE"
                    av = DISPONIBILIDADE.get(res["availability"], res["availability"])
                    extra = f" | sala: {sala_nome}" if sala_nome else ""
                    print(f"[{time.strftime('%H:%M:%S')}] {res['nick']}: {estado} ({av}){extra}")
            time.sleep(intervalo)
    except KeyboardInterrupt:
        print("\nMonitoramento parado.")


def fazer_login():
    print("\n> Login na SUA conta IMVU (a senha vai apenas para o proprio IMVU; nada e salvo em disco).")
    usuario = input("  Usuario IMVU: ").strip()
    if not usuario:
        print("  Cancelado.")
        return False
    senha = getpass.getpass("  Senha: ")
    codigo = ""
    for tentativa in range(2):
        corpo = {
            "gdpr_cookie_acceptance": True,
            "username": usuario,
            "password": senha,
            "remember_device": True,
        }
        if codigo:
            corpo["2fa_code"] = codigo
        try:
            _requisicao(f"{API}/login", metodo="POST", corpo=corpo)
        except ErroAPI as e:
            msg = str(e).lower()
            if tentativa == 0 and ("2fa" in msg or "code" in msg or "verifica" in msg or "challenge" in msg):
                codigo = input("  Codigo de verificacao (2FA/e-mail): ").strip()
                continue
            print(f"  Falha no login: {e}")
            return False
        try:
            js = _requisicao(f"{API}/login/me")
            node = (js.get("denormalized") or {}).get(js.get("id"), {})
            _sessao.update(
                ativo=True,
                nick=usuario,
                cid=node.get("relations", {}).get("quick_chat_profile"),
                sauce=(node.get("data") or {}).get("sauce"),
            )
            print(f"  Logado como {usuario}. Agora a consulta usa sua sessao.")
            return True
        except ErroAPI as e:
            print(f"  Login aceito, mas /login/me falhou: {e}")
            return False
    return False


def info_sala(room_id):
    m = re.search(r"(\d+-\d+)", room_id or "")
    if not m:
        print("  Informe o ID/parede assim: 12345678-1 ou a URL da sala.")
        return
    rid = m.group(1)
    try:
        html = _requisicao(
            f"https://www.imvu.com/rooms/show-room-details.php?room_instance_id={rid}",
            json_sim=False,
        )
    except ErroAPI as e:
        print(f"  Erro ao buscar sala: {e}")
        return
    mi = re.search(r"var roomInfo = (\{.*?\n\})\s*\n</script>", html, re.S)
    if not mi:
        print("  Sala nao encontrada (ou nao e uma sala publica listada).")
        return
    info = None
    bloco = mi.group(1)
    bloco_py = re.sub(r"\btrue\b", "True", bloco)
    bloco_py = re.sub(r"\bfalse\b", "False", bloco_py)
    bloco_py = re.sub(r"\bnull\b", "None", bloco_py)
    try:
        info = ast.literal_eval(bloco_py)
    except Exception:
        info = {
            "room_name": (re.search(r"'room_name'\s*:\s*'([^']*)'", bloco) or [None, "?"])[1],
            "owner": (re.search(r"'owner'\s*:\s*'([^']*)'", bloco) or [None, "?"])[1],
            "count": (re.search(r"'count'\s*:\s*'(\d+)'", bloco) or [None, "?"])[1],
            "max": (re.search(r"'max'\s*:\s*'(\d+)'", bloco) or [None, "?"])[1],
        }
    print(f"\n  Sala............: {info.get('room_name', '?')}  (ID {rid})")
    print(f"  Dono............: {info.get('owner', '?')}")
    print(f"  Ocupacao........: {info.get('count', '?')}/{info.get('max', '?')}")
    if info.get("desc"):
        try:
            print(f"  Descricao.......: {urllib.parse.unquote(str(info['desc']))[:160]}")
        except Exception:
            pass
    participantes = info.get("participants") or []
    if participantes:
        print("  Participantes...:")
        for p in participantes:
            if isinstance(p, dict):
                nome = p.get("name", "?")
                tamanho = p.get("size", "")
                print(f"    - {nome}" + (f" ({tamanho} KB)" if tamanho != "" else ""))
            else:
                print(f"    - {p}")
    elif info.get("count") not in ("0", 0, None, "?"):
        print("  Participantes...: (lista nao exposta; sala pode exigir login para detalhar)")
    else:
        print("  Participantes...: sala vazia")


MENU = """
+------------------------------------------------------------------+
|  IMVU STATUS - menu (nao oficial)                                |
|                                                                  |
|  1 - Checar nick (online/offline + sala publica/privada)         |
|  2 - Monitorar nick (avisa quando mudar status ou sala)          |
|  3 - Detalhes de sala publica (dono, lotacao, participantes)     |
|  4 - Listar salas do diretorio publico (por letra A-Z, 0-9)      |
|  5 - Login na conta IMVU (opcional, ve mais detalhes)            |
|  6 - Logout (voltar ao modo anonimo)                             |
|  0 - Sair                                                        |
+------------------------------------------------------------------+"""


def _sessao_info():
    if _sessao["ativo"]:
        return f"logado como {_sessao['nick']}"
    return "anonimo"


def opcao_checar():
    while True:
        try:
            linha = input("nick (Enter volta ao menu): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not linha:
            return
        for nick in linha.split():
            try:
                res = checar(nick)
            except ErroAPI as e:
                print(f"  {nick}: erro: {e}")
                continue
            if res is None:
                print(f"  '{nick}' nao encontrado no IMVU.")
            else:
                imprimir_detalhes(res, timestamp=False)


def opcao_watch():
    nick = input("nick para monitorar (Enter volta): ").strip()
    if not nick:
        return
    seg = input("intervalo em segundos (Enter = 30): ").strip()
    try:
        intervalo = max(5.0, float(seg)) if seg else 30.0
    except ValueError:
        intervalo = 30.0
    modo_watch(nick.split()[0], intervalo)


def opcao_sala():
    while True:
        try:
            rid = input("ID ou URL da sala (Enter volta ao menu): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not rid:
            return
        info_sala(rid)


def listar_salas(letra):
    letra = (letra or "A").strip().upper()[:1]
    if not letra.isalnum():
        letra = "A"
    try:
        texto = _requisicao(
            "https://www.imvu.com/rooms/index.php?" + urllib.parse.urlencode({"letter": letra}),
            json_sim=False,
        )
    except ErroAPI as e:
        print(f"  Erro ao buscar diretorio: {e}")
        return
    salas = re.findall(r"room_instance_id=(\d+-\d+)'>([^<]*)</a>", texto)
    if not salas:
        print(f"  Nenhuma sala encontrada para a letra {letra}.")
        return
    print(f"\n  Salas publicas do diretorio (letra {letra}) - use a opcao 3 para ver detalhes:")
    for i, (rid, nome) in enumerate(salas, 1):
        print(f"   {i:>4}. {htmlmod.unescape(nome).strip()}  ({rid})")
    print(f"  total: {len(salas)} salas")


def opcao_diretorio():
    letra = input("letra do diretorio (A-Z ou 0-9, Enter = A): ").strip()
    listar_salas(letra)


def opcao_logout():
    _cookies.clear()
    _sessao.update(ativo=False, nick=None, cid=None, sauce=None)
    print("  Sessao descartada (modo anonimo).")


def menu():
    print("=" * 66)
    print("  IMVU Status - consulta por nick (nao oficial)")
    print("=" * 66)
    while True:
        print(MENU)
        print(f"  sessao: {_sessao_info()}")
        try:
            op = input("opcao> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if op in ("0", "sair", "q", "quit", "exit"):
            break
        elif op == "":
            continue
        elif op == "1":
            opcao_checar()
        elif op == "2":
            opcao_watch()
        elif op == "3":
            opcao_sala()
        elif op == "4":
            opcao_diretorio()
        elif op == "5":
            fazer_login()
        elif op == "6":
            opcao_logout()
        else:
            print("  Opcao invalida.")
    print("Ate mais!")


def main():
    p = argparse.ArgumentParser(description="IMVU Status — consulta de nick no terminal.")
    p.add_argument("nicks", nargs="*", help="um ou mais nicks para checar e sair")
    p.add_argument("-w", "--watch", metavar="NICK", help="monitora o nick (Ctrl+C para parar)")
    p.add_argument("-d", "--delay", type=float, default=30.0, help="intervalo do watch em segundos")
    p.add_argument("--sala", metavar="ROOM_ID", help="mostra detalhes de uma sala publica")
    p.add_argument("--login", action="store_true", help="faz login na sua conta IMVU antes")
    args = p.parse_args()

    if args.login or args.watch or args.sala or args.nicks:
        if args.login:
            fazer_login()
        if args.sala:
            info_sala(args.sala)
        if args.watch:
            modo_watch(args.watch, max(5.0, args.delay))
        for nick in args.nicks:
            try:
                res = checar(nick)
            except ErroAPI as e:
                print(f"  {nick}: erro: {e}")
                continue
            if res is None:
                print(f"\n'{nick}' nao encontrado no IMVU.")
            else:
                imprimir_detalhes(res)
        return

    menu()


if __name__ == "__main__":
    main()
