import asyncio
import datetime
import hmac
import json
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from . import analytics
from .ai import resumir_com_ia
from .alerts import url_segura
from .collector import coletor
from .config import (
    ADMIN_TOKEN,
    APP_VERSAO,
    COLETOR_ATIVO,
    CORS_ORIGINS,
    DIRETORIO_MINUTOS,
    MAX_WATCH_POR_DEVICE,
    POLL_AVATAR,
    POLL_SALA,
    POLL_WATCH,
    RAIZ,
    RATE_API_MIN,
    RATE_STATUS_MIN,
    TZ,
    orcamento_descricao,
)
from .db import (
    Alert,
    Avatar,
    Look,
    Room,
    RoomSnapshot,
    SessionLocal,
    StatusEvent,
    Watch,
    Webhook,
    init_db,
)
from .events import barramento
from .imvu import IMVUErro, cliente
from .services import (
    avatar_publico,
    buscar_avatar,
    descobrir_salas,
    normalizar_nick,
    processar_status,
)

log = logging.getLogger("imvu.api")

WEB_DIR = RAIZ / "web"
RE_DEVICE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
RE_RID = re.compile(r"(\d+-\d+)")
RE_NICK = re.compile(r"^[^\x00-\x1f/\\<>]{2,40}$")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    if COLETOR_ATIVO:
        coletor.iniciar()
    try:
        yield
    finally:
        if COLETOR_ATIVO:
            await coletor.parar()
        await cliente.fechar()


