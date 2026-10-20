import datetime
import socket

from server.alerts import url_segura
from server.db import Alert, Avatar, Look, Room, RoomSnapshot, StatusEvent, utcnow


def criar_avatar(db, nick, cid, online=True):
    agora = utcnow()
    avatar = Avatar(
        nick=nick,
        cid=cid,
        display_name=nick.title(),
        avatar_url="https://example.com/avatar.png",
        followers=42,
        country="BR",
        online=online,
        availability="brb",
        room_rid="12345678-1",
        room_name="Sala Teste",
        room_privacy="public",
        first_seen=agora - datetime.timedelta(days=5),
        last_seen=agora,
        last_online_at=agora - datetime.timedelta(hours=1),
        total_events=4,
    )
    db.add(avatar)
    db.flush()
    db.add_all(
        [
            StatusEvent(
                avatar_id=avatar.id,
                ts=agora - datetime.timedelta(hours=3),
                kind="online",
                room_rid="12345678-1",
                room_name="Sala Teste",
            ),
            StatusEvent(
                avatar_id=avatar.id,
                ts=agora - datetime.timedelta(hours=2),
                kind="sala",
                room_rid="12345678-1",
                room_name="Sala Teste",
            ),
            StatusEvent(
                avatar_id=avatar.id,
                ts=agora - datetime.timedelta(hours=1),
                kind="offline",
                room_name="Sala Teste",
            ),
        ]
    )
    db.add(
        Look(
            avatar_id=avatar.id,
            ts=agora - datetime.timedelta(hours=2),
            signature="look-1",
            caption="look de teste",
            image_url="https://example.com/look.png",
        )
    )
    db.commit()
    return avatar.id


def criar_sala(db, rid, nome):
    agora = utcnow()
    sala = Room(rid=rid, name=nome, owner="Dono Teste", capacity=40)
    db.add(sala)
    db.flush()
    for indice in range(8):
        db.add(
            RoomSnapshot(
                room_id=sala.id,
                ts=agora - datetime.timedelta(hours=8 - indice),
                count=10 + indice,
                capacity=40,
            )
        )
    db.commit()
    return sala.id


def test_health_e_config(client):
    saude = client.get("/api/health")
    assert saude.status_code == 200
    assert saude.json()["versao"] == "3.0.0"
    assert saude.json()["coletor"]["desligado"] is True

    config = client.get("/api/config")
    assert config.status_code == 200
    assert "tempo_real" in config.json()["recursos"]


def test_paginas_do_dashboard(client):
    for rota in ("/", "/static/style.css", "/static/app.js", "/manifest.webmanifest"):
        resposta = client.get(rota)
        assert resposta.status_code == 200


def test_historico_completo(client, db):
    criar_avatar(db, "PerfilXYZ", "cid-1")
    historico = client.get("/api/avatar/PerfilXYZ/history?horas=168")
    assert historico.status_code == 200
    dados = historico.json()
    assert dados["nick"] == "PerfilXYZ"
    assert dados["estatisticas"]["eventos"] >= 3
    assert len(dados["grafico"]["dias"]) == 14
    assert len(dados["grafico"]["horas"]) == 24
    assert dados["previsao"]["amostras"] >= 1
    assert len(dados["eventos"]) >= 3

    previsao = client.get("/api/avatar/PerfilXYZ/prediction")
    assert previsao.status_code == 200
    assert "proxima" in previsao.json()

    looks = client.get("/api/avatar/PerfilXYZ/looks")
    assert looks.status_code == 200
    assert len(looks.json()["looks"]) == 1

    ausente = client.get("/api/avatar/naoexiste/history")
    assert ausente.status_code == 404


def test_salas_analytics(client, db):
    criar_sala(db, "87654321-1", "Sala Analytics")

    top = client.get("/api/rooms/top?horas=24&limite=10")
    assert top.status_code == 200
    salas = top.json()["salas"]
    assert any(sala["rid"] == "87654321-1" for sala in salas)

    history = client.get("/api/rooms/87654321-1/history?horas=24")
    assert history.status_code == 200
    dados = history.json()
    assert dados["sala"]["nome"] == "Sala Analytics"
    assert dados["resumo"]["amostras"] == 8
    assert len(dados["pontos"]) == 8

    heat = client.get("/api/rooms/heatmap?horas=168")
    assert heat.status_code == 200
    assert len(heat.json()["matriz"]) == 7
    assert all(len(linha) == 24 for linha in heat.json()["matriz"])


