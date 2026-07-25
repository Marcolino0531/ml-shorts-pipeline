# ml-shorts-pipeline

Pipeline em Python para automatizar vídeos curtos (TikTok/Shorts) de produtos em alta no Mercado Livre.

## Etapas do pipeline

| # | Etapa | Módulo | Status |
|---|-------|--------|--------|
| 1 | Coleta de produtos em alta (API oficial + fallback Playwright) | `mlshorts.collectors` | implementado |
| 2 | Filtro (nota ≥ 4.5, volume de vendas) + download das imagens | `mlshorts.collectors.filters` / `.images` | implementado |
| 3 | Roteiro de até 45s via OpenAI/Claude | `mlshorts.scriptgen` | implementado |
| 4 | Narração via ElevenLabs | `mlshorts.tts` | próxima fase |
| 5 | Montagem 1080x1920 com FFmpeg e legendas dinâmicas | `mlshorts.video` | próxima fase |
| 6 | Publicação com intervalo mínimo por nicho e fila agendada | `mlshorts.publish` | implementado |
| 7 | Metadados (título, descrição, hashtags, link de afiliado) | `mlshorts.publish` | próxima fase |

## Estrutura

```
config/settings.yaml      categorias monitoradas, filtros e parâmetros de vídeo
src/mlshorts/
  config.py               settings.yaml (pydantic) + segredos do .env
  models.py               Product, Review, ProductImage, VideoScript, VideoMetadata
  collectors/
    base.py               protocolo ProductCollector + CollectorError
    mercadolivre_api.py   API oficial: token -> highlights -> itens -> descrição/reviews
    mercadolivre_scraper.py  fallback Playwright (vitrine "Mais vendidos" + PDP)
    filters.py            regras de aprovação e ranking
    images.py             download das imagens em alta resolução
    service.py            orquestra coleta -> filtro -> imagens -> JSON em data/raw
  scriptgen/
    prompts.py            system prompt Viral Hook + serializacao dos dados do produto
    schema.py             JSON schema das cenas (JSON mode e tool calling)
    providers.py          OpenAIScriptProvider (json_schema strict), AnthropicScriptProvider (tool_use)
    generator.py          ScriptGenerator (validacao/duracao) + ScriptGenerationService
  publish/
    store.py              fila + historico (JsonPublicationStore | SqlitePublicationStore)
    scheduler.py          PublicationScheduler: intervalo por nicho, enfileiramento e process_due
  tts/ video/             etapas seguintes (contratos definidos nos docstrings)
  storage/paths.py        convenção de diretórios em data/
data/{raw,images,audio,video,out}   artefatos por etapa (versionados apenas os .gitkeep)
tests/                    testes unitários (HTTP mockado com respx)
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium      # apenas para o coletor de fallback
cp .env.example .env             # preencha as credenciais
```

`ML_CLIENT_ID` / `ML_CLIENT_SECRET` vêm de uma aplicação criada no
[DevCenter do Mercado Livre](https://developers.mercadolivre.com.br/devcenter). Sem elas o
pipeline usa apenas o coletor por scraping.

## Uso

```bash
mlshorts categories                        # lista as categorias configuradas
mlshorts collect                           # coleta todas as categorias do settings.yaml
mlshorts collect --category MLB1051 -v     # apenas uma categoria, com log detalhado
mlshorts collect --skip-images             # sem baixar imagens
mlshorts script                            # roteiros a partir do ultimo data/raw/products-*.json
mlshorts script --products-file data/raw/products-20260101T000000Z.json

mlshorts queue-add --product-id MLB123 --niche Celulares --media data/video/MLB123.mp4
mlshorts publish-queue          # rode periodicamente (cron): publica o que ja pode ir ao ar
mlshorts queue-list --status pending
```

Cron sugerido (de hora em hora):

```cron
0 * * * * cd /caminho/do/projeto && .venv/bin/mlshorts publish-queue >> data/out/publish.log 2>&1
```

Saída: `data/raw/products-<timestamp>.json` com os produtos aprovados (título, preço, nota,
vendas, ficha técnica, comentários positivos) e as imagens em `data/images/<product_id>/`.

## Geração de roteiro (`scriptgen`)

O system prompt obriga o formato **Viral Hook** com exatamente quatro cenas — `gancho`,
`apresentacao`, `prova_social` e `cta` — e proíbe inventar dados: o prompt do usuário carrega
somente título, preço, marca, nota, avaliações, unidades vendidas, ficha técnica e comentários
positivos reais vindos de `data/raw/products-*.json`.

A resposta é sempre estruturada:

```json
{"cenas": [{"bloco": "gancho", "fala_narrador": "...", "instrucao_visual": "..."}]}
```

- **OpenAI**: `response_format={"type": "json_schema", ..., "strict": true}`.
- **Anthropic**: tool calling com `tool_choice` forçado na ferramenta `gerar_roteiro`.

O `ScriptGenerator` valida a presença dos quatro blocos, reordena as cenas, estima a duração
(`palavras / words_per_second`, default 2.6 para pt-BR) e, se passar de 45s, pede uma reescrita
mais curta antes de desistir. A saída vai para `data/out/scripts-<timestamp>.json`.

## Agendamento de publicação (`publish`)

Nada e publicado em lote: cada nicho respeita um intervalo mínimo entre postagens.

```yaml
publishing:
  min_interval_hours: 24
  interval_hours_by_niche:
    Informatica: 12
  backend: sqlite          # ou json
  queue_path: data/out/publications.sqlite3
  max_per_run: 1
```

- `queue-add` chama `PublicationScheduler.submit()`: se o nicho estiver livre, publica na hora;
  senão grava o vídeo como `pending` com `scheduled_for = ultima_publicacao + intervalo`.
  Vários pendentes do mesmo nicho são espaçados (24h, 48h, ...), nunca no mesmo horário.
- `publish-queue` (o comando do cron) chama `process_due()`: pega os vencidos, revalida o
  intervalo contra a última publicação do nicho, publica no máximo `max_per_run` por rodada e
  reagenda os demais.
- Falha na publicação marca o item como `failed` e **não** consome a janela do nicho.
- O destino final ainda é o `DryRunPublisher` (só loga); a integração com as redes entra junto
  com os metadados, implementando o protocolo `Publisher`.

## Estratégia de coleta

1. **API oficial** (`MercadoLivreAPICollector`): autentica por `client_credentials`, lê
   `/highlights/{site}/category/{id}` para os mais vendidos, faz multiget em `/items`,
   complementa com `/items/{id}/description` e `/reviews/item/{id}` (só notas ≥ 4,
   ordenadas por likes).
2. **Fallback por scraping** (`MercadoLivreScraperCollector`): usado quando não há
   credenciais ou a API falha. Navega em `/mais-vendidos/{categoria}`, abre cada PDP e
   extrai nota, avaliações, ficha técnica, comentários e imagens (thumb `-I` convertida
   para a variante `-F` em alta resolução).

O `CollectionService` encadeia os dois: o primeiro coletor que devolver produtos vence.

## Testes e qualidade

```bash
pytest
ruff check . && ruff format --check .
mypy
```
