import asyncio
import datetime
import logging
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import analytics
from .alerts import criar_alertas, disparar_webhooks
from .config import RETENCAO_ALERTAS_DIAS, RETENCAO_EVENTOS_DIAS, RETENCAO_SNAPSHOTS_DIAS
from .db import (
    Alert,
    Avatar,
    Look,
    Room,
    RoomSnapshot,
    SessionLocal,
    StatusEvent,
    Watch,
    utcnow,
)
from .events import barramento

log = logging.getLogger("imvu.services")
_travas: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def normalizar_nick(nick: str) -> str:
    return (nick or "").strip()


def buscar_avatar(db: Session, nick: str) -> Avatar | None:
    nick = normalizar_nick(nick)
    if not nick:
        return None
    return db.scalar(select(Avatar).where(Avatar.nick == nick)) or db.scalar(
        select(Avatar).where(Avatar.nick.ilike(nick))
    )


def avatar_publico(avatar: Avatar) -> dict:
    return {
        "nick": avatar.nick,
        "display_name": avatar.display_name or avatar.nick,
        "cid": avatar.cid,
        "online": avatar.online,
        "availability": avatar.availability,
        "sala": (
            {
                "rid": avatar.room_rid,
                "nome": avatar.room_name,
                "privacidade": avatar.room_privacy,
            }
            if avatar.room_name
            else None
        ),
        "pais": avatar.country,
        "registrado": avatar.registered,
        "seguidores": avatar.followers,
        "marcas": [],
        "tagline": avatar.tagline,
        "foto_perfil": avatar.look_image or avatar.avatar_url,
        "avatar_3d": avatar.avatar_url,
        "look": (
            {"assinatura": avatar.look_signature, "corpo": avatar.look_name}
            if avatar.look_signature
            else None
        ),
        "aparente_offline": (not avatar.online) and bool(avatar.room_name),
        "atualizado_em": analytics.iso(avatar.last_seen),
    }


def _dispositivos(db: Session, avatar_id: int) -> set[str]:
    return {
        linha
        for linha in db.scalars(
            select(Watch.device).where(Watch.avatar_id == avatar_id, Watch.notificar.is_(True))
        ).all()
    }


