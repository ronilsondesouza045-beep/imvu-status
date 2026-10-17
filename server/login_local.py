# -*- coding: utf-8 -*-
"""
IMVU Status - login local da conta do dono (roda NO SEU PC, uma vez).

Primeiro tenta puxar a sessao pronta do Firefox (se voce estiver logado no
IMVU por la, serve sem digitar nada). Se nao achar, pede o nick, a senha
(a senha nao aparece na tela) e o codigo de verificacao (2FA) se o IMVU pedir.

Obs.: o Chrome NAO permite mais ler os cookies (protecao App-Bound Encryption
do Chrome 127+), por isso usamos Firefox ou digitacao.

No fim:

  - salva a sessao em server/dados/session.json (fica so no seu PC e e
    ignorado pelo GitHub);
  - gera o texto pronto para colar no Render como variavel IMVU_SESSAO.

A senha NAO e salva em lugar nenhum e nao e enviada a ninguem.
NUNCA mande a senha nem o texto gerado para outra pessoa (nem para o ChatGPT).
"""

import base64
import getpass
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from app import SESSION_FILE, SessaoIMVU, criar_pastas  # noqa: E402

ARQUIVO_ENV = os.path.join(os.path.dirname(SESSION_FILE), "sessao_env.txt")


def tentar_firefox():
    try:
        import browser_cookie3
    except Exception:
        return None
    try:
        jar = browser_cookie3.firefox(domain_name="imvu.com")
    except Exception:
        return None
    cookies = list(jar)
    if not cookies:
        return None
    sessao = SessaoIMVU()
    for cookie in cookies:
        sessao.cookies.set_cookie(cookie)
    if sessao.validar():
        sessao.salvar_sessao()
        return sessao
    return None


def gerar_texto_env():
    criar_pastas()
    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        conteudo = f.read().strip()
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
    print("Tentando puxar a sessao do Firefox (se voce estiver logado")
    print("no IMVU por la, nem precisa digitar a senha)...")
    sessao = tentar_firefox()
    if sessao:
        print("Sessao puxada do Firefox! Logado como %s." % (sessao.nick or "?"))
    else:
        print("Nao achei sessao no Firefox (ou nao esta instalado).")
        print("Sem problema: digite os dados abaixo.")
        print()
        usuario = input("Seu nick do IMVU: ").strip()
        if not usuario:
            print("Cancelado.")
            return 1
        senha = getpass.getpass("Sua senha (nao aparece na tela): ")
        if not senha:
            print("Cancelado.")
            return 1

        sessao = SessaoIMVU()
        codigo = ""
        ok = False
        for _ in range(4):
            ok = sessao.login(codigo, usuario, senha)
            if ok:
                break
            if sessao.estado == "precisa_2fa":
                print()
                codigo = input("Codigo de verificacao que o IMVU mandou (email/app): ").strip()
                if not codigo:
                    print("Cancelado.")
                    return 1
                continue
            print()
            print("Nao deu certo:", sessao.ultimo_erro)
            print("Confira o nick e a senha e rode este atalho de novo.")
            return 1

        if not ok:
            print()
            print("Nao deu certo: codigo de verificacao incorreto varias vezes.")
            print("Espere alguns minutos e rode o atalho de novo.")
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
    print("   Environment -> Add Environment Variable")
    print("      Key:   IMVU_SESSAO")
    print("      Value: (cole o texto do arquivo)")
    print()
    print("NUNCA mande esse texto nem a sua senha para ninguem.")
    print("Se a sessao expirar algum dia, rode este atalho de novo.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
