# IMVU Status 3.0

Observatorio publico do IMVU: status em tempo real, historico, previsao de horarios, analytics de salas, alertas, webhooks e API aberta. O projeto tem um app Android em KivyMD e um backend em FastAPI com painel web.

**Gratis e sem paywall.** Nao existe presente, wishlist, credito, login Google ou Firebase. Todos os recursos usam apenas dados publicos do IMVU.

> Este projeto nao e afiliado ao IMVU. "IMVU" e marca de seus respectivos donos. Use com responsabilidade: somente dados publicos, sem perseguir ou vigiar pessoas.

---

## Recursos

- **Status em tempo real** de qualquer avatar publico: online/offline, disponibilidade, sala exposta, pais, seguidores e ultimo acesso.
- **Perfil completo** do avatar: dados publicos do perfil, detalhes e **outfit atual** com precos em creditos.
- **Catalogo** de produtos com busca, ordenacao (relevancia, novidades, preco, avaliacao) e paginacao.
- **Salas ao vivo**: diretorio publico, salas ordenadas por ocupacao e detalhes da sala (foto, descricao, capacidade, idioma, dono).
- **Historico** com presenca por dia, atividade por hora, recordes de tempo online/offline, linha do tempo e salas frequentes.
- **Previsao** ponderada por recencia (matriz 7x24, janela de 90 dias): horarios mais provaveis de ficar online.
- **Galeria de looks** com imagens registradas quando o visual muda.
- **Salas**: ranking por ocupacao, tendencia, pico, media, historico de lotacao, diretorio publico e mapa de calor 7x24.
- **Acompanhamento**: lista por aparelho (ate 10 avatares) com alertas quando entram online.
- **SSE** (`/api/events`) para eventos em tempo real e **WebSocket** (`/ws/live`) para o painel.
- **Webhooks** para Discord, Telegram ou endpoint proprio, com filtros de evento.
- **Resumo diario** deterministico, com texto opcional gerado por IA compativel com OpenAI.
- **API publica** com OpenAPI em `/docs`.
- **App Android** com abas Inicio, Salas, Historico e Alertas, notificacoes locais e configuracao do servidor.
- **Painel completo no APK**: o botao de globo na barra superior abre o painel web (catalogo, perfil, salas, API) numa WebView nativa, sem sair do app.

---

## Arquitetura

```
IMVU (dados publicos)
        |
        v
  coletor assincrono  --->  SQLite/Postgres
        |                        |
        v                        v
   barramento (SSE/WS)      FastAPI + /docs
        |                        |
        v                        v
   app Android (KivyMD)     painel web (HTML/CSS/JS)
```

- O coletor roda junto com o processo web (`COLETOR_ATIVO=1`) ou sozinho: `python -m server.collector`.
- SQLite e o padrao local (`dados/imvu.db`). Em producao use `DATABASE_URL` para Postgres.
- O app nao fala com o IMVU diretamente: ele consome a sua API.
- Retencao automatica: snapshots de salas 14 dias, eventos 180 dias, alertas 60 dias.

---

## Rodar o backend

Requisitos: Python 3.11+.

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
$env:COLETOR_ATIVO="1"
$env:TZ="America/Sao_Paulo"
uvicorn server.app:app --reload
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
COLETOR_ATIVO=1 TZ=America/Sao_Paulo uvicorn server.app:app --reload
```

- Painel: `http://127.0.0.1:8000/`
- API/docs: `http://127.0.0.1:8000/docs`
- Saude: `http://127.0.0.1:8000/api/health`

Para so testar a API sem coletar dados:

```bash
COLETOR_ATIVO=0 uvicorn server.app:app
```

---

## App Android

O APK e gerado pelo GitHub Actions em cada push na `main` (artifact `imvu-status-apk`) ou em `workflow_dispatch`. Tags `v*` publicam um release com o APK.

Configuracao no `buildozer.spec`:

- `version = 3.0.0`
- Python 3.11.5, Kivy 2.3.0, KivyMD 1.2.0
- `requests`, `plyer`, `certifi`
- Permissoes: INTERNET, ACCESS_NETWORK_STATE, POST_NOTIFICATIONS
- `android.api = 33`, `android.minapi = 24`, NDK 25b (build estavel; veja Play Store abaixo)

No primeiro uso, abra **Configuracoes** no app e informe a URL do backend (ex.: `https://imvu-status.onrender.com`). O painel web fica na mesma URL.

O icone de globo na barra superior (tambem disponivel em Configuracoes) abre o **painel completo** na WebView nativa do Android, em modo `?app=1` para que os links abram na mesma janela. No desktop, o mesmo botao abre o painel no navegador. A WebView exige o backend em HTTPS; URLs `http://` podem ser bloqueadas pelo Android.

Notificacoes usam o canal local do Android (plyer). O app recebe eventos por SSE enquanto estiver em execucao; nao ha push via FCM quando o processo esta encerrado.

