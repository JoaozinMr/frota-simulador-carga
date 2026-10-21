"""Testes do simulador: python -m unittest discover -s tests"""

import asyncio
import json
import random
import re
import unittest
import uuid
from datetime import datetime, timezone

from aiohttp import web

from simulador.caos import TIPOS_CORRUPCAO, corromper
from simulador.config import Config, carregar
from simulador.envio import Enviador, atraso_backoff
from simulador.metricas import Metricas, percentil
from simulador.veiculo import (PERFIL_ESTACIONADO, PERFIL_ROTA_LONGA, Veiculo,
                               criar_frota, distancia_km)

CONFIG = Config(seed=42)


def valido_para_api(corpo: bytes) -> bool:
    """Réplica das regras do TelemetriaValidator da API de Ingestão."""
    try:
        p = json.loads(corpo)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(p, dict):
        return False
    campos = ["id_evento", "id_veiculo", "sequencia", "timestamp", "latitude", "longitude",
              "velocidade", "motor_ligado"]
    if any(c not in p for c in campos):
        return False
    numericos = all(isinstance(p[c], (int, float)) and not isinstance(p[c], bool)
                    for c in ["latitude", "longitude", "velocidade", "sequencia"])
    return (numericos and isinstance(p["motor_ligado"], bool)
            and -90 <= p["latitude"] <= 90 and -180 <= p["longitude"] <= 180
            and p["velocidade"] >= 0 and p["sequencia"] >= 0
            and re.fullmatch(r"[A-Za-z0-9_-]+", p["id_veiculo"]) is not None
            and re.search(r"T.*(?:Z|[+-]\d{2}:\d{2})$", p["timestamp"]) is not None
            and uuid.UUID(p["id_evento"]).int != 0)


class VeiculoTests(unittest.TestCase):
    def test_frota_tem_tamanho_ids_unicos_e_posicoes_na_regiao(self):
        frota = criar_frota(CONFIG, random.Random(1))
        self.assertEqual(len(frota), 1000)
        self.assertEqual(len({v.id_veiculo for v in frota}), 1000)
        for v in frota:
            self.assertLessEqual(
                distancia_km(v.latitude, v.longitude, CONFIG.centro_latitude, CONFIG.centro_longitude),
                CONFIG.raio_operacao_km)

    def test_rota_longa_sai_do_geofence_em_poucos_minutos(self):
        rng = random.Random(10)
        longas = [v for v in criar_frota(Config(taxa_rota_longa=0.5), rng) if v.perfil == PERFIL_ROTA_LONGA]
        for _ in range(15 * 60):
            for v in longas:
                v.avancar(1.0, CONFIG, rng)
        centro = (CONFIG.centro_latitude, CONFIG.centro_longitude)
        fora = sum(distancia_km(v.latitude, v.longitude, *centro) > 50 for v in longas)
        self.assertGreaterEqual(fora / len(longas), 0.95)

    def test_sensor_morto_nunca_em_veiculo_estacionado(self):
        frota = criar_frota(Config(taxa_sensor_morto=1, taxa_estacionados=0.5), random.Random(2))
        for v in frota:
            self.assertEqual(v.morre_em is None, v.perfil == PERFIL_ESTACIONADO)

    def test_leitura_respeita_contrato_e_sequencia_cresce(self):
        v = criar_frota(CONFIG, random.Random(3))[0]
        primeira = v.leitura(datetime.now(timezone.utc))
        segunda = v.leitura(datetime.now(timezone.utc))
        self.assertTrue(valido_para_api(json.dumps(primeira).encode()))
        self.assertEqual(segunda["sequencia"], primeira["sequencia"] + 1)
        self.assertNotEqual(segunda["id_evento"], primeira["id_evento"])
        self.assertTrue(primeira["timestamp"].endswith("Z"))

    def test_veiculo_se_move_e_permanece_valido(self):
        rng = random.Random(4)
        v = criar_frota(CONFIG, rng)[0]
        inicio = (v.latitude, v.longitude)
        for _ in range(3600):
            v.avancar(1.0, CONFIG, rng)
        self.assertNotEqual(inicio, (v.latitude, v.longitude))
        self.assertTrue(valido_para_api(json.dumps(v.leitura(datetime.now(timezone.utc))).encode()))

    def test_rota_longa_sai_do_geofence_e_normal_volta(self):
        rng = random.Random(5)
        longa = Veiculo("L", PERFIL_ROTA_LONGA, CONFIG.centro_latitude, CONFIG.centro_longitude, 0, 70, velocidade=70)
        normal = Veiculo("N", "normal", CONFIG.centro_latitude, CONFIG.centro_longitude, 0, 70, velocidade=70)
        for _ in range(3 * 3600):
            longa.avancar(1.0, CONFIG, rng)
            normal.avancar(1.0, CONFIG, rng)
        centro = (CONFIG.centro_latitude, CONFIG.centro_longitude)
        self.assertGreater(distancia_km(longa.latitude, longa.longitude, *centro), 50)
        self.assertLess(distancia_km(normal.latitude, normal.longitude, *centro), 50)