def test_digest(client):
    resposta = client.get("/api/digest?horas=24")
    assert resposta.status_code == 200
    dados = resposta.json()
    assert isinstance(dados["linhas"], list)
    assert dados["linhas"]
    assert "fatos" in dados


def test_watch_ciclo(client, db):
    device = "aparelhoteste123"
    criar_avatar(db, "avatarwatch", "cid-watch", online=False)

    criado = client.post(
        "/api/watch",
        json={"device": device, "nick": "avatarwatch", "notificar": True},
    )
    assert criado.status_code == 200
    assert criado.json()["ok"] is True

    lista = client.get("/api/watch", params={"device": device})
    assert lista.status_code == 200
    nicks = [item["nick"] for item in lista.json()["avatares"]]
    assert "avatarwatch" in nicks

    removido = client.delete("/api/watch/avatarwatch", params={"device": device})
    assert removido.status_code == 200
    restante = client.get("/api/watch", params={"device": device}).json()["avatares"]
    assert all(item["nick"] != "avatarwatch" for item in restante)

    invalido = client.get("/api/watch", params={"device": "curto"})
    assert invalido.status_code == 400


def test_alertas(client, db):
    device = "aparelhoalertas123"
    avatar_id = criar_avatar(db, "avataralerta", "cid-alerta")
    alerta = Alert(
        device=device,
        avatar_id=avatar_id,
        kind="online",
        title="Avatar entrou online",
        body="avataralerta esta online",
        seen=False,
    )
    db.add(alerta)
    db.commit()

    lista = client.get("/api/alerts", params={"device": device})
    assert lista.status_code == 200
    alertas = lista.json()["alertas"]
    assert len(alertas) == 1
    assert alertas[0]["visto"] is False

    marcado = client.post("/api/alerts/seen", json={"device": device, "ids": [alertas[0]["id"]]})
    assert marcado.status_code == 200
    assert marcado.json()["marcados"] == 1

    lista2 = client.get("/api/alerts", params={"device": device}).json()["alertas"]
    assert lista2[0]["visto"] is True


def test_webhooks_validacao_e_ciclo(client):
    device = "aparelhohooks12"

    invalido = client.post(
        "/api/webhooks",
        json={"device": device, "tipo": "generico", "url": "https://localhost/hook", "eventos": ["online"]},
    )
    assert invalido.status_code == 400

    telegram = client.post(
        "/api/webhooks",
        json={"device": device, "tipo": "telegram", "url": "https://api.telegram.org/bot123/sendMessage"},
    )
    assert telegram.status_code == 400

    criado = client.post(
        "/api/webhooks",
        json={
            "device": device,
            "tipo": "generico",
            "url": "https://93.184.216.34/hook",
            "eventos": ["online", "offline"],
        },
    )
    assert criado.status_code == 200
    hook_id = criado.json()["webhook"]["id"]

    lista = client.get("/api/webhooks", params={"device": device})
    assert lista.status_code == 200
    assert len(lista.json()["webhooks"]) == 1

    removido = client.delete(f"/api/webhooks/{hook_id}", params={"device": device})
    assert removido.status_code == 200
    assert client.get("/api/webhooks", params={"device": device}).json()["webhooks"] == []


def test_url_segura_bloqueia_rede_interna(monkeypatch):
    assert url_segura("https://93.184.216.34/hook")
    assert not url_segura("http://93.184.216.34/hook")
    assert not url_segura("https://127.0.0.1/hook")
    assert not url_segura("https://10.0.0.1/hook")
    assert not url_segura("https://[::1]/hook")
    assert not url_segura("https://localhost/hook")
    assert not url_segura("https://servico.internal/hook")
    assert not url_segura("https://usuario:senha@93.184.216.34/hook")

    monkeypatch.setattr(
        "server.alerts.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.10", 443))],
    )
    assert not url_segura("https://meudominio.example/hook")


