"""Pacotes corrompidos que simulam falhas de hardware; todos devem ir para a DLQ (HTTP 400)."""

from __future__ import annotations

import json
import random

TIPOS_CORRUPCAO = (
    "latitude_invalida",
    "longitude_invalida",
    "velocidade_negativa",
    "campo_ausente",
    "tipo_incorreto",
    "timestamp_sem_fuso",
    "json_truncado",
    "bytes_lixo",
)


def corromper(leitura: dict, rng: random.Random) -> tuple[str, bytes]:
    """Devolve (tipo de corrupção, corpo HTTP) a partir de uma leitura válida."""
    tipo = rng.choice(TIPOS_CORRUPCAO)
    pacote = dict(leitura)

    if tipo == "latitude_invalida":
        pacote["latitude"] = rng.choice([999.0, -91.5, 123.456])
    elif tipo == "longitude_invalida":
        pacote["longitude"] = rng.choice([-999.0, 181.0])
    elif tipo == "velocidade_negativa":
        pacote["velocidade"] = -abs(pacote["velocidade"]) - 1
    elif tipo == "campo_ausente":
        del pacote[rng.choice(["id_evento", "id_veiculo", "timestamp", "latitude", "motor_ligado"])]
    elif tipo == "tipo_incorreto":
        pacote["latitude"] = "NaN-sensor"
    elif tipo == "timestamp_sem_fuso":
        pacote["timestamp"] = pacote["timestamp"].removesuffix("Z")
    elif tipo == "json_truncado":
        texto = json.dumps(pacote)
        return tipo, texto[: rng.randint(1, len(texto) - 1)].encode()
    elif tipo == "bytes_lixo":
        return tipo, bytes(rng.getrandbits(8) for _ in range(rng.randint(8, 64)))

    return tipo, json.dumps(pacote).encode()
