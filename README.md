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
| 6 | Metadados (título, descrição, hashtags, link de afiliado) | `mlshorts.publish` | próxima fase |

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
  tts/ video/ publish/    etapas seguintes (contratos definidos nos docstrings)
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