def _aplicar_status(db: Session, dados: dict):
    nick = normalizar_nick(dados.get("nick") or "")
    cid = str(dados.get("cid") or "").strip() or None
    if not nick:
        return None, [], []

    avatar = None
    if cid:
        avatar = db.scalar(select(Avatar).where(Avatar.cid == cid))
    if avatar is None:
        avatar = buscar_avatar(db, nick)

    agora = utcnow()
    novo = avatar is None
    eventos: list[dict] = []
    if novo:
        avatar = Avatar(
            nick=nick,
            cid=cid,
            first_seen=agora,
            last_seen=agora,
        )
        db.add(avatar)
        db.flush()
    else:
        if cid and not avatar.cid:
            avatar.cid = cid
        if nick and nick != avatar.nick:
            try:
                avatar.nick = nick
                db.flush()
            except Exception:
                db.rollback()
                avatar = buscar_avatar(db, nick)
                if avatar is None:
                    return None, [], []

    antes_online = bool(avatar.online)
    antes_sala = avatar.room_name
    antes_rid = avatar.room_rid
    antes_disp = avatar.availability
    antes_look = avatar.look_signature
    antes_suspeita = bool(avatar.last_suspected_offline)

    online = bool(dados.get("online"))
    sala = dados.get("sala") or None
    look = dados.get("look") or None
    disponibilidade = (dados.get("availability") or "").strip().lower() or None

    avatar.display_name = dados.get("display_name") or avatar.display_name or nick
    avatar.avatar_url = dados.get("avatar_3d") or dados.get("foto_perfil") or avatar.avatar_url
    avatar.followers = dados.get("seguidores") if dados.get("seguidores") is not None else avatar.followers
    avatar.country = dados.get("pais") or avatar.country
    avatar.registered = dados.get("registrado") or avatar.registered
    avatar.tagline = dados.get("tagline") or avatar.tagline
    avatar.online = online
    avatar.availability = disponibilidade
    avatar.last_seen = agora
    if online:
        if not antes_online or avatar.last_online_at is None:
            avatar.last_online_at = agora
    else:
        if antes_online or avatar.last_offline_at is None:
            avatar.last_offline_at = agora

    if sala:
        avatar.room_rid = sala.get("rid")
        avatar.room_name = sala.get("nome")
        avatar.room_privacy = sala.get("privacidade")
    else:
        avatar.room_rid = None
        avatar.room_name = None
        avatar.room_privacy = None

    suspeita = bool(dados.get("aparente_offline")) and (antes_online or avatar.last_online_at is not None)
    avatar.last_suspected_offline = suspeita

    novos_eventos: list[dict] = []
    if novo:
        kind = "online" if online else "offline"
        _evento(db, avatar, kind, agora, sala=sala, detalhe={"baseline": True})
        novos_eventos.append({"kind": kind, "body": "primeiro registro", "baseline": True})
    else:
        if online != antes_online:
            kind = "online" if online else "offline"
            _evento(db, avatar, kind, agora, sala=sala)
            novos_eventos.append(
                {
                    "kind": kind,
                    "body": ("Sala atual: " + str(sala.get("nome"))) if sala and online else "",
                }
            )
        if sala and (sala.get("nome") != antes_sala or sala.get("rid") != antes_rid) and (online or antes_sala):
            _evento(db, avatar, "sala", agora, sala=sala)
            novos_eventos.append(
                {
                    "kind": "sala",
                    "room_name": sala.get("nome"),
                    "body": "Sala atual: " + str(sala.get("nome")),
                }
            )
        if disponibilidade != antes_disp and disponibilidade:
            _evento(db, avatar, "disponibilidade", agora, detalhe={"de": antes_disp, "para": disponibilidade})
            novos_eventos.append({"kind": "disponibilidade", "body": disponibilidade})
        if suspeita and not antes_suspeita:
            _evento(db, avatar, "suspeita", agora, sala=sala)
            novos_eventos.append({"kind": "suspeita", "body": "continua com sala registrada mesmo offline"})

        if look and look.get("assinatura") and look["assinatura"] != antes_look:
            imagem = dados.get("avatar_3d") or dados.get("foto_perfil")
            db.add(
                Look(
                    avatar_id=avatar.id,
                    signature=look["assinatura"],
                    caption=look.get("corpo") or ("look com %s itens" % look.get("total", "?")),
                    image_url=imagem,
                    items=look.get("total"),
                    ts=agora,
                )
            )
            avatar.look_signature = look["assinatura"]
            avatar.look_name = look.get("corpo")
            avatar.look_image = imagem
            if antes_look:
                _evento(db, avatar, "look", agora, detalhe={"anterior": antes_look, "novo": look["assinatura"]})
                novos_eventos.append({"kind": "look", "body": "look atualizado"})
            else:
                _evento(db, avatar, "look", agora, detalhe={"baseline": True})
        elif not avatar.look_image and (dados.get("avatar_3d") or dados.get("foto_perfil")):
            avatar.look_image = dados.get("avatar_3d") or dados.get("foto_perfil")

    if dados.get("seguidores") is not None:
        avatar.followers = dados.get("seguidores")

    alertas = []
    if not novo:
        alertas = criar_alertas(db, avatar, [e for e in novos_eventos if not e.get("baseline")])
    db.flush()
    avatar.total_events = int(avatar.total_events or 0) + len(novos_eventos)
    return avatar, novos_eventos, alertas


def _evento(db: Session, avatar: Avatar, kind: str, ts: datetime.datetime, sala=None, detalhe=None):
    db.add(
        StatusEvent(
            avatar_id=avatar.id,
            ts=ts,
            kind=kind,
            room_rid=(sala or {}).get("rid"),
            room_name=(sala or {}).get("nome"),
            detail=detalhe,
        )
    )