app = FastAPI(
    title="IMVU Status API",
    version=APP_VERSAO,
    description=(
        "Historico publico, tempo real, previsao e analytics de salas do IMVU. "
        "Somente dados publicos; respeite os termos do IMVU e a LGPD."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_rate: dict[str, list[float]] = {}


@app.middleware("http")
async def limitar_requisicoes(request: Request, call_next):
    caminho = request.url.path
    if caminho.startswith("/api/") and "/events" not in caminho and request.method in ("GET", "POST", "DELETE"):
        ip = request.client.host if request.client else "desconhecido"
        status_rota = caminho.startswith("/api/status") or caminho.startswith("/api/public")
        limite = RATE_STATUS_MIN if status_rota else RATE_API_MIN
        chave = f"{ip}:{'status' if status_rota else 'api'}"
        agora = time.monotonic()
        marcas = [m for m in _rate.get(chave, []) if agora - m < 60]
        if len(marcas) >= limite:
            _rate[chave] = marcas
            return JSONResponse({"erro": "muitas requisicoes; espere um instante"}, status_code=429)
        marcas.append(agora)
        _rate[chave] = marcas
    return await call_next(request)


def _validar_device(device: str) -> str:
    device = (device or "").strip()
    if not RE_DEVICE.fullmatch(device):
        raise HTTPException(400, "identificador de aparelho invalido")
    return device


def _validar_nick(nick: str) -> str:
    nick = normalizar_nick(nick)
    if not RE_NICK.fullmatch(nick):
        raise HTTPException(400, "nick invalido")
    return nick


def _validar_kind(kind: str) -> str:
    kind = (kind or "").strip().lower()
    if kind not in ("discord", "telegram", "generico"):
        raise HTTPException(400, "tipo de webhook invalido")
    return kind


def _serializar_alerta(alerta: Alert, nick: str | None) -> dict:
    return {
        "id": alerta.id,
        "tipo": alerta.kind,
        "titulo": alerta.title,
        "corpo": alerta.body,
        "quando": analytics.iso(alerta.ts),
        "nick": nick,
        "visto": alerta.seen,
    }


def _serializar_webhook(hook: Webhook) -> dict:
    return {
        "id": hook.id,
        "tipo": hook.kind,
        "url": hook.url,
        "chat_id": hook.chat_id,
        "eventos": hook.events or [],
        "ativo": hook.ativo,
        "criado_em": analytics.iso(hook.created_at),
    }


class WatchIn(BaseModel):
    device: str = Field(min_length=8, max_length=64)
    nick: str = Field(min_length=2, max_length=40)
    notificar: bool = True


class AlertSeenIn(BaseModel):
    device: str = Field(min_length=8, max_length=64)
    ids: list[int] = Field(default_factory=list)


class WebhookIn(BaseModel):
    device: str = Field(min_length=8, max_length=64)
    tipo: str = "generico"
    url: str = Field(min_length=12, max_length=800)
    chat_id: str | None = Field(default=None, max_length=64)
    eventos: list[str] = Field(default_factory=list)


_cache_status: dict[str, tuple[float, dict]] = {}


@app.get("/api/health")
def health():
    estado_db = True
    try:
        with SessionLocal() as db:
            db.execute(select(1))
    except Exception:
        estado_db = False
    return {
        "ok": estado_db,
        "versao": APP_VERSAO,
        "coletor": coletor.estado if COLETOR_ATIVO else {"desligado": True},
        "conexoes": barramento.conexoes(),
        "db": estado_db,
    }


@app.get("/api/config")
def config():
    return {
        "versao": APP_VERSAO,
        "fuso": TZ,
        "coletor_ativo": COLETOR_ATIVO,
        "intervalos": {
            "watch": POLL_WATCH,
            "avatar": POLL_AVATAR,
            "sala": POLL_SALA,
            "diretorio_minutos": DIRETORIO_MINUTOS,
        },
        "max_watch_por_device": MAX_WATCH_POR_DEVICE,
        "recursos": [
            "tempo_real",
            "historico",
            "looks",
            "previsao",
            "salas",
            "heatmap",
            "alertas",
            "webhooks",
            "resumo",
            "api_publica",
        ],
    }


@app.get("/api/status/{nick}")
async def obter_status(nick: str):
    nick = _validar_nick(nick)
    chave = nick.lower()
    agora = time.monotonic()
    item = _cache_status.get(chave)
    if item and item[0] > agora:
        dados = dict(item[1])
    else:
        try:
            dados = await cliente.check(nick, detalhes=True)
        except IMVUErro as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        if not dados:
            raise HTTPException(404, "nick nao encontrado no IMVU")
        _cache_status[chave] = (agora + 5, dados)
        if len(_cache_status) > 500:
            _cache_status.clear()
        try:
            await processar_status(dados)
        except Exception:
            log.exception("falha ao gravar status de %s", nick)
    resposta = dict(dados)
    with SessionLocal() as db:
        avatar = buscar_avatar(db, dados.get("nick") or nick)
        if avatar:
            resposta.update(analytics.resumo_avatar_rapido(db, avatar))
            resposta["historico_disponivel"] = True
        else:
            resposta["historico_disponivel"] = False
    return resposta


@app.get("/api/public/status/{nick}")
async def status_publico(nick: str):
    return await obter_status(nick)


@app.get("/api/avatar/{nick}/history")
def historico_avatar(nick: str, horas: int = Query(168, ge=1, le=720)):
    nick = _validar_nick(nick)
    with SessionLocal() as db:
        avatar = buscar_avatar(db, nick)
        if not avatar:
            raise HTTPException(404, "sem historico para esse nick; consulte o status primeiro")
        return analytics.estatisticas_avatar(db, avatar, horas=horas)


@app.get("/api/avatar/{nick}/looks")
def looks_avatar(nick: str, limite: int = Query(60, ge=1, le=200)):
    nick = _validar_nick(nick)
    with SessionLocal() as db:
        avatar = buscar_avatar(db, nick)
        if not avatar:
            raise HTTPException(404, "sem historico para esse nick")
        looks = db.scalars(
            select(Look).where(Look.avatar_id == avatar.id).order_by(Look.ts.desc()).limit(limite)
        ).all()
        return {
            "nick": avatar.nick,
            "looks": [
                {
                    "id": look.id,
                    "quando": analytics.iso(look.ts),
                    "imagem": look.image_url,
                    "legenda": look.caption,
                    "itens": look.items,
                }
                for look in looks
            ],
        }


@app.get("/api/avatar/{nick}/prediction")
def previsao_avatar(nick: str):
    nick = _validar_nick(nick)
    with SessionLocal() as db:
        avatar = buscar_avatar(db, nick)
        if not avatar:
            raise HTTPException(404, "sem historico para esse nick")
        return {"nick": avatar.nick, **analytics.prever_avatar(db, avatar)}


@app.get("/api/rooms/top")
def salas_top(horas: int = Query(24, ge=1, le=720), limite: int = Query(20, ge=1, le=100)):
    with SessionLocal() as db:
        return {"salas": analytics.top_salas(db, horas=horas, limite=limite)}


@app.get("/api/rooms/heatmap")
def salas_heatmap(horas: int = Query(168, ge=1, le=720)):
    with SessionLocal() as db:
        return analytics.heatmap_salas(db, horas=horas)


@app.get("/api/rooms/directory")
async def diretorio(letra: str = Query("A", min_length=1, max_length=1)):
    letra = (letra or "A").upper()
    try:
        lista = await cliente.diretorio(letra)
    except IMVUErro as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    novas = await asyncio.to_thread(descobrir_salas, lista)
    return {"letra": letra, "salas": lista, "novas": novas}


@app.get("/api/rooms/{rid}/history")
def historico_sala(rid: str, horas: int = Query(24, ge=1, le=720)):
    achado = RE_RID.search(rid)
    if not achado:
        raise HTTPException(400, "ID de sala invalido")
    with SessionLocal() as db:
        dados = analytics.historico_sala(db, achado.group(1), horas=horas)
        if not dados:
            raise HTTPException(404, "sala ainda sem historico; consulte o diretorio primeiro")
        return dados


@app.get("/api/digest")
async def digest(horas: int = Query(24, ge=1, le=168)):
    with SessionLocal() as db:
        dados = analytics.resumo_dia(db, horas=horas)
    texto_ia = await resumir_com_ia(dados["fatos"], dados["linhas"])
    return {**dados, "texto_ia": texto_ia}


@app.get("/api/watch")
def listar_watch(device: str = Query(...)):
    device = _validar_device(device)
    with SessionLocal() as db:
        linhas = db.execute(
            select(Watch, Avatar).join(Avatar, Avatar.id == Watch.avatar_id).where(Watch.device == device)
        ).all()
        saida = []
        for watch, avatar in linhas:
            publico = avatar_publico(avatar)
            publico["notificar"] = watch.notificar
            publico["adicionado_em"] = analytics.iso(watch.created_at)
            publico["previsao"] = analytics.prever_avatar(db, avatar)
            saida.append(publico)
        return {"avatares": saida}


@app.post("/api/watch")
async def adicionar_watch(corpo: WatchIn):
    device = _validar_device(corpo.device)
    nick = _validar_nick(corpo.nick)
    with SessionLocal() as db:
        total = db.scalar(select(func.count(Watch.id)).where(Watch.device == device))
        if (total or 0) >= MAX_WATCH_POR_DEVICE:
            raise HTTPException(400, f"limite de {MAX_WATCH_POR_DEVICE} avatares por aparelho")
        avatar = buscar_avatar(db, nick)
    if avatar is None:
        try:
            dados = await cliente.check(nick, detalhes=True)
        except IMVUErro as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        if not dados:
            raise HTTPException(404, "nick nao encontrado no IMVU")
        await processar_status(dados)
    with SessionLocal() as db:
        avatar = buscar_avatar(db, nick)
        if avatar is None:
            raise HTTPException(404, "nao foi possivel registrar o avatar")
        watch = db.scalar(
            select(Watch).where(Watch.device == device, Watch.avatar_id == avatar.id)
        )
        if watch:
            watch.notificar = corpo.notificar
        else:
            watch = Watch(device=device, avatar_id=avatar.id, notificar=corpo.notificar)
            db.add(watch)
        db.commit()
        publico = avatar_publico(avatar)
        publico["notificar"] = watch.notificar
        return {"ok": True, "avatar": publico}


@app.delete("/api/watch/{nick}")
def remover_watch(nick: str, device: str = Query(...)):
    device = _validar_device(device)
    nick = _validar_nick(nick)
    with SessionLocal() as db:
        avatar = buscar_avatar(db, nick)
        if avatar is None:
            raise HTTPException(404, "avatar nao encontrado no historico")
        resultado = db.execute(
            delete(Watch).where(Watch.device == device, Watch.avatar_id == avatar.id)
        )
        db.commit()
        if not resultado.rowcount:
            raise HTTPException(404, "esse avatar nao esta na sua lista")
        return {"ok": True}


@app.get("/api/alerts")
def listar_alertas(device: str = Query(...), limite: int = Query(50, ge=1, le=200)):
    device = _validar_device(device)
    with SessionLocal() as db:
        linhas = db.execute(
            select(Alert, Avatar.nick)
            .outerjoin(Avatar, Avatar.id == Alert.avatar_id)
            .where(Alert.device == device)
            .order_by(Alert.ts.desc())
            .limit(limite)
        ).all()
        return {"alertas": [_serializar_alerta(alerta, nick) for alerta, nick in linhas]}


@app.post("/api/alerts/seen")
def marcar_alertas(corpo: AlertSeenIn):
    device = _validar_device(corpo.device)
    with SessionLocal() as db:
        consulta = select(Alert).where(Alert.device == device)
        if corpo.ids:
            consulta = consulta.where(Alert.id.in_(corpo.ids))
        alertas = db.scalars(consulta).all()
        for alerta in alertas:
            alerta.seen = True
        db.commit()
        return {"ok": True, "marcados": len(alertas)}


@app.get("/api/events")
async def eventos_sse(request: Request, device: str = Query(...)):
    device = _validar_device(device)

    async def gerador():
        inscrito = barramento.assinar(dispositivo=device)
        try:
            yield "retry: 4000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evento = await asyncio.wait_for(inscrito.fila.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield "data: " + json.dumps(evento, ensure_ascii=False) + "\n\n"
        finally:
            barramento.remover(inscrito)

    return StreamingResponse(
        gerador(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    await ws.accept()
    inscrito = barramento.assinar(global_=True)
    try:
        while True:
            try:
                evento = await asyncio.wait_for(inscrito.fila.get(), timeout=20)
            except asyncio.TimeoutError:
                await ws.send_json({"tipo": "ping"})
                continue
            await ws.send_json(evento)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        barramento.remover(inscrito)


@app.get("/api/webhooks")
def listar_webhooks(device: str = Query(...)):
    device = _validar_device(device)
    with SessionLocal() as db:
        hooks = db.scalars(
            select(Webhook).where(Webhook.device == device).order_by(Webhook.created_at.desc())
        ).all()
        return {"webhooks": [_serializar_webhook(hook) for hook in hooks]}


@app.post("/api/webhooks")
def criar_webhook(corpo: WebhookIn):
    device = _validar_device(corpo.device)
    kind = _validar_kind(corpo.tipo)
    if not url_segura(corpo.url):
        raise HTTPException(400, "URL invalida: use https publico (sem localhost/rede interna)")
    if kind == "telegram" and not (corpo.chat_id or "").strip():
        raise HTTPException(400, "para o Telegram informe o chat_id")
    eventos = [e.strip().lower() for e in corpo.eventos if e.strip()]
    with SessionLocal() as db:
        total = db.scalar(select(func.count(Webhook.id)).where(Webhook.device == device))
        if (total or 0) >= 10:
            raise HTTPException(400, "limite de 10 webhooks por aparelho")
        hook = Webhook(
            device=device,
            kind=kind,
            url=corpo.url.strip(),
            chat_id=(corpo.chat_id or "").strip() or None,
            events=eventos,
        )
        db.add(hook)
        db.commit()
        return {"ok": True, "webhook": _serializar_webhook(hook)}


@app.delete("/api/webhooks/{hook_id}")
def remover_webhook(hook_id: int, device: str = Query(...)):
    device = _validar_device(device)
    with SessionLocal() as db:
        hook = db.get(Webhook, hook_id)
        if not hook or hook.device != device:
            raise HTTPException(404, "webhook nao encontrado")
        db.delete(hook)
        db.commit()
        return {"ok": True}


@app.get("/api/admin/status")
def admin_status(request: Request, token: str = Query("")):
    if not ADMIN_TOKEN:
        raise HTTPException(403, "ADMIN_TOKEN nao configurado")
    enviado = request.headers.get("X-Admin-Token") or token
    if not hmac.compare_digest(enviado, ADMIN_TOKEN):
        raise HTTPException(403, "token invalido")
    with SessionLocal() as db:
        contagens = {
            "avatares": db.scalar(select(func.count(Avatar.id))),
            "eventos": db.scalar(select(func.count(StatusEvent.id))),
            "salas": db.scalar(select(func.count(Room.id))),
            "snapshots": db.scalar(select(func.count(RoomSnapshot.id))),
            "alertas": db.scalar(select(func.count(Alert.id))),
            "webhooks": db.scalar(select(func.count(Webhook.id))),
        }
    return {
        "versao": APP_VERSAO,
        "coletor": coletor.estado,
        "conexoes": barramento.conexoes(),
        "contagens": contagens,
        "configuracao": orcamento_descricao(),
        "agora": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", include_in_schema=False)
def pagina_inicial():
    index = WEB_DIR / "index.html"
    if not index.exists():
        return JSONResponse({"api": APP_VERSAO, "docs": "/docs"})
    return FileResponse(index)


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    arquivo = WEB_DIR / "manifest.webmanifest"
    if arquivo.exists():
        return FileResponse(arquivo, media_type="application/manifest+json")
    return Response(status_code=204)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
