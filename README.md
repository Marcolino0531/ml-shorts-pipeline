# ml-shorts-pipeline

Pipeline em Python para automatizar vídeos curtos (TikTok/Shorts) de produtos em alta no Mercado Livre.

## Etapas do pipeline

| # | Etapa | Módulo | Status |
|---|-------|--------|--------|
| 1 | Coleta de produtos em alta (API oficial + fallback Playwright) | `mlshorts.collectors` | implementado |
| 2 | Filtro (nota ≥ 4.5, volume de vendas) + download das imagens | `mlshorts.collectors.filters` / `.images` | implementado |
| 3 | Roteiro de até 45s via OpenAI/Claude | `mlshorts.scriptgen` | implementado |
| 4 | Narração via ElevenLabs (áudio por cena + duração exata) | `mlshorts.tts` | implementado |
| 5 | Montagem 1080x1920 com FFmpeg e legendas dinâmicas | `mlshorts.video` | implementado |
| 6 | Publicação com intervalo mínimo por nicho e fila agendada | `mlshorts.publish` | implementado |
| 7 | Metadados (título, descrição, hashtags, link de afiliado) | `mlshorts.publish` | próxima fase |
| — | Dashboard Streamlit de acompanhamento | `mlshorts.dashboard` | implementado |

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
  tts/
    provider.py           ElevenLabsTTSProvider (POST /v1/text-to-speech/{voice_id})
    duration.py           FFprobeDurationProbe: duracao real de cada audio
    service.py            NarrationGenerator (audio por cena + offsets) + NarrationService
  publish/
    store.py              fila + historico (JsonPublicationStore | SqlitePublicationStore)
    scheduler.py          PublicationScheduler: intervalo por nicho, enfileiramento e process_due
  video/
    captions.py           legendas dinamicas (.ass) com a minutagem do narration.json
    renderer.py           VideoRenderer: um comando FFmpeg (imagens + audios + legendas)
    service.py            RenderService: um narration.json -> um data/video/<id>.mp4
  dashboard/
    data.py               DashboardData: le os artefatos de data/ e a fila (sem Streamlit)
    app.py                painel com as abas Produtos/Roteiros/Audio/Video/Fila
  storage/paths.py        convenção de diretórios em data/
scripts/seed_demo_data.py  popula data/ com artefatos ficticios para ver o painel
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

mlshorts narrate                           # narra o ultimo data/out/scripts-*.json
mlshorts narrate --product-id MLB123 -v    # apenas um produto

mlshorts render                            # monta os MP4 1080x1920 em data/video/
mlshorts render --product-id MLB123 -v     # apenas um produto

mlshorts queue-add --product-id MLB123 --niche Celulares --media data/video/MLB123.mp4
mlshorts publish-queue          # rode periodicamente (cron): publica o que ja pode ir ao ar
mlshorts queue-list --status pending

mlshorts dashboard                         # painel em http://localhost:8501
mlshorts dashboard --port 8080 -c config/settings.yaml
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

## Vídeo (`video`)

```bash
mlshorts render               # precisa de ffmpeg no PATH
```

Um único comando FFmpeg por produto, montado a partir de `data/audio/<id>/narration.json`:

- uma imagem de `data/images/<id>/` por cena (as imagens ciclam se houver menos que cenas; sem
  imagem nenhuma, entra fundo sólido), cada uma cobrindo a fala **mais a pausa seguinte**,
  enquadrada em 1080x1920 com `scale`+`pad` e um zoom lento (`zoompan`);
- os áudios entram nos offsets exatos do manifesto (`adelay` por cena + `amix`), então a imagem,
  a legenda e a narração não podem sair de sincronia;
- legendas dinâmicas em blocos de 3 palavras (tempo proporcional ao número de palavras dentro da
  cena) geradas em `.ass` e queimadas com o filtro `subtitles`.

Saída: `data/video/<product_id>.mp4` (H.264 + AAC, `+faststart`) e o `.ass` ao lado para conferência.
Estilo, fonte, zoom, CRF e palavras por legenda ficam na seção `video:` do settings.yaml.

## Dashboard (`dashboard`)

```bash
pip install -e ".[dashboard]"   # ou ".[dev]"
mlshorts dashboard
```

Abas: **Produtos** (tabela da coleta, ficha técnica, comentários, imagens e indicadores de
etapa concluída), **Roteiros** (cenas com fala e instrução visual), **Áudio** (`st.audio` por
cena, com início e duração vindos do manifesto da narração), **Vídeo** (`st.video` quando o MP4
da etapa do FFmpeg existir) e **Fila** (status, horário agendado, aprovação, publicação e erro).

O painel só **lê** `data/` mais a fila configurada em `publishing.queue_path` — nenhuma etapa é
disparada por ele, exceto os botões **Aprovar** / **Cancelar** da fila. Com
`publishing.require_approval: true`, nada é publicado sem aprovação: `queue-add` deixa o item
`pending` e o `publish-queue` ignora itens sem `approved_at`.

Para ver o painel sem gastar API: `python scripts/seed_demo_data.py` gera produto, roteiro,
áudios (silêncio via FFmpeg) e dois itens de fila fictícios.

## Narração (`tts`)

```yaml
tts:
  voice_id:                    # vazio usa ELEVENLABS_VOICE_ID do .env
  model_id: eleven_multilingual_v2
  stability: 0.45              # 0.0 mais expressivo, 1.0 mais monótono
  similarity_boost: 0.8
  style: 0.0
  use_speaker_boost: true
  output_format: mp3_44100_128
  pause_between_scenes_seconds: 0.25
```

Um arquivo **por cena** em `data/audio/<product_id>/NN-<bloco>.mp3` (`00-gancho`,
`01-apresentacao`, `02-prova_social`, `03-cta`), com a duração medida por `ffprobe` — não
estimada. O manifesto `data/audio/<product_id>/narration.json` (e o consolidado
`data/out/narration-<timestamp>.json`) traz `duration_seconds` e `start_seconds` de cada cena,
que é exatamente o que o FFmpeg precisa para trocar imagem e legenda no tempo certo:

```json
{"index": 1, "role": "apresentacao", "duration_seconds": 12.0, "start_seconds": 3.25}
```

`start_seconds` já acumula `pause_between_scenes_seconds`. Falha em um produto (quota, voz
inválida) é registrada e o serviço segue para os demais.

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
