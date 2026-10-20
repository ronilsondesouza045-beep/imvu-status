import datetime
import math
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import TZ
from .db import Alert, Avatar, Room, RoomSnapshot, StatusEvent, utcnow

FUSO = ZoneInfo(TZ)
DIAS_SEMANA = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


def _local(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(FUSO)


def iso(dt: datetime.datetime | None) -> str | None:
    if not dt:
        return None
    return _local(dt).isoformat()


def _minutos(segundos: float) -> int:
    return max(0, int(round(segundos / 60.0)))


def _intervalos(eventos: list[StatusEvent], online_inicial: bool, desde: datetime.datetime, agora: datetime.datetime):
    intervalos = []
    inicio = desde if online_inicial else None
    for evento in eventos:
        if evento.kind == "online":
            if inicio is None:
                inicio = evento.ts
        elif evento.kind == "offline":
            if inicio is not None:
                intervalos.append((inicio, evento.ts))
                inicio = None
    if inicio is not None and agora > inicio:
        intervalos.append((inicio, agora))
    return intervalos


def _eventos_avatar(db: Session, avatar_id: int, desde: datetime.datetime | None = None, limite: int | None = None):
    consulta = select(StatusEvent).where(StatusEvent.avatar_id == avatar_id)
    if desde:
        consulta = consulta.where(StatusEvent.ts >= desde)
    consulta = consulta.order_by(StatusEvent.ts.asc())
    if limite:
        consulta = consulta.limit(limite)
    return list(db.scalars(consulta).all())


def prever_avatar(db: Session, avatar: Avatar, agora: datetime.datetime | None = None) -> dict:
    agora = agora or utcnow()
    limite = agora - datetime.timedelta(days=90)
    eventos = db.scalars(
        select(StatusEvent)
        .where(StatusEvent.avatar_id == avatar.id, StatusEvent.kind == "online", StatusEvent.ts >= limite)
        .order_by(StatusEvent.ts.asc())
    ).all()
    amostras = len(eventos)
    if amostras < 2:
        return {
            "amostras": amostras,
            "confianca": 0.0,
            "janelas": [],
            "proxima": None,
            "em_minutos": None,
            "aviso": "ainda nao ha historico suficiente; acompanhe por mais alguns dias",
        }

    matriz = [[0.0] * 24 for _ in range(7)]
    for evento in eventos:
        local = _local(evento.ts)
        idade_dias = max(0.0, (agora - evento.ts).total_seconds() / 86400.0)
        peso = math.exp(-idade_dias / 35.0)
        matriz[local.weekday()][local.hour] += peso

    suavizada = [[0.0] * 24 for _ in range(7)]
    for dow in range(7):
        for hora in range(24):
            suavizada[dow][hora] = (
                matriz[dow][hora]
                + 0.45 * matriz[dow][(hora - 1) % 24]
                + 0.45 * matriz[dow][(hora + 1) % 24]
            )

    melhor = max(max(linha) for linha in suavizada)
    if melhor <= 0:
        melhor = 1e-9

    agora_local = _local(agora)
    candidatos = []
    for passo in range(1, 24 * 7 + 1):
        instante = agora_local + datetime.timedelta(hours=passo)
        score = suavizada[instante.weekday()][instante.hour]
        if score > 0:
            candidatos.append((score, instante))

    if not candidatos:
        return {
            "amostras": amostras,
            "confianca": 0.0,
            "janelas": [],
            "proxima": None,
            "em_minutos": None,
            "aviso": "sem padrao suficiente para prever",
        }

    candidatos.sort(key=lambda item: item[0], reverse=True)
    janelas = []
    usados = []
    for score, instante in candidatos:
        if any(abs((instante - usado).total_seconds()) < 3 * 3600 for usado in usados):
            continue
        usados.append(instante)
        janelas.append(
            {
                "dia_semana": DIAS_SEMANA[instante.weekday()],
                "hora": instante.hour,
                "quando": instante.isoformat(),
                "forca": round(min(1.0, score / melhor), 3),
            }
        )
        if len(janelas) >= 3:
            break

    limite_proximo = melhor * 0.65
    proxima = None
    for score, instante in sorted(candidatos, key=lambda item: item[1]):
        if score >= limite_proximo and (instante - agora_local).total_seconds() > 1800:
            proxima = instante
            break
    if proxima is None:
        proxima = min(candidatos, key=lambda item: item[1])[1]

    minutos = max(0, int((proxima - agora_local).total_seconds() / 60))
    confianca = round(min(1.0, amostras / 40.0), 2)
    return {
        "amostras": amostras,
        "confianca": confianca,
        "janelas": janelas,
        "proxima": proxima.isoformat(),
        "em_minutos": minutos,
    }


def estatisticas_avatar(db: Session, avatar: Avatar, horas: int = 168, agora: datetime.datetime | None = None) -> dict:
    agora = agora or utcnow()
    desde = agora - datetime.timedelta(hours=horas)
    eventos = _eventos_avatar(db, avatar.id, desde=desde)
    desde_presenca = agora - datetime.timedelta(days=14)
    eventos_presenca = _eventos_avatar(db, avatar.id, desde=desde_presenca)
    anteriores = db.scalars(
        select(StatusEvent)
        .where(StatusEvent.avatar_id == avatar.id, StatusEvent.ts < desde_presenca)
        .order_by(StatusEvent.ts.desc())
        .limit(1)
    ).all()
    online_inicial = bool(anteriores and anteriores[0].kind == "online")
    intervalos = _intervalos(eventos_presenca, online_inicial, desde_presenca, agora)
    minutos_online = _minutos(
        sum(
            max(0.0, (min(fim, agora) - max(inicio, desde)).total_seconds())
            for inicio, fim in intervalos
        )
    )

    historico = _eventos_avatar(db, avatar.id, desde=agora - datetime.timedelta(days=180))
    recorde_online = 0
    recorde_offline = 0
    inicio_online = None
    ultimo_offline = None
    for evento in historico:
        if evento.kind == "online":
            if ultimo_offline is not None:
                recorde_offline = max(recorde_offline, int((evento.ts - ultimo_offline).total_seconds() / 60))
                ultimo_offline = None
            if inicio_online is None:
                inicio_online = evento.ts
        elif evento.kind == "offline":
            if inicio_online is not None:
                recorde_online = max(recorde_online, int((evento.ts - inicio_online).total_seconds() / 60))
                inicio_online = None
            ultimo_offline = evento.ts
    if inicio_online is not None and avatar.online:
        recorde_online = max(recorde_online, int((agora - inicio_online).total_seconds() / 60))
    if ultimo_offline is not None and not avatar.online:
        recorde_offline = max(recorde_offline, int((agora - ultimo_offline).total_seconds() / 60))

    dias = []
    for deslocamento in range(13, -1, -1):
        dia_local = (_local(agora) - datetime.timedelta(days=deslocamento)).date()
        inicio_dia = datetime.datetime.combine(dia_local, datetime.time.min, tzinfo=FUSO)
        fim_dia = inicio_dia + datetime.timedelta(days=1)
        total = 0.0
        for inicio, fim in intervalos:
            a = max(inicio.replace(tzinfo=datetime.timezone.utc), inicio_dia)
            b = min(fim.replace(tzinfo=datetime.timezone.utc), fim_dia)
            if b > a:
                total += (b - a).total_seconds()
        dias.append({"dia": dia_local.isoformat(), "minutos": _minutos(total)})

    horas_contagem = [0] * 24
    heat = [[0] * 24 for _ in range(7)]
    for evento in eventos:
        if evento.kind == "online":
            local = _local(evento.ts)
            horas_contagem[local.hour] += 1
            heat[local.weekday()][local.hour] += 1

    salas = Counter()
    for evento in eventos:
        if evento.room_name:
            salas[evento.room_name] += 1
    top_salas = [{"nome": nome, "vezes": vezes} for nome, vezes in salas.most_common(8)]

    linha_tempo = [
        {
            "ts": iso(evento.ts),
            "tipo": evento.kind,
            "sala": evento.room_name,
            "detalhe": evento.detail or {},
        }
        for evento in eventos[-80:][::-1]
    ]

    agora_local = _local(agora)
    return {
        "nick": avatar.nick,
        "desde": iso(desde),
        "ate": iso(agora),
        "estatisticas": {
            "online_agora": avatar.online,
            "ultima_vez": iso(avatar.last_seen),
            "primeira_vez": iso(avatar.first_seen),
            "eventos": len(eventos),
            "minutos_online": minutos_online,
            "horas_online": round(minutos_online / 60.0, 1),
            "recorde_online_min": recorde_online,
            "recorde_offline_min": recorde_offline,
            "ultimo_offline": iso(avatar.last_offline_at),
            "sala_mais_frequentada": top_salas[0] if top_salas else None,
            "dia_local": agora_local.strftime("%Y-%m-%d"),
        },
        "grafico": {"dias": dias, "horas": horas_contagem},
        "heatmap": heat,
        "salas": top_salas,
        "eventos": linha_tempo,
        "previsao": prever_avatar(db, avatar, agora),
    }


def resumo_avatar_rapido(db: Session, avatar: Avatar) -> dict:
    previsao = prever_avatar(db, avatar)
    return {
        "primeira_vez": iso(avatar.first_seen),
        "ultima_vez": iso(avatar.last_seen),
        "total_eventos": avatar.total_events,
        "previsao": previsao,
    }


def top_salas(db: Session, horas: int = 24, limite: int = 20, agora: datetime.datetime | None = None) -> list[dict]:
    agora = agora or utcnow()
    desde = agora - datetime.timedelta(hours=horas)
    linhas = db.execute(
        select(
            RoomSnapshot.room_id,
            RoomSnapshot.ts,
            RoomSnapshot.count,
            RoomSnapshot.capacity,
            Room.rid,
            Room.name,
            Room.owner,
        )
        .join(Room, Room.id == RoomSnapshot.room_id)
        .where(RoomSnapshot.ts >= desde)
        .order_by(RoomSnapshot.ts.asc())
    ).all()

    agrupado: dict[int, dict] = defaultdict(lambda: {"counts": [], "tempos": [], "capacidade": None})
    info_salas = {}
    for room_id, ts, count, capacidade, rid, nome, dono in linhas:
        grupo = agrupado[room_id]
        grupo["counts"].append(int(count or 0))
        grupo["tempos"].append(ts)
        if capacidade:
            grupo["capacidade"] = int(capacidade)
        info_salas[room_id] = (rid, nome, dono)

    resultado = []
    for room_id, grupo in agrupado.items():
        counts = grupo["counts"]
        if not counts:
            continue
        recentes = counts[-3:]
        anteriores = counts[-6:-3]
        media_recente = sum(recentes) / len(recentes)
        media_anterior = sum(anteriores) / len(anteriores) if anteriores else media_recente
        rid, nome, dono = info_salas[room_id]
        resultado.append(
            {
                "room_id": room_id,
                "rid": rid,
                "nome": nome,
                "dono": dono,
                "online": counts[-1],
                "capacidade": grupo["capacidade"],
                "media": round(sum(counts) / len(counts), 1),
                "pico": max(counts),
                "amostras": len(counts),
                "tendencia": round(media_recente - media_anterior, 1),
                "atualizado": iso(grupo["tempos"][-1]),
            }
        )
    resultado.sort(key=lambda item: (item["media"], item["online"]), reverse=True)
    return resultado[:limite]


def historico_sala(db: Session, rid: str, horas: int = 24, agora: datetime.datetime | None = None):
    agora = agora or utcnow()
    desde = agora - datetime.timedelta(hours=horas)
    sala = db.scalar(select(Room).where(Room.rid == rid))
    if not sala:
        return None
    snapshots = db.scalars(
        select(RoomSnapshot)
        .where(RoomSnapshot.room_id == sala.id, RoomSnapshot.ts >= desde)
        .order_by(RoomSnapshot.ts.asc())
    ).all()
    pontos = [{"ts": iso(s.ts), "online": s.count, "capacidade": s.capacity} for s in snapshots]
    counts = [s.count for s in snapshots]
    melhor_hora = None
    por_hora: dict[int, list[int]] = defaultdict(list)
    for s in snapshots:
        por_hora[_local(s.ts).hour].append(s.count)
    if por_hora:
        hora, valores = max(por_hora.items(), key=lambda item: sum(item[1]) / len(item[1]))
        melhor_hora = {"hora": hora, "media": round(sum(valores) / len(valores), 1)}
    return {
        "sala": {
            "rid": sala.rid,
            "nome": sala.name,
            "dono": sala.owner,
            "capacidade": sala.capacity,
            "primeira_vez": iso(sala.first_seen),
            "ultima_vez": iso(sala.last_seen),
        },
        "pontos": pontos,
        "resumo": {
            "amostras": len(counts),
            "online_agora": counts[-1] if counts else 0,
            "media": round(sum(counts) / len(counts), 1) if counts else 0,
            "pico": max(counts) if counts else 0,
            "melhor_horario": melhor_hora,
        },
    }


def heatmap_salas(db: Session, horas: int = 168, agora: datetime.datetime | None = None) -> dict:
    agora = agora or utcnow()
    desde = agora - datetime.timedelta(hours=horas)
    snapshots = db.execute(
        select(RoomSnapshot.ts, RoomSnapshot.count).where(RoomSnapshot.ts >= desde)
    ).all()
    soma = [[0.0] * 24 for _ in range(7)]
    contagem = [[0] * 24 for _ in range(7)]
    for ts, count in snapshots:
        local = _local(ts)
        soma[local.weekday()][local.hour] += count or 0
        contagem[local.weekday()][local.hour] += 1
    matriz = [
        [
            round(soma[d][h] / contagem[d][h], 1) if contagem[d][h] else 0.0
            for h in range(24)
        ]
        for d in range(7)
    ]
    return {
        "dias": DIAS_SEMANA,
        "horas": list(range(24)),
        "matriz": matriz,
        "amostras": contagem,
        "total_amostras": sum(sum(linha) for linha in contagem),
    }


def resumo_dia(db: Session, horas: int = 24, agora: datetime.datetime | None = None) -> dict:
    agora = agora or utcnow()
    desde = agora - datetime.timedelta(hours=horas)

    eventos = db.execute(
        select(StatusEvent.kind, StatusEvent.ts, StatusEvent.room_name, StatusEvent.avatar_id)
        .where(StatusEvent.ts >= desde)
    ).all()
    online = sum(1 for e in eventos if e.kind == "online")
    offline = sum(1 for e in eventos if e.kind == "offline")
    trocas_sala = sum(1 for e in eventos if e.kind == "sala")
    trocas_look = sum(1 for e in eventos if e.kind == "look")
    avatares_vistos = len({e.avatar_id for e in eventos})

    novos_avatares_qtd = len(db.scalars(select(Avatar.id).where(Avatar.first_seen >= desde)).all())
    novas_salas_qtd = len(db.scalars(select(Room.id).where(Room.first_seen >= desde)).all())
    alertas_qtd = len(db.scalars(select(Alert.id).where(Alert.ts >= desde)).all())

    salas = top_salas(db, horas=horas, limite=5, agora=agora)
    top_sala = salas[0] if salas else None

    heat = heatmap_salas(db, horas=min(horas, 168), agora=agora)
    pico_hora = None
    melhor = -1.0
    for dow in range(7):
        for hora in range(24):
            if heat["amostras"][dow][hora] and heat["matriz"][dow][hora] > melhor:
                melhor = heat["matriz"][dow][hora]
                pico_hora = {"dia": heat["dias"][dow], "hora": hora, "media": melhor}

    fatos = {
        "janela_horas": horas,
        "online": online,
        "offline": offline,
        "trocas_sala": trocas_sala,
        "trocas_look": trocas_look,
        "avatares_vistos": avatares_vistos,
        "novos_avatares": novos_avatares_qtd,
        "novas_salas": novas_salas_qtd,
        "alertas": alertas_qtd,
        "sala_destaque": top_sala,
        "horario_pico": pico_hora,
    }

    linhas = []
    if online or offline:
        linhas.append(
            f"Nas ultimas {horas}h: {online} entradas online, {offline} saidas e "
            f"{avatares_vistos} avatares com atividade registrada."
        )
    else:
        linhas.append(f"Nenhuma mudanca de status registrada nas ultimas {horas}h.")
    if top_sala:
        linhas.append(
            f"Sala em destaque: {top_sala['nome']} (media {top_sala['media']}, "
            f"pico {top_sala['pico']}, agora {top_sala['online']})."
        )
    if pico_hora:
        linhas.append(
            f"Horario mais movimentado: {pico_hora['dia']} as {pico_hora['hora']}h "
            f"(media {pico_hora['media']} pessoas por sala)."
        )
    if novas_salas_qtd:
        linhas.append(f"{novas_salas_qtd} sala(s) nova(s) entraram no diretorio publico.")
    if alertas_qtd:
        linhas.append(f"{alertas_qtd} alerta(s) entregues aos aparelhos monitorados.")

    return {"gerado_em": iso(agora), "fatos": fatos, "linhas": linhas}
