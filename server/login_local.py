# -*- coding: utf-8 -*-
"""
IMVU Status - login local da conta do dono (roda NO SEU PC, quando precisar).

Jeitos de conectar (o assistente tenta na ordem):

  1. Puxa a sessao pronta do Firefox (se voce estiver logado no IMVU por la,
     serve sem digitar nada);
  2. Colar o valor do cookie osCsid copiado do navegador — funciona no
     Chrome, Edge, Firefox... qualquer um. (O Chrome nao deixa mais ler os
     cookies sozinho por causa da protecao App-Bound Encryption do 127+,
     mas copiar o valor na mao funciona).
  3. Digitar o nick e a senha (a senha nao aparece na tela).

No fim:

  - salva a sessao em server/dados/session.json (fica so no seu PC e e
    ignorado pelo GitHub);
  - gera o texto pronto para colar no Render como variavel IMVU_SESSAO
    (curto, com so o cookie que importa — seguro para colar em qualquer lugar).

A senha NAO e salva em lugar nenhum e nao e enviada a ninguem.
NUNCA mande a senha nem o texto gerado para outra pessoa.
"""

import base64
import getpass
import http.cookiejar
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from app import SESSION_FILE, SessaoIMVU, criar_pastas  # noqa: E402

ARQUIVO_ENV = os.path.join(os.path.dirname(SESSION_FILE), "sessao_env.txt")


def tentar_firefox():
    print("Procurando sessao pronta no Firefox...")
    try:
        import browser_cookie3
    except Exception:
        print("  (browser_cookie3 nao instalado; sem problema, use outra opcao)")
        return None
    try:
        jar = browser_cookie3.firefox(domain_name="imvu.com")
    except Exception:
        print("  (nao encontrei o Firefox/perfil neste PC)")
        return None
    cookies = list(jar)
    if not cookies:
        print("  (o Firefox nao tem cookies do IMVU - nao esta logado por la)")
        return None
    sessao = SessaoIMVU()
    for cookie in cookies:
        sessao.cookies.set_cookie(cookie)
    if sessao.validar():
        sessao.salvar_sessao()
        return sessao
    print("  (achei cookies do IMVU no Firefox, mas a sessao deles expirou)")
    return None


def tentar_cookie():
    print()
    print("Como copiar o cookie do navegador (Chrome, Edge, Firefox...):")
    print("  1) Abra o IMVU logado no navegador: https://www.imvu.com")
    print("  2) Aperte F12 (abre o painel de desenvolvedor)")
    print("  3) Procure a aba 'Application' (em portugues as vezes aparece")
    print("     como 'Aplicativo') e no menu da esquerda clique em:")
    print("     Cookies > https://www.imvu.com")
    print("  4) Ache a linha 'osCsid'")
    print("  5) Clique DUAS VEZES em cima do VALOR dela (coluna 'Value')")
    print("     e copie tudo (Ctrl+C)")
    print()
    valor = input("Cole aqui o valor do osCsid: ").strip()
    if not valor:
        print("Cancelado.")
        return None
    cookie = http.cookiejar.Cookie(
        0, "osCsid", valor, None, False,
        ".imvu.com", True, True, "/", True, False,
        None, False, None, None, {})
    sessao = SessaoIMVU()
    sessao.cookies.set_cookie(cookie)
    print("Conferindo com o IMVU...")
    if sessao.validar():
        sessao.salvar_sessao()
        return sessao
    print()
    print("Esse valor nao funcionou:", sessao.ultimo_erro)
    print("Confira se o IMVU esta logado nesse navegador e se copiou a")
    print("coluna 'Value' da linha osCsid (o valor, nao o nome).")
    return None


def tentar_senha():
    usuario = input("Seu nick do IMVU: ").strip()
    if not usuario:
        print("Cancelado.")
        return None
    senha = getpass.getpass("Sua senha (nao aparece na tela): ")
    if not senha:
        print("Cancelado.")
        return None

    sessao = SessaoIMVU()
    codigo = ""
    for _ in range(4):
        ok = sessao.login(codigo, usuario, senha)
        if ok:
            return sessao
        if sessao.estado == "precisa_2fa":
            print()
            codigo = input("Codigo de verificacao que o IMVU mandou (email/app): ").strip()
            if not codigo:
                print("Cancelado.")
                return None
            continue
        print()
        print("Nao deu certo:", sessao.ultimo_erro)
        print("Confira o nick e a senha e rode este atalho de novo.")
        return None

    print()
    print("Nao deu certo: codigo de verificacao incorreto varias vezes.")
    print("Espere alguns minutos e rode o atalho de novo.")
    return None


def gerar_texto_env():
    """Gera o texto da IMVU_SESSAO enxuto (so o cookie osCsid + dados)."""
    criar_pastas()
    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        dados = json.load(f)
    enxutos = []
    for c in dados.get("cookies") or []:
        if c.get("name") != "osCsid":
            continue
        limpo = {k: c.get(k) for k in
                 ("version", "name", "value", "domain", "path",
                  "secure", "expires", "discard")
                 if c.get(k) is not None}
        if c.get("rest"):
            limpo["rest"] = c["rest"]
        enxutos.append(limpo)
    if not enxutos:
        enxutos = dados.get("cookies") or []
    enxuto = {
        "cookies": enxutos,
        "sauce": dados.get("sauce"),
        "cid": dados.get("cid"),
        "nick": dados.get("nick"),
    }
    conteudo = json.dumps(enxuto, separators=(",", ":"), ensure_ascii=True)
    texto = base64.b64encode(conteudo.encode("utf-8")).decode("ascii")
    with open(ARQUIVO_ENV, "w", encoding="utf-8") as f:
        f.write(texto)
    return texto


def main():
    print()
    print("=== IMVU Status - login do dono (presente gratis) ===")
    print("Sua senha vai SO para o site do IMVU. Ela nao e salva nem")
    print("enviada a ninguem. Nao mostre esta janela a terceiros.")
    print()

    sessao = tentar_firefox()

    if sessao:
        print("Sessao puxada do Firefox! Logado como %s." % (sessao.nick or "?"))
    else:
        print()
        print("Escolha como conectar:")
        print("  1) Colar o valor do cookie osCsid (rapido; funciona no Chrome)")
        print("  2) Digitar nick e senha")
        escolha = input("Digite 1 ou 2: ").strip()
        if escolha == "1":
            sessao = tentar_cookie()
        else:
            sessao = tentar_senha()
        if not sessao:
            return 1

    gerar_texto_env()

    print()
    print("=" * 62)
    print("TUDO PRONTO! Login feito como %s." % (sessao.nick or "conta"))
    print()
    print("1) O texto para colar no Render ficou salvo em:")
    print("   %s" % ARQUIVO_ENV)
    print("   (abra com o Bloco de Notas, aperte Ctrl+A e depois Ctrl+C)")
    print()
    print("2) No Render, dentro do seu servico:")
    print("   Environment -> clique na variavel IMVU_SESSAO")
    print("      e troque o valor pelo texto novo (apague o antigo)")
    print()
    print("NUNCA mande esse texto nem a sua senha para ninguem.")
    print("Se a sessao expirar algum dia, rode este atalho de novo.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
