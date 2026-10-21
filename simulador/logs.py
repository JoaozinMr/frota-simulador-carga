"""Logs JSON no mesmo formato compacto (Serilog RenderedCompactJsonFormatter) dos serviços .NET."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

SERVICE_NAME = "frota-simulador-carga"

_NIVEIS = {"DEBUG": "Debug", "WARNING": "Warning", "ERROR": "Error", "CRITICAL": "Fatal"}
_ATRIBUTOS_PADRAO = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class CompactJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        evento = {
            "@t": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "@m": record.getMessage(),
        }
        if record.levelname in _NIVEIS:  # Information é omitido, como no Serilog
            evento["@l"] = _NIVEIS[record.levelname]
        if record.exc_info:
            evento["@x"] = self.formatException(record.exc_info)
        evento["Service"] = SERVICE_NAME
        evento.update({k: v for k, v in record.__dict__.items() if k not in _ATRIBUTOS_PADRAO})
        return json.dumps(evento, ensure_ascii=False, default=str)


def configurar() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CompactJsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return logging.getLogger("simulador")
