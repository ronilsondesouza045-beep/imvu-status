import asyncio
import datetime
import ipaddress
import json
import socket
import urllib.parse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import WEBHOOKS_ATIVOS, WEBHOOK_TIMEOUT
from .db import Alert, Avatar, SessionLocal, Watch, Webhook, utcnow

TITULOS = {
    "online": "{nick} entrou online",
    "offline": "{nick} ficou offline",
    "sala": "{nick} mudou de sala",
    "look": "{nick} trocou de look",
    "suspeita": "{nick} pode estar no modo aparecer offline",
    "disponibilidade": "{nick} mudou a disponibilidade",
}


def criar_alertas(db: Session, avatar: Avatar, eventos: list[dict]) -> list[dict]:
    if not eventos:
        return []
    watches = db.scalars(
        select(Watch).where(Watch.avatar_id == avatar.id, Watch.notificar.is_(True))
    ).all()
    if not watches:
        return []
    saida = []
    limite_ruido = utcnow() - datetime.timedelta(seconds=45)
    for watch in watches:
        for evento in eventos:
            recente = db.scalar(
                select(Alert)
                .where(
                    Alert.device == watch.device,
                    Alert.avatar_id == avatar.id,
                    Alert.kind == evento["kind"],
                    Alert.ts >= limite_ruido,
                )
                .limit(1)
            )
            if recente:
                continue
            titulo = TITULOS.get(evento["kind"], "{nick} atualizou").format(nick=avatar.nick)
            body = evento.get("body") or ""
            alerta = Alert(
                device=watch.device,
                avatar_id=avatar.id,
                kind=evento["kind"],
                title=titulo,
                body=body,
            )
            db.add(alerta)
            db.flush()
            saida.append(
                {
                    "id": alerta.id,
                    "device": watch.device,
                    "avatar_id": avatar.id,
                    "nick": avatar.nick,
                    "kind": evento["kind"],
                    "title": titulo,
                    "body": body,
                    "ts": alerta.ts,
                }
            )
    return saida


def _ip_publica(texto: str) -> bool:
    try:
        endereco = ipaddress.ip_address(str(texto).split("%", 1)[0])
    except ValueError:
        return False
    if endereco.version == 6 and endereco.ipv4_mapped:
        endereco = endereco.ipv4_mapped
    return endereco.is_global and not (
        endereco.is_private
        or endereco.is_loopback
        or endereco.is_link_local
        or endereco.is_reserved
        or endereco.is_multicast
        or endereco.is_unspecified
    )


def url_segura(url: str) -> bool:
    try:
        partes = urllib.parse.urlparse((url or "").strip())
        host = (partes.hostname or "").lower().strip(".")
        porta = partes.port or 443
    except ValueError:
        return False
    if partes.scheme != "https" or not host or partes.username or partes.password:
        return False
    if not 1 <= porta <= 65535:
        return False
    if host in ("localhost", "localhost.localdomain", "0.0.0.0", "::", "::1"):
        return False
    if host.endswith((".local", ".internal", ".lan", ".home", ".localhost", ".onion")):
        return False
    try:
        endereco = ipaddress.ip_address(host)
    except ValueError:
        endereco = None
    if endereco is not None:
        return _ip_publica(str(endereco))
    try:
        infos = socket.getaddrinfo(host, porta, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError):
        return False
    return bool(infos) and all(_ip_publica(info[4][0]) for info in infos)


def _payload_webhook(webhook: Webhook, alerta: dict) -> dict | None:
    texto = alerta["title"] + ("\n" + alerta["body"] if alerta.get("body") else "")
    if webhook.kind == "discord":
        return {"content": texto}
    if webhook.kind == "telegram":
        if not webhook.chat_id:
            return None
        return {"chat_id": webhook.chat_id, "text": texto, "disable_web_page_preview": True}
    return {
        "evento": alerta["kind"],
        "nick": alerta.get("nick"),
        "titulo": alerta["title"],
        "mensagem": alerta.get("body"),
        "quando": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


async def disparar_webhooks(device: str, alerta: dict):
    if not WEBHOOKS_ATIVOS:
        return
    with SessionLocal() as db:
        hooks = db.scalars(
            select(Webhook).where(Webhook.device == device, Webhook.ativo.is_(True))
        ).all()
        alvos = []
        for hook in hooks:
            eventos = hook.events or []
            if eventos and alerta["kind"] not in eventos:
                continue
            payload = _payload_webhook(hook, alerta)
            if payload is None:
                continue
            url = hook.url
            if hook.kind == "telegram":
                if url.rstrip("/").endswith("/sendMessage"):
                    alvo = url
                else:
                    alvo = url.rstrip("/") + "/sendMessage"
            else:
                alvo = url
            alvos.append((hook.kind, alvo, payload, hook.id))

    if not alvos:
        return
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=False) as cliente:
        for kind, url, payload, hook_id in alvos:
            if not await asyncio.to_thread(url_segura, url):
                _marcar_falha(hook_id, "URL deixou de ser publica/segura")
                continue
            try:
                resposta = await cliente.post(url, json=payload)
                if resposta.status_code >= 400:
                    _marcar_falha(hook_id, f"HTTP {resposta.status_code}")
            except Exception as exc:
                _marcar_falha(hook_id, str(exc)[:200])


def _marcar_falha(hook_id: int, motivo: str):
    with SessionLocal() as db:
        hook = db.get(Webhook, hook_id)
        if hook:
            hook.ativo = False
            db.commit()
        _ = motivo


def formatar_evento_alerta(evento: dict) -> dict:
    kind = evento["kind"]
    body = evento.get("body") or ""
    if kind == "sala" and evento.get("room_name"):
        body = f"Sala atual: {evento['room_name']}"
    return {"kind": kind, "body": body}


def json_seguro(valor) -> str:
    try:
        return json.dumps(valor, ensure_ascii=False)
    except Exception:
        return "{}"