---

## Deploy

### Render

O `render.yaml` cria o servico `imvu-status` com:

```text
uvicorn server.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"
```

Atencao ao plano free:

- a instancia dorme apos alguns minutos sem acesso, interrompendo o coletor;
- o disco e efemero: SQLite pode ser perdido em cada deploy/restart.

Para uso continuo, escolha uma destas opcoes:

1. VPS pequena (inclusive Oracle Cloud free tier) com Docker e volume persistente;
2. Render pago com disco persistente;
3. Postgres externo (Neon, Supabase) via `DATABASE_URL`, mantendo apenas o disco para nada;
4. ping externo a cada 5-10 minutos para reduzir o sono (nao garante coleta 24/7).

### Docker

```bash
docker build -t imvu-status .
docker run -d --name imvu-status -p 8000:8000 -v imvu_dados:/app/dados \
  -e TZ=America/Sao_Paulo -e COLETOR_ATIVO=1 imvu-status
```

Com Postgres:

```bash
docker run -d --name imvu-status -p 8000:8000 \
  -e TZ=America/Sao_Paulo \
  -e COLETOR_ATIVO=1 \
  -e DATABASE_URL="postgresql+psycopg://usuario:senha@host:5432/banco" \
  imvu-status
```

### Variaveis de ambiente

| Variavel | Padrao | Descricao |
|---|---|---|
| `DATABASE_URL` | SQLite em `dados/imvu.db` | URL SQLAlchemy (Postgres recomendado em producao) |
| `COLETOR_ATIVO` | `1` | Liga o coletor no processo web |
| `TZ` | `America/Sao_Paulo` | Fuso usado em graficos e previsao |
| `POLL_WATCH` | `15` | Segundos entre checagens de avatares acompanhados |
| `POLL_AVATAR` | `120` | Segundos entre checagens de avatares recentes |
| `POLL_SALA` | `45` | Segundos entre checagens de salas |
| `SALAS_POR_CICLO` | `6` | Salas consultadas por ciclo |
| `DIRETORIO_MINUTOS` | `10` | Intervalo de descoberta no diretorio publico |
| `DETALHES_MINUTOS` | `10` | Intervalo para atualizar detalhes completos |
| `MAX_WATCH_POR_DEVICE` | `10` | Limite de avatares por aparelho |
| `RETENCAO_SNAPSHOTS_DIAS` | `14` | Retencao de snapshots de salas |
| `RETENCAO_EVENTOS_DIAS` | `180` | Retencao de eventos de status |
| `RETENCAO_ALERTAS_DIAS` | `60` | Retencao de alertas |
| `ADMIN_TOKEN` | vazio | Protege `/api/admin/status` (header `X-Admin-Token`) |
| `IA_API_KEY` | vazio | Chave para resumo por IA (opcional) |
| `IA_BASE_URL` | `https://api.openai.com/v1` | Endpoint compativel com OpenAI |
| `IA_MODEL` | `gpt-4o-mini` | Modelo do resumo |
| `CORS_ORIGINS` | `*` | Origens permitidas |
| `RATE_STATUS_MIN` | `40` | Limite de consultas de status por minuto por IP |
| `RATE_API_MIN` | `120` | Limite geral de API por minuto por IP |

---

## API publica

Base: `https://SEU-SERVIDOR`

| Metodo | Rota | Descricao |
|---|---|---|
| GET | `/api/status/{nick}` | status atual, sala, previsao |
| GET | `/api/public/status/{nick}` | alias publico do status |
| GET | `/api/avatar/{nick}/history?horas=168` | resumo, graficos, timeline e previsao |
| GET | `/api/avatar/{nick}/profile?produtos=30` | perfil completo e outfit atual |
| GET | `/api/avatar/{nick}/outfit?limite=40` | itens do outfit com precos |
| GET | `/api/avatar/{nick}/looks` | galeria de looks |
| GET | `/api/avatar/{nick}/prediction` | previsao de horarios |
| GET | `/api/catalog/search?q=dress&pagina=1&ordenar=relevancia&tipo=produtos` | busca no catalogo |
| GET | `/api/rooms/top?horas=24&limite=20` | ranking de salas |
| GET | `/api/rooms/heatmap?horas=168` | mapa de calor 7x24 |
| GET | `/api/rooms/directory?letra=A` | diretorio publico |
| GET | `/api/rooms/live?letra=A&limite=12` | salas online ordenadas por ocupacao |
| GET | `/api/rooms/{rid}` | detalhes publicos da sala |
| GET | `/api/rooms/{rid}/history?horas=24` | ocupacao de uma sala |
| GET | `/api/digest?horas=24` | resumo do dia (com `texto_ia` opcional) |
| GET | `/api/watch?device={id}` | avatares acompanhados |
| POST | `/api/watch` | adicionar acompanhamento |
| DELETE | `/api/watch/{nick}?device={id}` | remover acompanhamento |
| GET | `/api/alerts?device={id}` | alertas do aparelho |
| POST | `/api/alerts/seen` | marcar alertas como vistos |
| GET | `/api/events?device={id}` | stream SSE |
| WS | `/ws/live` | feed global do painel |
| GET | `/api/webhooks?device={id}` | webhooks configurados |
| POST | `/api/webhooks` | criar webhook |
| DELETE | `/api/webhooks/{id}?device={id}` | remover webhook |
| GET | `/api/health` | saude do sistema |
| GET | `/api/config` | configuracao publica |
| GET | `/api/admin/status` | contagens e orcamento (ADMIN_TOKEN) |

