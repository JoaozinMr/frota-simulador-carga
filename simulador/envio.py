"""Envio HTTP para a API de Ingestão com retry/backoff preservando o pacote original."""

from __future__ import annotations

import asyncio
import logging
import random
import time

import aiohttp

from .config import Config
from .metricas import Metricas

# 5xx/429 e falhas de transporte têm resultado ambíguo (a API pode ter publicado e a
# confirmação se perdido): reenviamos o MESMO corpo, com o mesmo id_evento e timestamp,
# para que a deduplicação do Worker/PostgreSQL absorva a eventual duplicata.
STATUS_TRANSITORIOS = {429, 500, 502, 503, 504}

logger = logging.getLogger("simulador.envio")


def atraso_backoff(tentativa: int, config: Config, rng: random.Random, retry_after: str | None = None) -> float:
    """Exponencial com jitter (0.5s, 1s, 2s, ... até backoff_max); respeita Retry-After da API."""
    if retry_after:
        try:
            return min(float(retry_after), config.backoff_max_segundos)
        except ValueError:
            pass
    base = min(config.backoff_max_segundos, 0.5 * 2 ** (tentativa - 1))
    return base * rng.uniform(0.5, 1.0)


class Enviador:
    def __init__(self, session: aiohttp.ClientSession, config: Config,
                 metricas: Metricas, rng: random.Random) -> None:
        self._session = session
        self._config = config
        self._metricas = metricas
        self._rng = rng

    async def enviar(self, corpo: bytes, correlation_id: str, valido: bool) -> int | None:
        """Envia até obter uma resposta definitiva. Retorna o status HTTP ou None se descartado."""
        tentativa = 0
        while True:
            tentativa += 1
            retry_after = None
            inicio = time.perf_counter()
            try:
                async with self._session.post(
                    self._config.target_url, data=corpo,
                    headers={"Content-Type": "application/json", "X-Correlation-ID": correlation_id},
                ) as resposta:
                    await resposta.read()
                    status = resposta.status
                    retry_after = resposta.headers.get("Retry-After")
            except (aiohttp.ClientError, asyncio.TimeoutError) as ex:
                status = None
                self._metricas.incrementar(f"erro_transporte.{type(ex).__name__}")
            finally:
                self._metricas.incrementar("requisicoes")
                self._metricas.registrar_latencia((time.perf_counter() - inicio) * 1000)

            if status is not None and status not in STATUS_TRANSITORIOS:
                self._contabilizar(status, valido)
                return status

            if status is not None:
                self._metricas.incrementar(f"status.{status}")
            limite = self._config.max_tentativas
            if limite and tentativa >= limite:
                self._metricas.incrementar("descartados")
                logger.warning(f"Pacote descartado após {tentativa} tentativas",
                               extra={"CorrelationId": correlation_id, "Tentativas": tentativa})
                return None

            self._metricas.incrementar("retries")
            await asyncio.sleep(atraso_backoff(tentativa, self._config, self._rng, retry_after))

    def _contabilizar(self, status: int, valido: bool) -> None:
        self._metricas.incrementar(f"status.{status}")
        if valido:
            # 202 = confirmado pelo broker; qualquer outro status definitivo é inesperado.
            self._metricas.incrementar("aceitos" if status == 202 else "validos_rejeitados")
        else:
            # Pacote corrompido: o esperado é 400 (encaminhado à DLQ).
            self._metricas.incrementar("corrompidos_na_dlq" if status == 400 else "corrompidos_aceitos")