async def processar_status(dados: dict):
    if not dados or not dados.get("nick"):
        return None
    nick = normalizar_nick(dados["nick"])
    async with _travas[nick.lower()]:
        with SessionLocal() as db:
            avatar, eventos, alertas = _aplicar_status(db, dados)
            if avatar is None:
                return None
            db.commit()
            avatar_id = avatar.id
            nome = avatar.nick
            dispositivos = _dispositivos(db, avatar_id)
            resumo = analytics.resumo_avatar_rapido(db, avatar)
            publico = avatar_publico(avatar)

        mudancas = [e for e in eventos if not e.get("baseline")]
        if mudancas:
            await barramento.publicar(
                {
                    "tipo": "status",
                    "nick": nome,
                    "online": publico["online"],
                    "sala": publico["sala"],
                    "availability": publico["availability"],
                    "eventos": [e["kind"] for e in mudancas],
                },
                dispositivos=dispositivos,
                global_=True,
            )
        for alerta in alertas:
            payload = {
                "tipo": "alerta",
                "id": alerta["id"],
                "nick": alerta["nick"],
                "kind": alerta["kind"],
                "titulo": alerta["title"],
                "corpo": alerta["body"],
            }
            await barramento.publicar(payload, dispositivos={alerta["device"]}, global_=False)
            asyncio.create_task(disparar_webhooks(alerta["device"], alerta))
        return {"avatar_id": avatar_id, "eventos": eventos, "alertas": len(alertas), "resumo": resumo}


def registrar_sala(info: dict):
    if not info or not info.get("rid"):
        return None
    agora = utcnow()
    with SessionLocal() as db:
        sala = db.scalar(select(Room).where(Room.rid == info["rid"]))
        if not sala:
            sala = Room(rid=info["rid"], first_seen=agora, last_seen=agora)
            db.add(sala)
            db.flush()
        sala.name = info.get("nome") or sala.name
        sala.owner = info.get("dono") or sala.owner
        sala.capacity = int(info["capacidade"]) if info.get("capacidade") else sala.capacity
        sala.last_seen = agora
        ultimo = db.scalar(
            select(RoomSnapshot)
            .where(RoomSnapshot.room_id == sala.id)
            .order_by(RoomSnapshot.ts.desc())
            .limit(1)
        )
        criado = False
        if not ultimo or (agora - ultimo.ts).total_seconds() >= 10:
            db.add(RoomSnapshot(room_id=sala.id, ts=agora, count=int(info.get("online") or 0), capacity=sala.capacity))
            sala.last_snapshot_at = agora
            criado = True
        db.commit()
        payload = {
            "tipo": "sala",
            "rid": sala.rid,
            "nome": sala.name,
            "online": int(info.get("online") or 0),
            "capacidade": sala.capacity,
        } if criado else None
    if payload:
        asyncio.create_task(barramento.publicar(payload, global_=True))
    return payload


def descobrir_salas(lista: list[dict]):
    if not lista:
        return 0
    agora = utcnow()
    novas = 0
    with SessionLocal() as db:
        existentes = {
            rid
            for rid in db.scalars(select(Room.rid).where(Room.rid.in_([item["rid"] for item in lista[:200]]))).all()
        }
        for item in lista[:200]:
            if item["rid"] in existentes:
                sala = db.scalar(select(Room).where(Room.rid == item["rid"]))
                if sala:
                    sala.name = item.get("nome") or sala.name
                    sala.last_seen = agora
                continue
            db.add(Room(rid=item["rid"], name=item.get("nome"), first_seen=agora, last_seen=agora))
            novas += 1
        db.commit()
    return novas


def aplicar_retencao():
    agora = utcnow()
    with SessionLocal() as db:
        db.execute(
            delete(RoomSnapshot).where(
                RoomSnapshot.ts < agora - datetime.timedelta(days=RETENCAO_SNAPSHOTS_DIAS)
            )
        )
        db.execute(
            delete(StatusEvent).where(
                StatusEvent.ts < agora - datetime.timedelta(days=RETENCAO_EVENTOS_DIAS)
            )
        )
        db.execute(
            delete(Alert).where(Alert.ts < agora - datetime.timedelta(days=RETENCAO_ALERTAS_DIAS))
        )
        db.commit()
    log.info("retencao aplicada")