class CaosTests(unittest.TestCase):
    def test_todo_pacote_corrompido_e_rejeitado_pela_validacao(self):
        rng = random.Random(6)
        v = criar_frota(CONFIG, rng)[0]
        tipos = set()
        for _ in range(2000):
            tipo, corpo = corromper(v.leitura(datetime.now(timezone.utc)), rng)
            tipos.add(tipo)
            self.assertFalse(valido_para_api(corpo), f"{tipo} passou na validação: {corpo!r}")
        self.assertEqual(tipos, set(TIPOS_CORRUPCAO))


class ConfigTests(unittest.TestCase):
    def test_ambiente_e_argumentos(self):
        import os
        os.environ["SIM_NUM_VEICULOS"] = "10"
        try:
            config = carregar(["--intervalo-segundos", "0.5", "--target-url", "http://x/api/telemetry"])
        finally:
            del os.environ["SIM_NUM_VEICULOS"]
        self.assertEqual((config.num_veiculos, config.intervalo_segundos, config.target_url),
                         (10, 0.5, "http://x/api/telemetry"))

    def test_taxa_invalida(self):
        with self.assertRaises(SystemExit):
            carregar(["--taxa-corrompidos", "1.5"])


class MetricasTests(unittest.TestCase):
    def test_percentil(self):
        self.assertEqual(percentil(list(range(1, 101)), 95), 95)
        self.assertEqual(percentil([], 95), 0)

    def test_backoff_limitado_e_retry_after(self):
        rng = random.Random(7)
        self.assertLessEqual(atraso_backoff(20, CONFIG, rng), CONFIG.backoff_max_segundos)
        self.assertEqual(atraso_backoff(1, CONFIG, rng, "2"), 2)


class EnvioTests(unittest.IsolatedAsyncioTestCase):
    async def _servidor(self, respostas: list[int]):
        recebidos = []

        async def handler(request: web.Request):
            recebidos.append((await request.read(), request.headers["X-Correlation-ID"]))
            status = respostas.pop(0) if respostas else 202
            return web.json_response({}, status=status, headers={"Retry-After": "0"})

        app = web.Application()
        app.router.add_post("/api/telemetry", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.addAsyncCleanup(runner.cleanup)
        porta = site._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{porta}/api/telemetry", recebidos

    async def test_retry_reenvia_mesmo_corpo_e_correlation_id(self):
        import aiohttp
        url, recebidos = await self._servidor([503, 503, 202])
        metricas = Metricas()
        async with aiohttp.ClientSession() as session:
            enviador = Enviador(session, Config(target_url=url), metricas, random.Random(8))
            status = await enviador.enviar(b'{"a":1}', "corr-1", valido=True)
        self.assertEqual(status, 202)
        self.assertEqual(recebidos, [(b'{"a":1}', "corr-1")] * 3)
        self.assertEqual((metricas.total["retries"], metricas.total["aceitos"]), (2, 1))

    async def test_400_nao_tem_retry_e_limite_descarta(self):
        import aiohttp
        url, recebidos = await self._servidor([400, 503, 503])
        metricas = Metricas()
        async with aiohttp.ClientSession() as session:
            enviador = Enviador(session, Config(target_url=url, max_tentativas=2), metricas, random.Random(9))
            self.assertEqual(await enviador.enviar(b"{x", "c", valido=False), 400)
            self.assertIsNone(await enviador.enviar(b"{}", "d", valido=True))
        self.assertEqual(len(recebidos), 3)
        self.assertEqual((metricas.total["corrompidos_na_dlq"], metricas.total["descartados"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
