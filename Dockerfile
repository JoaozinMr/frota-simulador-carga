FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
# Dependências em camada separada para aproveitar cache.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY simulador/ simulador/

RUN useradd --create-home --uid 10001 app
USER app

# Configuração por variáveis SIM_* (ver README); argumentos extras são repassados ao script.
ENTRYPOINT ["python", "-m", "simulador"]
