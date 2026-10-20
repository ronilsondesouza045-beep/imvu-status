import asyncio
import datetime
from dataclasses import dataclass, field


@dataclass(eq=False)
class Inscrito:
    dispositivo: str | None = None
    global_: bool = False
    fila: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=500))


class Barramento:
    def __init__(self):
        self._inscritos: set[Inscrito] = set()

    def assinar(self, dispositivo: str | None = None, global_: bool = False) -> Inscrito:
        inscrito = Inscrito(dispositivo=dispositivo, global_=global_)
        self._inscritos.add(inscrito)
        return inscrito

    def remover(self, inscrito: Inscrito):
        self._inscritos.discard(inscrito)

    async def publicar(self, evento: dict, dispositivos: set[str] | None = None, global_: bool = True):
        evento = dict(evento)
        evento.setdefault("ts", datetime.datetime.now(datetime.timezone.utc).isoformat())
        for inscrito in list(self._inscritos):
            entregar = (global_ and inscrito.global_) or bool(
                dispositivos and inscrito.dispositivo in dispositivos
            )
            if not entregar:
                continue
            try:
                inscrito.fila.put_nowait(evento)
            except asyncio.QueueFull:
                pass

    def conexoes(self) -> int:
        return len(self._inscritos)


barramento = Barramento()
