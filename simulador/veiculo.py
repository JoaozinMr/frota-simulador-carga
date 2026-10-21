"""Modelo de um caminhão simulado: perfil, movimento e geração das leituras de telemetria."""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Config

RAIO_TERRA_KM = 6371.0

PERFIL_NORMAL = "normal"
PERFIL_IMPRUDENTE = "imprudente"      # ultrapassa o limite de 80 km/h do Worker
PERFIL_ROTA_LONGA = "rota_longa"      # segue em linha reta e sai do geofence
PERFIL_ESTACIONADO = "estacionado"    # motor desligado, velocidade 0


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine, a mesma fórmula do RuleEvaluator do Worker."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * RAIO_TERRA_KM * math.asin(math.sqrt(a))


def rumo_graus(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def timestamp_iso(instante: datetime) -> str:
    """ISO 8601 com Z explícito (a API rejeita timestamps sem fuso)."""
    return instante.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Veiculo:
    id_veiculo: str
    perfil: str
    latitude: float
    longitude: float
    rumo: float
    velocidade_cruzeiro: float
    # Instante (segundos desde o início) em que o sensor "morre"; None = nunca.
    morre_em: float | None = None
    sequencia: int = 0
    velocidade: float = field(default=0.0)

    @property
    def motor_ligado(self) -> bool:
        return self.perfil != PERFIL_ESTACIONADO

    def avancar(self, dt_segundos: float, config: Config, rng: random.Random) -> None:
        """Move o caminhão por dt segundos: rumo e velocidade variam como um passeio aleatório."""
        if not self.motor_ligado:
            self.velocidade = 0.0
            return

        alvo = self.velocidade_cruzeiro
        self.velocidade = max(0.0, self.velocidade + (alvo - self.velocidade) * 0.3 + rng.gauss(0, 3))

        if self.perfil == PERFIL_ROTA_LONGA:
            self.rumo = (self.rumo + rng.gauss(0, 1)) % 360
        else:
            self.rumo = (self.rumo + rng.gauss(0, 8)) % 360
            # Mantém a frota na região de operação: perto da borda, volta para o centro.
            if distancia_km(self.latitude, self.longitude,
                            config.centro_latitude, config.centro_longitude) > config.raio_operacao_km:
                self.rumo = rumo_graus(self.latitude, self.longitude,
                                       config.centro_latitude, config.centro_longitude)

        passo_km = self.velocidade * dt_segundos / 3600
        rumo = math.radians(self.rumo)
        self.latitude += math.degrees(passo_km * math.cos(rumo) / RAIO_TERRA_KM)
        self.longitude += math.degrees(
            passo_km * math.sin(rumo) / (RAIO_TERRA_KM * math.cos(math.radians(self.latitude))))
        self.latitude = max(-90.0, min(90.0, self.latitude))
        self.longitude = ((self.longitude + 180) % 360) - 180

    def leitura(self, instante: datetime) -> dict:
        """Nova leitura: UUID novo e sequência crescente por veículo (contrato da API de Ingestão)."""
        self.sequencia += 1
        return {
            "id_evento": str(uuid.uuid4()),
            "id_veiculo": self.id_veiculo,
            "sequencia": self.sequencia,
            "timestamp": timestamp_iso(instante),
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "velocidade": round(self.velocidade, 1),
            "motor_ligado": self.motor_ligado,
        }


def criar_frota(config: Config, rng: random.Random) -> list[Veiculo]:
    """Instancia num_veiculos perfis; os percentuais da configuração definem a composição da frota."""
    frota = []
    for i in range(config.num_veiculos):
        sorteio = rng.random()
        if sorteio < config.taxa_imprudentes:
            perfil, cruzeiro = PERFIL_IMPRUDENTE, rng.uniform(85, 110)
        elif sorteio < config.taxa_imprudentes + config.taxa_rota_longa:
            perfil, cruzeiro = PERFIL_ROTA_LONGA, rng.uniform(60, 78)
        elif sorteio < config.taxa_imprudentes + config.taxa_rota_longa + config.taxa_estacionados:
            perfil, cruzeiro = PERFIL_ESTACIONADO, 0.0
        else:
            perfil, cruzeiro = PERFIL_NORMAL, rng.uniform(35, 75)

        angulo = rng.uniform(0, 2 * math.pi)
        if perfil == PERFIL_ROTA_LONGA:
            # Parte perto da borda, rumo para fora: sai do geofence em poucos minutos.
            distancia = config.raio_operacao_km * rng.uniform(0.85, 0.98)
            rumo = math.degrees(angulo) % 360
        else:
            # Posição inicial uniforme dentro de 80% do raio de operação.
            distancia = config.raio_operacao_km * 0.8 * math.sqrt(rng.random())
            rumo = rng.uniform(0, 360)
        lat = config.centro_latitude + math.degrees(distancia * math.cos(angulo) / RAIO_TERRA_KM)
        lon = config.centro_longitude + math.degrees(
            distancia * math.sin(angulo) / (RAIO_TERRA_KM * math.cos(math.radians(config.centro_latitude))))

        # Sensor morto só faz sentido com motor ligado: o Worker ignora veículos DESLIGADO.
        morre_em = None
        if perfil != PERFIL_ESTACIONADO and rng.random() < config.taxa_sensor_morto:
            morre_em = rng.uniform(config.sensor_morto_apos_min_segundos, config.sensor_morto_apos_max_segundos)

        frota.append(Veiculo(
            id_veiculo=f"CAM-{i + 1:04d}", perfil=perfil, latitude=lat, longitude=lon,
            rumo=rumo, velocidade_cruzeiro=cruzeiro, morre_em=morre_em,
            velocidade=cruzeiro))
    return frota
