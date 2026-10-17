# IMVU Status

Consulta de nick do IMVU — versão **terminal** (CLI) e **app Android** (KivyMD) com interface Material.

Usa os endpoints públicos do próprio site do IMVU (`api.imvu.com`) e o diretório público de salas.
Não faz varredura de pessoas nem fura localização desativada: mostra apenas o que o IMVU expõe publicamente.

## O que mostra

- Online / offline e disponibilidade (quando exposta)
- Sala atual, com indicação de **pública** ou **privada** (quando o IMVU expõe)
- Detecção de possível modo "aparecer offline" (o perfil ainda aponta a última sala)
- Diretório público de salas + dono, lotação e participantes de cada sala
- **Card do avatar**: imagem do avatar (renderização oficial ou foto de perfil), seguidores e o look atual
- Monitoramento a cada X segundos
- Login opcional na sua conta (aumenta o que aparece)
- **Aba PRESENTE**: presente grátis — a pessoa digita o nick, vê o status (já recebeu?) e a **wishlist** dela, e resgata 1 item (limite por avatar e teto diário, configurados no backend)
- **Aba APP**: verifica se há versão nova, mostra o que mudou e botão para baixar o APK

## Estrutura

```
main.py                  -> app Android (KivyMD)
server/app.py            -> backend do presente grátis (usa sua conta SEM expor ela)
server/login_local.py    -> login local do dono (gera o texto da sessão pro Render)
configurar_presente.bat  -> atalho de duplo clique que chama o login local
render.yaml              -> configuração pronta do deploy no Render
buildozer.spec           -> configuração do APK
.github/workflows/       -> GitHub Actions que compila o APK de graça
cli/imvu_status.py       -> versão terminal (menu numerado, Python puro)
assets/                  -> ícone e presplash do app
```

## Presente grátis (aba PRESENTE)

Quem usa o app **nunca vê** a conta que paga os presentes: o app só conversa com o
seu backend, e o backend guarda a conta em segredo. **Nunca coloque usuário/senha
no app nem no GitHub** — se a credencial vazar dentro do APK, qualquer pessoa
descompila e rouba a conta.

### Jeito fácil (recomendado)

1. **Faça o login no seu PC** (a senha fica só aí, ninguém vê):
   dê **duplo clique em `configurar_presente.bat`** (precisa do
   [Python](https://www.python.org/downloads/) instalado; marque "Add Python to PATH").
   - Se você usa o **Firefox** logado no IMVU, ele puxa a sessão sozinho — **nem digita senha**.
   - Senão, digite o nick, a senha (não aparece na tela) e o código 2FA se o IMVU pedir.
   - Por que não puxa do **Chrome**? O Chrome 127+ criptografa os cookies com
     App-Bound Encryption (proteção anti-roubo); só daria com técnicas de invasão
     que antivírus bloqueiam — por isso usamos Firefox ou digitação única.

   No fim ele salva a sessão e mostra o arquivo
   `server\dados\sessao_env.txt` — é o texto que você vai colar no Render.
2. **Suba o backend no Render** (grátis):
   - No [render.com](https://render.com), crie a conta → **New → Blueprint** → escolha este repositório
     (o arquivo `render.yaml` já configura tudo).
   - Em **Environment**, preencha:
     - `IMVU_SESSAO` → cole o texto do passo 1
     - `GIFT_PRODUCT_ID` → ID do item que será presenteado (pegue na URL do item: `products_id=NNN`)
     - `ADMIN_TOKEN` → invente um código secreto (ex: `meu-token-123`)
   - Pronto: o servidor acorda já logado, sem senha e sem 2FA no servidor.
3. **Ligue o app no backend**: no `main.py`, troque `PRESENTE_URL` pela URL do
   Render (ex: `https://imvu-status-presente.onrender.com`) e publique a versão nova.

Se algum dia a sessão expirar, é só rodar o `configurar_presente.bat` de novo e
atualizar o `IMVU_SESSAO` no Render.

### Jeito manual (alternativa)

Também dá para colocar `IMVU_USER` e `IMVU_PASS` nas variáveis do Render. Se o
IMVU pedir 2FA, envie o código uma vez:

```
curl -X POST https://SEU-BACKEND.onrender.com/admin/2fa -H "X-Admin-Token: SEU_TOKEN" -H "Content-Type: application/json" -d "{\"codigo\": \"123456\"}"
```

Outras variáveis (têm padrão): `GIFT_CREDITOS` (500), `GIFT_LIMITE_PESSOA` (1),
`GIFT_LIMITE_DIARIO` (10), `GIFT_MENSAGEM`. Confira tudo em
`GET /admin/status` (com o header `X-Admin-Token`).

### Avisos

- O plano grátis do Render "dorme" e o disco é apagado em atualizações do servidor
  (a lista de quem já recebeu pode zerar; use um **Disk** se quiser persistência).
- Presente é **por item** (produto do catálogo de até `GIFT_CREDITOS`), não crédito puro.
- Automatizar presente é ainda mais sensível que consulta: **pode dar ban na conta**.
  Use `GIFT_LIMITE_DIARIO` baixo e acompanhe o `/admin/status`.
- Nunca suba a pasta `server/dados/` (já está no `.gitignore`): ela contém a sessão da sua conta.
- A senha **nunca** é salva: nem no seu PC, nem no servidor, nem no GitHub.

## Como usar a versão terminal

```
python cli/imvu_status.py
```

## Como pegar o APK

**Versões publicadas (recomendado):** aba **Releases** do repositório — o APK fica anexado na versão mais recente.

**Builds de desenvolvimento:** o APK é compilado automaticamente pelo GitHub Actions a cada push:

1. Abra a aba **Actions** do repositório
2. Entre no workflow **build-apk** → último run
3. Baixe o artifact **imvu-status-apk** (contém o `.apk` de debug)
4. No celular, instale com "instalar de fontes desconhecidas" habilitado

O primeiro build demora ~20 a 40 minutos (o Android SDK/NDK é baixado do zero).

Quer compilar local? Precisa de Docker:

```
docker run --rm -v "$PWD":/home/user/hostcwd kivy/buildozer:latest android debug
```

## Como publicar uma atualização (dono do repositorio)

1. Edite o app normalmente (correcoes/melhorias)
2. Atualize a versao nos dois lugares (precisam ser iguais):
   - `main.py` → `APP_VERSAO = "x.y.z"`
   - `buildozer.spec` → `version = x.y.z`
3. Commit e push na `main` (isso valida o build no workflow build-apk)
4. Crie a tag anotada com o changelog (o texto vira as "notas" que o app mostra):
   ```
   git tag -a v1.2.0 -m "Corrigido: ..." -m "Novo: ..."
   git push origin v1.2.0
   ```
5. O workflow **release-apk** compila e cria a Release com o APK anexado
6. Quem tem o app instalado ve a atualizacao na aba **APP** (verifica sozinho ao abrir, ou no botao) com o changelog e o botao de download

## Limitações (importante)

- API **não oficial**: o IMVU pode mudar ou bloquear a qualquer momento; evite excesso de requisições.
- **Localização desligada continua desligada**: se a pessoa desativou a sala, ela não aparece em lugar nenhum — e este projeto não tenta contornar isso.
- Uso por sua conta e risco (ToS do IMVU).
