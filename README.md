# Frota – Simulador de Carga

Simulador (Python 3.12, `asyncio` + `aiohttp`) que se comporta como a frota de caminhões conectados, enviando telemetria em alta frequência para a [API de Ingestão](https://github.com/JoaozinMr/frota-api-ingestao) através do balanceador de carga. Parte do sistema distribuído de monitoramento de frota (orquestrado pelo repositório [frota-infraestrutura](https://github.com/JoaozinMr/frota-infraestrutura)).

## Funcionalidades

| Requisito do edital | Implementação |
|---|---|
| Carga de 1.000 sensores | [criar_frota](simulador/veiculo.py): `SIM_NUM_VEICULOS=1000` perfis, uma corrotina por caminhão, início escalonado dentro do intervalo |
| Geração constante | [Simulador._ciclo](simulador/app.py): a cada `SIM_INTERVALO_SEGUNDOS` o caminhão avança (passeio aleatório de rumo/velocidade) e envia uma leitura. Agenda pelo relógio, sem acumular rajadas |
| Requisições assíncronas | [Enviador](simulador/envio.py): `aiohttp` com pool de `SIM_MAX_CONEXOES` conexões keep-alive para `POST /api/telemetry` |
| Pacotes corrompidos (DLQ) | [caos.py](simulador/caos.py): `SIM_TAXA_CORROMPIDOS` (1%) — latitude/longitude fora da faixa, velocidade negativa, campo ausente, tipo incorreto, timestamp sem fuso, JSON truncado e bytes aleatórios. Todos devem receber `400` e ir para `frota.telemetria.dlq` |
| Sensor morto | `SIM_TAXA_SENSOR_MORTO` (2%) dos caminhões com motor ligado param de transmitir entre 30 e 120s após o início; o Worker emite `SENSOR_MORTO` após 60s de silêncio |
| Dados fora de ordem | `SIM_TAXA_ATRASADOS` (2%) das leituras ficam retidas até `SIM_ATRASO_MAX_SEGUNDOS` e chegam depois das seguintes, com timestamp e `sequencia` originais |
| Regras de negócio | Perfis da frota: `imprudente` (5%, 85–110 km/h → `EXCESSO_VELOCIDADE`), `rota_longa` (3%, sai do raio de 50 km → `FORA_GEOFENCE`), `estacionado` (3%, motor desligado) e `normal` (35–75 km/h, mantido dentro do geofence de São Paulo) |
| Não perder leituras | `503`, `5xx`, `429`, timeout e erro de conexão → retry com backoff exponencial + jitter (respeita `Retry-After`), reenviando o **mesmo corpo** (mesmo `id_evento`, `timestamp` e `X-Correlation-ID`). `400` nunca é reenviado |
| Observabilidade | Logs JSON no formato compacto do Serilog (`@t`, `@m`, `@l`, `Service=frota-simulador-carga`) com um resumo a cada 10s: req/s, latência p50/p95/p99, status HTTP, retries, corrompidos, atrasados e sensores mortos |
| Containerização | [Dockerfile](Dockerfile) `python:3.12-slim`, usuário sem privilégios, entrypoint `python -m simulador` |

## Contrato enviado

Mesmo contrato da API de Ingestão (`POST /api/telemetry`, header `X-Correlation-ID` com UUID):

```json
{
  "id_evento": "c12b3efe-f5ef-4857-bad4-a2a791494e01",
  "id_veiculo": "CAM-0001",
  "sequencia": 123,
  "timestamp": "2026-10-19T13:30:00.123Z",
  "latitude": -23.551,
  "longitude": -46.634,
  "velocidade": 72.5,
  "motor_ligado": true
}
```

Cada leitura nova tem `id_evento` novo e `sequencia` crescente por veículo. Leituras corrompidas consomem a sequência (lacuna, como um pacote perdido do rastreador).

## Configuração

Variáveis de ambiente `SIM_<CAMPO>` ou argumentos `--<campo>` (o argumento vence). Lista completa: `python -m simulador --help`.

| Variável | Padrão | Descrição |
|---|---|---|
| `SIM_TARGET_URL` | `http://localhost/api/telemetry` | URL do balanceador (no Compose: `http://nginx/api/telemetry`) |
| `SIM_NUM_VEICULOS` | `1000` | Caminhões simulados |
| `SIM_INTERVALO_SEGUNDOS` | `1.0` | Período de envio por caminhão (1.000 caminhões a 1s ≈ 1.000 req/s) |
| `SIM_DURACAO_SEGUNDOS` | `0` | Encerra após N segundos (`0` = até SIGTERM/Ctrl+C) |
| `SIM_MAX_CONEXOES` | `200` | Conexões HTTP simultâneas |
| `SIM_TIMEOUT_SEGUNDOS` | `10` | Timeout total por tentativa (a API leva até ~10s para responder 503 com o broker fora) |
| `SIM_MAX_TENTATIVAS` | `0` | Tentativas por pacote (`0` = sem limite: o caminhão segura a leitura até a API confirmar) |
| `SIM_BACKOFF_MAX_SEGUNDOS` | `10` | Teto do backoff exponencial |
| `SIM_TAXA_CORROMPIDOS` | `0.01` | Fração de pacotes corrompidos |
| `SIM_TAXA_SENSOR_MORTO` | `0.02` | Fração de caminhões que param de transmitir |
| `SIM_SENSOR_MORTO_APOS_MIN_SEGUNDOS` / `_MAX_` | `30` / `120` | Janela em que o sensor morre |
| `SIM_TAXA_ATRASADOS` | `0.02` | Fração de leituras entregues fora de ordem |
| `SIM_ATRASO_MAX_SEGUNDOS` | `30` | Atraso máximo das leituras retidas |
| `SIM_TAXA_IMPRUDENTES` / `_ROTA_LONGA` / `_ESTACIONADOS` | `0.05` / `0.03` / `0.03` | Composição da frota |
| `SIM_CENTRO_LATITUDE` / `_LONGITUDE` / `SIM_RAIO_OPERACAO_KM` | São Paulo / `45` | Região de operação (alinhada ao geofence padrão do Worker: centro SP, 50 km) |
| `SIM_SEED` | — | Semente do gerador aleatório (execuções reprodutíveis) |

Com o retry sem limite, uma indisponibilidade da API não gera rajada: cada caminhão segura a leitura atual e só gera a próxima depois da confirmação (o relógio do ciclo é reiniciado).

## Executar

```bash
# Ambiente completo (balanceador, APIs, Worker, bancos): use o repositório frota-infraestrutura
make up                      # sobe tudo, incluindo este simulador

# Localmente, apontando para a API de Ingestão isolada (porta 8081 do compose dela)
pip install -r requirements.txt
python -m simulador --target-url http://localhost:8081/api/telemetry --num-veiculos 100

# Container
docker build -t frota-simulador-carga .
docker run --rm --network host -e SIM_TARGET_URL=http://localhost/api/telemetry frota-simulador-carga
```

## Testes

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Cobrem o contrato das leituras (réplica das regras do `TelemetriaValidator`), a rejeição de todos os tipos de pacote corrompido, a movimentação (perfil `rota_longa` sai do geofence, `normal` permanece), a configuração e o retry contra um servidor HTTP local (`503 → 503 → 202` reenviando o mesmo corpo e correlation ID; `400` sem retry).

## O que observar na demonstração

- Logs do simulador: `docker compose logs -f simulador` (resumo a cada 10s; `status.400` ≈ 1% = DLQ).
- DLQ: `docker compose exec redpanda rpk topic consume frota.telemetria.dlq -n 5`.
- Alertas: `curl http://localhost/api/alerts/resumo` — `EXCESSO_VELOCIDADE`, `FORA_GEOFENCE` e, após ~2 minutos, `SENSOR_MORTO`.
- Painel Grafana (`http://localhost:3000`): vazão orgânica de ~1.000 eventos/s e posição dos caminhões no mapa.
