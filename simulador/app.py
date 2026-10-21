"""Orquestração: uma corrotina por caminhão, injeção de caos e relatório periódico de métricas."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import signal
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone

import aiohttp

from .caos import corromper
from .config import Config
from .envio import Enviador
from .metricas import Metricas
from .veiculo import Veiculo, criar_frota

logger = logging.getLogger("simulador")


class Simulador:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.rng = random.Random(config.seed)
        self.metricas = Metricas()
        self.frota = criar_frota(config, self.rng)
        self._parar = asyncio.Event()
        self._atrasados: set[asyncio.Task] = set()
        self._inicio = 0.0

    async def executar(self) -> None:
        loop = asyncio.get_running_loop()
        for sinal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sinal, self._parar.set)
            except NotImplementedError:  # Windows: Ctrl+C cai em KeyboardInterrupt
                pass

        perfis = Counter(v.perfil for v in self.frota)
        logger.info(
            f"Iniciando {len(self.frota)} caminhões contra {self.config.target_url} "
            f"a cada {self.config.intervalo_segundos}s",
            extra={"Perfis": dict(perfis),
                   "SensoresQueVaoMorrer": sum(v.morre_em is not None for v in self.frota),
                   "Config": asdict(self.config)})

        # ttl_dns_cache curto: acompanha recriação do balanceador sem reiniciar o simulador.
        connector = aiohttp.TCPConnector(limit=self.config.max_conexoes, ttl_dns_cache=10)
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_segundos)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            enviador = Enviador(session, self.config, self.metricas, self.rng)
            self._inicio = loop.time()
            tarefas = [asyncio.create_task(self._ciclo(v, enviador)) for v in self.frota]
            relatorio = asyncio.create_task(self._relatar())

            duracao = self.config.duracao_segundos or None
            try:
                await asyncio.wait_for(self._parar.wait(), timeout=duracao)
            except asyncio.TimeoutError:
                logger.info(f"Duração de {self.config.duracao_segundos}s atingida")

            logger.info("Encerrando simulador")
            for tarefa in [*tarefas, *self._atrasados, relatorio]:
                tarefa.cancel()
            await asyncio.gather(*tarefas, *self._atrasados, relatorio, return_exceptions=True)

        resumo = self.metricas.fechar_janela()
        logger.info("Resumo final", extra={"Total": resumo["Total"]})

    async def _ciclo(self, veiculo: Veiculo, enviador: Enviador) -> None:
        loop = asyncio.get_running_loop()
        intervalo = self.config.intervalo_segundos
        # Escalona o início para não disparar os 1.000 caminhões no mesmo instante.
        await asyncio.sleep(self.rng.uniform(0, intervalo))
        proximo = ultimo = loop.time()

        while True:
            agora = loop.time()
            if veiculo.morre_em is not None and agora - self._inicio >= veiculo.morre_em:
                # Sensor morto: o loop do caminhão para com o motor ligado; o Worker
                # deve emitir SENSOR_MORTO após o limite de silêncio (padrão 60s).
                self.metricas.incrementar("sensores_mortos")
                logger.warning(f"Sensor morto simulado: {veiculo.id_veiculo} parou de transmitir",
                               extra={"IdVeiculo": veiculo.id_veiculo, "Sequencia": veiculo.sequencia})
                return

            veiculo.avancar(agora - ultimo, self.config, self.rng)
            ultimo = agora
            await self._emitir(veiculo.leitura(datetime.now(timezone.utc)), enviador)

            # Agenda pelo relógio (não por sleep fixo); se ficou muito para trás
            # (ex.: API fora e retries longos), retoma sem disparar uma rajada de atraso.
            proximo += intervalo
            espera = proximo - loop.time()
            if espera < -intervalo:
                proximo, espera = loop.time(), 0
            await asyncio.sleep(max(0.0, espera))

    async def _emitir(self, leitura: dict, enviador: Enviador) -> None:
        sorteio = self.rng.random()
        correlation_id = str(uuid.uuid4())

        if sorteio < self.config.taxa_corrompidos:
            tipo, corpo = corromper(leitura, self.rng)
            self.metricas.incrementar("corrompidos")
            self.metricas.incrementar(f"corrompidos.{tipo}")
            await enviador.enviar(corpo, correlation_id, valido=False)
            return

        corpo = json.dumps(leitura).encode()
        if sorteio < self.config.taxa_corrompidos + self.config.taxa_atrasados:
            # Fora de ordem: a leitura fica retida e chega depois das seguintes, com o
            # timestamp e a sequência originais (como um rastreador que perdeu sinal).
            atraso = self.rng.uniform(min(2 * self.config.intervalo_segundos, self.config.atraso_max_segundos),
                                      self.config.atraso_max_segundos)
            self.metricas.incrementar("atrasados")
            tarefa = asyncio.create_task(self._enviar_depois(atraso, corpo, correlation_id, enviador))
            self._atrasados.add(tarefa)
            tarefa.add_done_callback(self._atrasados.discard)
            return

        self.metricas.incrementar("validos")
        await enviador.enviar(corpo, correlation_id, valido=True)

    async def _enviar_depois(self, atraso: float, corpo: bytes, correlation_id: str, enviador: Enviador) -> None:
        await asyncio.sleep(atraso)
        await enviador.enviar(corpo, correlation_id, valido=True)

    async def _relatar(self) -> None:
        while True:
            await asyncio.sleep(self.config.intervalo_metricas_segundos)
            resumo = self.metricas.fechar_janela()
            ativos = len(self.frota) - self.metricas.total["sensores_mortos"]
            logger.info(
                f"{resumo['RequisicoesPorSegundo']} req/s, p95 {resumo['LatenciaP95Ms']} ms, "
                f"{ativos} caminhões transmitindo",
                extra={**resumo, "CaminhoesAtivos": ativos, "AtrasadosPendentes": len(self._atrasados)})
