"""Configuração do simulador: variáveis de ambiente com sobrescrita por argumentos de linha de comando."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Config:
    # Destino: a URL do balanceador (Nginx), nunca uma réplica específica da API.
    target_url: str = "http://localhost/api/telemetry"
    num_veiculos: int = 1000
    intervalo_segundos: float = 1.0
    duracao_segundos: float = 0  # 0 = executa até receber SIGTERM/SIGINT
    max_conexoes: int = 200
    timeout_segundos: float = 10.0
    # Retry de 503/timeouts preservando id_evento e timestamp (0 = sem limite, nunca descarta).
    max_tentativas: int = 0
    backoff_max_segundos: float = 10.0
    # Centro do geofence do Worker (São Paulo) e raio em que os caminhões normalmente circulam.
    centro_latitude: float = -23.5505
    centro_longitude: float = -46.6333
    raio_operacao_km: float = 45.0
    # Perfis de frota (frações de 0 a 1).
    taxa_imprudentes: float = 0.05
    taxa_rota_longa: float = 0.03
    taxa_estacionados: float = 0.03
    # Injeção de caos (frações de 0 a 1).
    taxa_corrompidos: float = 0.01
    taxa_sensor_morto: float = 0.02
    sensor_morto_apos_min_segundos: float = 30
    sensor_morto_apos_max_segundos: float = 120
    taxa_atrasados: float = 0.02
    atraso_max_segundos: float = 30
    intervalo_metricas_segundos: float = 10
    seed: int | None = None


def _converter(tipo: str, valor: str):
    if tipo in ("int", "int | None"):
        return int(valor)
    if tipo == "float":
        return float(valor)
    return valor


def carregar(argv: list[str] | None = None) -> Config:
    """Lê SIM_<CAMPO> do ambiente (ex.: SIM_NUM_VEICULOS) e depois os argumentos --campo."""
    parser = argparse.ArgumentParser(
        prog="simulador", description="Simulador de carga de telemetria da frota.")
    valores = {}
    for campo in fields(Config):
        env = f"SIM_{campo.name.upper()}"
        if env in os.environ and os.environ[env] != "":
            valores[campo.name] = _converter(str(campo.type), os.environ[env])
        parser.add_argument(
            f"--{campo.name.replace('_', '-')}", dest=campo.name,
            type=lambda v, t=str(campo.type): _converter(t, v),
            help=f"(env {env}, padrão {campo.default})")

    args = parser.parse_args(argv)
    valores.update({k: v for k, v in vars(args).items() if v is not None})
    config = Config(**valores)
    validar(config)
    return config


def validar(config: Config) -> None:
    if config.num_veiculos < 1:
        raise SystemExit("num_veiculos deve ser >= 1")
    if config.intervalo_segundos <= 0:
        raise SystemExit("intervalo_segundos deve ser > 0")
    for nome in ("taxa_imprudentes", "taxa_rota_longa", "taxa_estacionados",
                 "taxa_corrompidos", "taxa_sensor_morto", "taxa_atrasados"):
        if not 0 <= getattr(config, nome) <= 1:
            raise SystemExit(f"{nome} deve estar entre 0 e 1")
    if config.sensor_morto_apos_min_segundos > config.sensor_morto_apos_max_segundos:
        raise SystemExit("sensor_morto_apos_min_segundos deve ser <= sensor_morto_apos_max_segundos")
