import asyncio
import datetime
import logging
import string
import time

from sqlalchemy import func, select

from .config import (
    DETALHES_MINUTOS,
    DIRETORIO_MINUTOS,
    MAX_AVATARES_RECENTES,
    MAX_SALAS_CONHECIDAS,
    POLL_AVATAR,
    POLL_SALA,
    POLL_WATCH,
    SALAS_POR_CICLO,
)
from .db import Avatar, Room, SessionLocal, Watch, init_db, utcnow
from .imvu import IMVUErro, cliente
from .services import aplicar_retencao, descobrir_salas, processar_status, registrar_sala

log = logging.getLogger("imvu.coletor")

LETRAS = list(string.ascii_uppercase) + list(string.digits)


class Coletor:
    def __init__(self):
        self.estado = {
            "iniciado_em": None,
            "ciclos": 0,
            "ultimo_ciclo": None,
            "ultimo_erro": None,
            "pausado_ate": None,
            "consultas": 0,
            "eventos": 0,
            "salas": 0,
            "diretorio": 0,
        }
        self._parar = asyncio.Event()
        self._tarefa: asyncio.Task | None = None
        self._ultimo: dict[str, float] = {}
        self._ultimo_full: dict[str, float] = {}
        self._ultimo_salas = 0.0
        self._ultimo_diretorio = 0.0
        self._ultima_retencao = 0.0
        self._letra = 0
        self._pausa_ate = 0.0

    def iniciar(self):
        if self._tarefa is None or self._tarefa.done():
            self._parar.clear()
            self._tarefa = asyncio.create_task(self._loop())

    async def parar(self):
        self._parar.set()
        if self._tarefa:
            try:
                await asyncio.wait_for(self._tarefa, timeout=10)
            except Exception:
                self._tarefa.cancel()

    async def _loop(self):
        self.estado["iniciado_em"] = utcnow().isoformat()
        await asyncio.sleep(2)
        while not self._parar.is_set():
            inicio = time.monotonic()
            try:
                await self._ciclo()
                self.estado["ultimo_erro"] = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.estado["ultimo_erro"] = str(exc)[:300]
                log.exception("erro no ciclo do coletor")
            self.estado["ciclos"] += 1
            self.estado["ultimo_ciclo"] = utcnow().isoformat()
            decorrido = time.monotonic() - inicio
            try:
                await asyncio.wait_for(self._parar.wait(), timeout=max(2.0, 6.0 - decorrido))
            except asyncio.TimeoutError:
                pass

    async def _ciclo(self):
        if time.monotonic() < self._pausa_ate:
            return
        agora = utcnow()
        with SessionLocal() as db:
            nicks_watch = list(
                db.scalars(
                    select(Avatar.nick)
                    .join(Watch, Watch.avatar_id == Avatar.id)
                    .where(Watch.notificar.is_(True))
                    .distinct()
                ).all()
            )
            recentes = list(
                db.scalars(
                    select(Avatar.nick)
                    .where(Avatar.last_seen >= agora - datetime.timedelta(hours=2))
                    .order_by(Avatar.last_seen.desc())
                    .limit(MAX_AVATARES_RECENTES)
                ).all()
            )
        nicks = list(dict.fromkeys(nicks_watch + recentes))
        agora_mono = time.monotonic()
        alvos = []
        for nick in nicks:
            intervalo = POLL_WATCH if nick in nicks_watch else POLL_AVATAR
            if agora_mono - self._ultimo.get(nick.lower(), 0.0) < intervalo:
                continue
            detalhes = agora_mono - self._ultimo_full.get(nick.lower(), 0.0) >= DETALHES_MINUTOS * 60
            alvos.append((nick, detalhes))

        if alvos:
            resultados = await asyncio.gather(
                *[self._consultar(nick, detalhes) for nick, detalhes in alvos],
                return_exceptions=True,
            )
            for resultado in resultados:
                if isinstance(resultado, IMVUErro) and resultado.status == 429:
                    self._pausa_ate = time.monotonic() + 180
                    self.estado["pausado_ate"] = utcnow().isoformat()
                    break

        if time.monotonic() - self._ultimo_salas >= POLL_SALA:
            self._ultimo_salas = time.monotonic()
            await self._snapshot_salas()

        if time.monotonic() - self._ultimo_diretorio >= DIRETORIO_MINUTOS * 60:
            self._ultimo_diretorio = time.monotonic()
            await self._descobrir()

        if time.monotonic() - self._ultima_retencao >= 24 * 3600:
            self._ultima_retencao = time.monotonic()
            await asyncio.to_thread(aplicar_retencao)

        self._podar()

    async def _consultar(self, nick: str, detalhes: bool):
        self._ultimo[nick.lower()] = time.monotonic()
        self.estado["consultas"] += 1
        if detalhes:
            self._ultimo_full[nick.lower()] = time.monotonic()
        dados = await cliente.check(nick, detalhes=detalhes)
        if dados:
            resultado = await processar_status(dados)
            if resultado:
                self.estado["eventos"] += len(resultado.get("eventos") or [])
        return dados

    async def _snapshot_salas(self):
        with SessionLocal() as db:
            salas = list(
                db.scalars(
                    select(Room)
                    .order_by(func.coalesce(Room.last_snapshot_at, datetime.datetime(1970, 1, 1)).asc())
                    .limit(min(SALAS_POR_CICLO, MAX_SALAS_CONHECIDAS))
                ).all()
            )
            rids = [sala.rid for sala in salas]
        for rid in rids:
            try:
                info = await cliente.sala(rid)
                if registrar_sala(info):
                    self.estado["salas"] += 1
            except IMVUErro as exc:
                if exc.status == 429:
                    self._pausa_ate = time.monotonic() + 180
                    return
            except Exception:
                log.debug("falha ao atualizar sala %s", rid, exc_info=True)

    async def _descobrir(self):
        letra = LETRAS[self._letra % len(LETRAS)]
        self._letra += 1
        try:
            lista = await cliente.diretorio(letra)
            novas = await asyncio.to_thread(descobrir_salas, lista)
            self.estado["diretorio"] += novas
        except IMVUErro as exc:
            if exc.status == 429:
                self._pausa_ate = time.monotonic() + 180

    def _podar(self):
        limite = time.monotonic() - 3600
        for chave in [k for k, v in self._ultimo.items() if v < limite]:
            self._ultimo.pop(chave, None)
            self._ultimo_full.pop(chave, None)


coletor = Coletor()


async def _main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    log.info("coletor standalone iniciado")
    coletor.iniciar()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await coletor.parar()


if __name__ == "__main__":
    asyncio.run(_main())
