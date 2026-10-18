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
- **Aba PRESENTE**: 100 créditos grátis por avatar a cada 24h — a pessoa digita o nick, confirma que a conta é dela, vê a **wishlist** e resgata (limites anti-abuso configurados no backend)
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
   - Senão, escolha **1** para colar o valor do cookie `osCsid` (funciona no **Chrome**,
     Edge, qualquer navegador — o próprio assistente mostra o passo a passo do F12)
     ou **2** para digitar o nick, a senha (não aparece na tela) e o código 2FA se o IMVU pedir.
   - Por que não puxa do **Chrome** sozinho? O Chrome 127+ criptografa os cookies com
     App-Bound Encryption (proteção anti-roubo) — por isso a cópia manual ou o Firefox.

   No fim ele salva a sessão e mostra o arquivo
   `server\dados\sessao_env.txt` — é o texto que você vai colar no Render.
2. **Suba o backend no Render** (grátis):
   - No [render.com](https://render.com), crie a conta → **New → Blueprint** → escolha este repositório
     (o arquivo `render.yaml` já configura tudo).
    - Em **Environment**, preencha:
      - `IMVU_SESSAO` → cole o texto do passo 1
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

Outras variáveis (têm padrão): `PRESENTE_CREDITOS` (100), `GIFT_JANELA_HORAS` (24),
`GIFT_IDADE_MINIMA_DIAS` (7), `GIFT_LIMITE_IP_AVATARES` (3), `GIFT_LIMITE_DIARIO` (10),
`KEEPALIVE_MINUTOS` (5 — o servidor "acorda" a sessão do IMVU de tempos em tempos
pra ela não expirar por inatividade; `0` desliga), `GIFT_MENSAGEM`. Confira tudo em
`GET /admin/status` (com o header `X-Admin-Token`).

### Login do app (conta por email — Firebase)

O app agora abre numa **tela de login bonita** (com o banner) e o resgate fica ligado
à conta de email: trocar de celular, desinstalar o app ou usar VPN **não** libera outro
nick — se entrar com a mesma conta, continua no mesmo avatar.

Para ativar (uma vez só, grátis, ~5 min):

1. Abra [console.firebase.google.com](https://console.firebase.google.com) com sua conta Google.
2. **Criar projeto** → nome `imvu-status` → desmarque Analytics → Criar.
3. Menu **Criação → Authentication → Começar** → aba **Método de login** →
   ative **E-mail/senha** → Salvar.
4. ⚙ **Configurações do projeto → Seus apps** → clique no ícone **`</>`** (Web) →
   apelido `app` → Registrar.
5. Copie a **Web API Key** (`AIzaSy...`) e cole em **dois lugares**:
   - no `main.py`, constante `FIREBASE_KEY`;
   - no Render, variável `FIREBASE_KEY` (o campo já existe no `render.yaml`).
6. Publique a versão nova do app.

Como funciona no dia a dia:

- o app **lembra o login** (não precisa digitar sempre; só se sair da conta);
- o e-mail precisa estar **confirmado** (o Firebase manda o link; confira o spam);
- **uma conta = um avatar para sempre** (e um avatar = uma conta; trocar de nick
  na conta não burla o vínculo);
- os limites de aparelho, IP, idade de conta e teto diário continuam valendo;
   - para desligar a exigência de login (manutenção): `EXIGIR_LOGIN=0` no Render.

### Login com Google (opcional)

Além do email/senha, o app tem o botão **CONECTAR COM GOOGLE** na tela de login.

As credenciais **nunca** vão para o repositório (o GitHub bloqueia segredos):

- **No build (GitHub):** configure os secrets do repositório `GOOGLE_CLIENT_ID` e
  `GOOGLE_CLIENT_SECRET` (Settings → Secrets and variables → Actions). O workflow cria
  o `google_creds.py` sozinho antes de compilar.
- **Localmente:** crie o arquivo `google_creds.py` (gitignored) na raiz com:
  `GOOGLE_CLIENT_ID = "..."` e `GOOGLE_CLIENT_SECRET = "..."`.

Como obter (uma vez só, grátis):

1. [Google Auth Platform](https://console.cloud.google.com/auth/clients) (projeto do
   Firebase) → **Clientes → Criar cliente** → tipo **Aplicativo para computador**.
2. Copie o **ID do cliente** e a **Chave secreta** para os secrets/arquivo acima.
3. Em **Público-alvo**, clique em **Publicar app** (sem isso só a sua conta entra).
4. (O login por email/senha continua funcionando igual, sem mudar nada.)

### Avisos

- O plano grátis do Render "dorme" e o disco é apagado em atualizações do servidor
  (a lista de quem já recebeu pode zerar; use um **Disk** se quiser persistência).
- Presente é **100 créditos** a cada 24h por avatar (confira `PRESENTE_CREDITOS` e
  `GIFT_JANELA_HORAS`).
- **1 aparelho = 1 avatar para sempre**: o app manda o ID do aparelho, então trocar
  para VPN/4G ou reinstalar o app **não** libera outro nick. Contas com menos de
  `GIFT_IDADE_MINIMA_DIAS` não recebem (anti nick-fake) e há limite de avatares
  diferentes por IP.
- **Conta do app (email)**: com o login ativado, o resgate exige conta com email
  confirmado e fica ligado a ela para sempre — mesmo em outro celular, o vínculo
  continua. Quem tenta outro nick na mesma conta ou outro nick no mesmo aparelho
  é bloqueado.
- A partir desta versão o APK é assinado sempre com a mesma chave (`ci/.android/debug.keystore`),
  então as atualizações instalam por cima das anteriores (não precisa desinstalar).
- Automatizar presente é ainda mais sensível que consulta: **pode dar ban na conta**.
  Use `GIFT_LIMITE_DIARIO` baixo e acompanhe o `/admin/status`.
- Nunca suba a pasta `server/dados/` (já está no `.gitignore`): ela contém a sessão da sua conta.
- A senha **nunca** é salva: nem no seu PC, nem no servidor, nem no GitHub.

## Problemas comuns

### Diz "Pode resgatar" (ou "presente disponível") mas as pessoas não conseguem receber

A sessão do IMVU (a que o `configurar_presente.bat` capturou) **expira quando fica
muito tempo sem uso** — igual ao site, que desloga sozinho. O servidor agora:

- **mantém a sessão viva sozinho** (`KEEPALIVE_MINUTOS`, padrão 5 minutos);
- **não sobrescreve** mais a sessão salva com a cópia antiga da `IMVU_SESSAO` a cada
  reinício (a variável só é aplicada se mudou ou se não havia sessão nenhuma);
- **só mostra "pode resgatar" quando a sessão do servidor foi validada de verdade**.

Detalhe importante: abrir o IMVU Desktop/Firefox na sua máquina **não** renova a
sessão do servidor — o servidor tem a própria sessão, separada.

**Não deixe servidor de teste aberto no seu PC**: se rodar `server/app.py` aqui
para testar, feche depois (Ctrl+C ou feche a janela). Rodando escondido, ele tenta
renovar uma sessão velha direto do seu IP e o IMVU pode derrubar a sessão de
segurança.

No plano grátis o Render **dorme após ~15 min sem visitas** e aí o keep-alive
também para. Para funcionar 24h, crie um monitor grátis (ex.:
[cron-job.org](https://cron-job.org) ou [UptimeRobot](https://uptimerobot.com))
apontando para `https://SEU-BACKEND.onrender.com/health` **a cada 5 a 10 minutos**:
isso mantém o serviço acordado e a sessão renovando.

Se mesmo assim a sessão morrer, rode o `configurar_presente.bat` de novo, atualize
a `IMVU_SESSAO` no Render (o servidor detecta o texto novo e recarrega sozinho) e
olhe os logs do Render — o keep-alive avisa quando a sessão cai. Alternativa:
configure `IMVU_USER` e `IMVU_PASS` no Render (veja "Jeito manual") para o servidor
entrar de novo sozinho quando a sessão cair (se o IMVU pedir 2FA, use o `/admin/2fa`).

### Login com Google: só a conta do dono consegue entrar

O app OAuth do Google está **em modo de teste** — só as contas da lista de
testadores entram (por isso criação de conta e login por email funcionam normal).
Para liberar geral, abra
[console.cloud.google.com/auth/audience](https://console.cloud.google.com/auth/audience)
(no mesmo projeto do Firebase) → **Público-alvo** → **Publicar app**. É grátis e
rápido: os escopos usados (`email`/`profile`) não exigem verificação do Google.

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
