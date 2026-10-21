import asyncio

from . import config, logs
from .app import Simulador


def main() -> None:
    logs.configurar()
    try:
        asyncio.run(Simulador(config.carregar()).executar())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