Exemplos:

```bash
curl https://SEU-SERVIDOR/api/status/algum_nick
curl "https://SEU-SERVIDOR/api/avatar/algum_nick/history?horas=168"
curl "https://SEU-SERVIDOR/api/avatar/algum_nick/profile?produtos=30"
curl "https://SEU-SERVIDOR/api/catalog/search?q=dress&ordenar=preco_asc"
curl "https://SEU-SERVIDOR/api/rooms/live?letra=A&limite=12"
curl "https://SEU-SERVIDOR/api/rooms/top?horas=24&limite=10"
curl "https://SEU-SERVIDOR/api/events?device=0123456789abcdef0123456789abcdef"
```

O `device` e um identificador anonimo de 8 a 64 caracteres (`A-Z a-z 0-9 _ -`). O app gera 32 caracteres hexadecimais e guarda localmente. Nao ha conta, senha ou dado pessoal.

Webhook `POST /api/webhooks`:

```json
{
  "device": "0123456789abcdef0123456789abcdef",
  "tipo": "discord",
  "url": "https://discord.com/api/webhooks/...",
  "chat_id": null,
  "eventos": ["online", "offline", "sala"]
}
```

Tipos: `discord`, `telegram`, `generico`. URLs internas/localhost sao bloqueadas. Para Telegram, informe `chat_id`.

---

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes usam SQLite temporario, `COLETOR_ATIVO=0` e nao acessam o IMVU.

---

## Estrutura

```
main.py                  app Android (KivyMD 1.2)
buildozer.spec           build do APK
assets/                  icone e splash
server/
  app.py                 FastAPI, rotas, SSE, WebSocket, estaticos
  collector.py           loop de coleta
  imvu.py                cliente publico do IMVU
  services.py            gravacao de status, salas e alertas
  analytics.py           estatisticas, previsao, heatmap, digest
  alerts.py              criacao de alertas e webhooks
  ai.py                  resumo opcional por IA
  db.py                  modelos SQLAlchemy
  config.py              variaveis de ambiente
  events.py              barramento SSE/WS
web/                     painel (index.html, style.css, app.js)
tests/                   pytest
render.yaml              blueprint do Render
Dockerfile               deploy em VPS/container
```

---

## Play Store

O APK do GitHub Actions e debug e serve para instalacao direta. Publicar no Google Play exige:

1. Conta de desenvolvedor: taxa unica de **US$ 25** e verificacao de identidade.
2. **AAB** em vez de APK (`android.release_artifact = aab`) e assinatura com keystore proprio.
3. **Target API 36** para novos apps/atualizacoes em 2026. O spec atual usa API 33 para build estavel; sera preciso atualizar python-for-android/NDK e testar antes de enviar.
4. **16 KB page size** (NDK 27+) para Android 15+.
5. Contas pessoais novas: **12 testadores** por **14 dias** antes de producao.
6. Politica de privacidade publica (LGPD), Data Safety e declaracao de dados coletados.
7. Cuidado com a marca: "IMVU Status" pode ser tratado como uso indevido de marca. Considere um nome neutro ("Status do Avatar") e mantenha o aviso de nao afiliacao.
8. Nao inclua auto-atualizacao por APK: o app atual nao baixa nem instala atualizacoes.

Alternativas sem taxa: GitHub Releases (APK direto), F-Droid (build reproduzivel e open source), Galaxy Store e AppGallery (processos proprios), ou distribuicao limitada para ate 20 dispositivos.

---

## Privacidade e limites

- Apenas dados publicos expostos pelo IMVU; nada de contas, senhas, cookies ou creditos.
- Sem rastreamento de relacoes entre pessoas, sem mapa de co-presenca e sem burlar "aparecer offline".
- Salas sao tratadas de forma agregada; nao ha lista de ocupantes.
- Alertas e webhooks sao opt-in por aparelho; `device` nao identifica uma pessoa.
- Respeite os Termos de Uso do IMVU, limites de requisicao e a LGPD. Nao use o projeto para perseguicao, assedio ou vigilancia.

## Licenca

Uso pessoal e educacional. Revise os termos do IMVU antes de operar em producao ou redistribuir.
