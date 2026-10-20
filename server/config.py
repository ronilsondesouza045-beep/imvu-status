import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS_DIR = Path(os.getenv("DADOS_DIR") or (RAIZ / "dados"))
DADOS_DIR.mkdir(parents=True, exist_ok=True)

APP_NOME = "IMVU Status API"
APP_VERSAO = "3.0.0"

DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite:///" + (DADOS_DIR / "imvu.db").as_posix()
TZ = os.getenv("TZ", "America/Sao_Paulo")

COLETOR_ATIVO = os.getenv("COLETOR_ATIVO", "1").strip().lower() not in ("0", "false", "nao", "no")
POLL_WATCH = int(os.getenv("POLL_WATCH", "15"))
POLL_AVATAR = int(os.getenv("POLL_AVATAR", "120"))
POLL_SALA = int(os.getenv("POLL_SALA", "45"))
SALAS_POR_CICLO = int(os.getenv("SALAS_POR_CICLO", "6"))
DIRETORIO_MINUTOS = int(os.getenv("DIRETORIO_MINUTOS", "10"))
DETALHES_MINUTOS = int(os.getenv("DETALHES_MINUTOS", "10"))
MAX_AVATARES_RECENTES = int(os.getenv("MAX_AVATARES_RECENTES", "40"))
MAX_WATCH_POR_DEVICE = int(os.getenv("MAX_WATCH_POR_DEVICE", "10"))
MAX_SALAS_CONHECIDAS = int(os.getenv("MAX_SALAS_CONHECIDAS", "800"))

RETENCAO_SNAPSHOTS_DIAS = int(os.getenv("RETENCAO_SNAPSHOTS_DIAS", "14"))
RETENCAO_EVENTOS_DIAS = int(os.getenv("RETENCAO_EVENTOS_DIAS", "180"))
RETENCAO_ALERTAS_DIAS = int(os.getenv("RETENCAO_ALERTAS_DIAS", "60"))

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

IA_BASE_URL = os.getenv("IA_BASE_URL", "https://api.openai.com/v1").rstrip("/")
IA_API_KEY = os.getenv("IA_API_KEY", "").strip()
IA_MODEL = os.getenv("IA_MODEL", "gpt-4o-mini")
IA_TIMEOUT = int(os.getenv("IA_TIMEOUT", "25"))

WEBHOOK_TIMEOUT = int(os.getenv("WEBHOOK_TIMEOUT", "10"))
WEBHOOKS_ATIVOS = os.getenv("WEBHOOKS_ATIVOS", "1").strip().lower() not in ("0", "false", "nao", "no")

RATE_STATUS_MIN = int(os.getenv("RATE_STATUS_MIN", "40"))
RATE_API_MIN = int(os.getenv("RATE_API_MIN", "120"))


def orcamento_descricao():
    return {
        "coletor_ativo": COLETOR_ATIVO,
        "poll_watch_segundos": POLL_WATCH,
        "poll_avatar_segundos": POLL_AVATAR,
        "poll_sala_segundos": POLL_SALA,
        "salas_por_ciclo": SALAS_POR_CICLO,
        "retencao_snapshots_dias": RETENCAO_SNAPSHOTS_DIAS,
        "retencao_eventos_dias": RETENCAO_EVENTOS_DIAS,
    }
