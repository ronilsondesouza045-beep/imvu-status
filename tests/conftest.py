import os
import tempfile
from pathlib import Path

BASE_TESTE = Path(tempfile.gettempdir()) / "imvu_status_testes.db"
for sufixo in ("", "-wal", "-shm"):
    arquivo = Path(str(BASE_TESTE) + sufixo)
    if arquivo.exists():
        arquivo.unlink()

os.environ["DATABASE_URL"] = "sqlite:///" + BASE_TESTE.as_posix()
os.environ["COLETOR_ATIVO"] = "0"
os.environ["TZ"] = "America/Sao_Paulo"
os.environ.pop("ADMIN_TOKEN", None)
os.environ.pop("IA_API_KEY", None)

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.db import SessionLocal


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as cliente:
        yield cliente


@pytest.fixture()
def db():
    with SessionLocal() as sessao:
        yield sessao
