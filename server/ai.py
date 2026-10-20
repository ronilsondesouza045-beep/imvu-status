import json
import logging

import httpx

from .config import IA_API_KEY, IA_BASE_URL, IA_MODEL, IA_TIMEOUT

log = logging.getLogger("imvu.ia")

SISTEMA = (
    "Voce resume dados publicos de atividade do IMVU em portugues do Brasil. "
    "Seja direto, objetivo e nao invente numeros. Use no maximo 4 frases. "
    "Nao faca julgamentos sobre pessoas e nao sugira vigiar ninguem."
)


async def resumir_com_ia(fatos: dict, linhas: list[str]) -> str | None:
    if not IA_API_KEY:
        return None
    prompt = (
        "Dados agregados das ultimas horas:\n"
        + json.dumps(fatos, ensure_ascii=False)
        + "\n\nResumo factual atual:\n"
        + "\n".join(linhas)
        + "\n\nEscreva um resumo curto em portugues do Brasil."
    )
    try:
        async with httpx.AsyncClient(timeout=IA_TIMEOUT) as cliente:
            resposta = await cliente.post(
                IA_BASE_URL + "/chat/completions",
                headers={"Authorization": "Bearer " + IA_API_KEY},
                json={
                    "model": IA_MODEL,
                    "messages": [
                        {"role": "system", "content": SISTEMA},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 220,
                },
            )
            resposta.raise_for_status()
            dados = resposta.json()
            conteudo = (
                (dados.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            ).strip()
            return conteudo or None
    except Exception as exc:
        log.warning("resumo por IA indisponivel: %s", exc)
        return None