def test_nick_invalido(client):
    resposta = client.get("/api/avatar/a/history")
    assert resposta.status_code == 400
    resposta = client.get("/api/status/%3Cscript%3E")
    assert resposta.status_code == 400


def test_perfil_e_outfit(client, monkeypatch):
    async def falso_perfil(nick, limite_outfit=40):
        return {
            "nick": nick,
            "display_name": nick.title(),
            "cid": "123",
            "online": True,
            "sala": {"rid": "12345678-1", "nome": "Sala Teste", "privacidade": "public"},
            "perfil": {"titulo": "Titulo", "imagem": None, "seguidores": 7, "seguindo": 3},
            "detalhes": {"idade": 21, "genero": "Feminino", "pais": "BR"},
            "outfit": {"total": 1, "itens": [{"id": 1, "nome": "Camisa", "preco": 100}]},
            "atualizado_em": "2026-01-01T00:00:00+00:00",
        }

    async def falso_check(nick, detalhes=False):
        return {"nick": nick}

    async def falso_resolver(nick):
        return {"rel": {"profile_outfit": "https://api.imvu.com/profile_outfit/profile_outfit-1"}}

    async def falso_outfit(rel, limite=40):
        return {"total": 1, "itens": [{"id": 1, "nome": "Camisa"}]}

    monkeypatch.setattr("server.app.cliente.perfil_completo", falso_perfil)
    monkeypatch.setattr("server.app.cliente.check", falso_check)
    monkeypatch.setattr("server.app.cliente.resolver_nick", falso_resolver)
    monkeypatch.setattr("server.app.cliente.outfit", falso_outfit)

    perfil = client.get("/api/avatar/SemHistoricoXYZ/profile?produtos=10")
    assert perfil.status_code == 200
    dados = perfil.json()
    assert dados["nick"] == "SemHistoricoXYZ"
    assert dados["perfil"]["seguidores"] == 7
    assert dados["outfit"]["itens"][0]["nome"] == "Camisa"
    assert dados["historico_disponivel"] is False

    roupa = client.get("/api/avatar/SemHistoricoXYZ/outfit?limite=5")
    assert roupa.status_code == 200
    assert roupa.json()["nick"] == "SemHistoricoXYZ"
    assert roupa.json()["total"] == 1


def test_catalogo_endpoint(client, monkeypatch):
    async def falso_catalogo(termo, pagina=1, ordenar="relevancia", tipo="produtos"):
        return {
            "termo": termo,
            "pagina": pagina,
            "paginas": 2,
            "ordenar": ordenar,
            "tipo": tipo,
            "itens": [{"id": 10, "nome": "Vestido", "preco": 500}],
        }

    monkeypatch.setattr("server.app.cliente.catalogo", falso_catalogo)

    resposta = client.get("/api/catalog/search", params={"q": "dress", "pagina": 2})
    assert resposta.status_code == 200
    assert resposta.json()["itens"][0]["nome"] == "Vestido"
    assert resposta.json()["pagina"] == 2

    assert client.get("/api/catalog/search", params={"q": ""}).status_code == 422
    assert client.get("/api/catalog/search", params={"q": "x", "ordenar": "invalido"}).status_code == 400
    assert client.get("/api/catalog/search", params={"q": "x", "tipo": "invalido"}).status_code == 400


def test_salas_publicas_endpoints(client, monkeypatch):
    assert client.get("/api/rooms/xxx").status_code == 400

    async def falso_live(letra="A", limite=12):
        return {"letra": letra, "total": 50, "salas": [{"rid": "12345678-1", "nome": "Sala X", "online": 5}]}

    async def falso_sala(rid):
        return {"rid": rid, "nome": "Sala X", "online": 5, "capacidade": 20}

    monkeypatch.setattr("server.app.cliente.salas_ao_vivo", falso_live)
    monkeypatch.setattr("server.app.cliente.sala_publica", falso_sala)

    vivo = client.get("/api/rooms/live?letra=A&limite=5")
    assert vivo.status_code == 200
    assert vivo.json()["salas"][0]["online"] == 5

    detalhe = client.get("/api/rooms/12345678-1")
    assert detalhe.status_code == 200
    assert detalhe.json()["capacidade"] == 20

    assert client.get("/api/rooms/top?horas=24&limite=5").status_code == 200


