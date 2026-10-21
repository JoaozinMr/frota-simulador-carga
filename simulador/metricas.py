"""Contadores agregados: o simulador loga um resumo por janela em vez de uma linha por requisição."""

from __future__ import annotations

import time
from collections import Counter


def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, max(0, round(p / 100 * len(ordenados)) - 1))
    return ordenados[indice]


class Metricas:
    def __init__(self) -> None:
        self.total: Counter[str] = Counter()
        self._janela: Counter[str] = Counter()
        self._latencias_ms: list[float] = []
        self._inicio_janela = time.monotonic()

    def incrementar(self, nome: str, quantidade: int = 1) -> None:
        self.total[nome] += quantidade
        self._janela[nome] += quantidade

    def registrar_latencia(self, ms: float) -> None:
        self._latencias_ms.append(ms)

    def fechar_janela(self) -> dict:
        """Resumo da janela atual (taxa, status e latências) e reinício dos contadores da janela."""
        agora = time.monotonic()
        duracao = max(agora - self._inicio_janela, 1e-9)
        latencias, janela = self._latencias_ms, self._janela
        self._latencias_ms, self._janela, self._inicio_janela = [], Counter(), agora
        return {
            "JanelaSegundos": round(duracao, 1),
            "RequisicoesPorSegundo": round(janela["requisicoes"] / duracao, 1),
            "LatenciaP50Ms": round(percentil(latencias, 50), 1),
            "LatenciaP95Ms": round(percentil(latencias, 95), 1),
            "LatenciaP99Ms": round(percentil(latencias, 99), 1),
            "Janela": dict(janela),
            "Total": dict(self.total),
        }
