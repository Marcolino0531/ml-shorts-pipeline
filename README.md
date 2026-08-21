# ml-shorts-pipeline

Pipeline em Python para automatizar vídeos curtos (TikTok/Shorts) de produtos em alta no Mercado Livre.

## Etapas do pipeline

| # | Etapa | Módulo | Status |
|---|-------|--------|--------|
| 1 | Coleta de produtos em alta (API oficial + vitrine `/ofertas` via Playwright) | `mlshorts.collectors` | implementado |
| 2 | Filtro (nota ≥ 4.5, volume de vendas) + download das imagens | `mlshorts.collectors.filters` / `.images` | implementado |
| 3 | Roteiro de até 45s via OpenAI/Claude | `mlshorts.scriptgen` | implementado |
| 4 | Narração via ElevenLabs (áudio por cena + duração exata) | `mlshorts.tts` | implementado |
| 5 | Montagem 1080x1920 com FFmpeg e legendas dinâmicas | `mlshorts.video` | implementado |
| 6 | Publicação com intervalo mínimo por nicho e fila agendada | `mlshorts.publish` | implementado |
| 7 | Metadados (título, descrição, hashtags, link de afiliado) + post no YouTube/TikTok | `mlshorts.publish` | implementado |
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
    mercadolivre_scraper.py  scraping Playwright da vitrine /ofertas (+ PDP quando abre)
    ml_categories.py      nomes da categoria e das filhas, para filtrar as ofertas
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
    metadata.py           titulo, descricao, hashtags do nicho e link de afiliado do ML
    youtube.py            YouTubePublisher: upload retomavel na Data API v3 como Shorts
    tiktok.py             TikTokPublisher: Content Posting API (init -> upload -> status)
    publishers.py         build_publisher() + MultiPublisher (varias redes por post)
  video/
    captions.py           legendas dinamicas (.ass) com a minutagem do narration.json
    renderer.py           VideoRenderer: um comando FFmpeg (imagens + audios + legendas)
    service.py            RenderService: um narration.json -> um data/video/<id>.mp4
  dashboard/
    data.py               DashboardData: le os artefatos de data/ e a fila (sem Streamlit)
    app.py                painel com as abas Produtos/Roteiros/Audio/Video/Fila
  storage/paths.py        convenção de diretórios em data/
scripts/
  seed_demo_data.py       popula data/ com artefatos ficticios para ver o painel
  smoke_pipeline.py/.sh   health check: fluxo completo em modo simulado
  pipeline_daily.sh       rodada de producao (collect -> script -> narrate -> render)
deploy/
  setup_linux.sh          provisiona a VPS Ubuntu (Python, FFmpeg, Playwright, venv)
  crontab.example         agendamento por cron
  systemd/                services + timers (coleta 12h, fila 12h, dashboard)
data/{raw,images,audio,video,out}   artefatos por etapa (versionados apenas os .gitkeep)
tests/                    testes unitários (HTTP mockado com respx)
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium      # necessario para a coleta na vitrine /ofertas
cp .env.example .env             # preencha as credenciais
```

### Preenchendo o `.env`

| Variável | Onde conseguir | Obrigatória? |
| --- | --- | --- |
| `ML_CLIENT_ID`, `ML_CLIENT_SECRET` | [DevCenter do Mercado Livre](https://developers.mercadolivre.com.br/devcenter) → *Criar aplicação* (habilite os fluxos *Authorization Code* e *Refresh Token*) | Não — sem elas a coleta cai no scraping por Playwright |
| `ML_REFRESH_TOKEN` | gere uma vez logado como dono da conta: abra `https://auth.mercadolivre.com.br/authorization?response_type=code&client_id=<ML_CLIENT_ID>&redirect_uri=<sua_redirect_uri>`, troque o `code` recebido por token (`POST /oauth/token` com `grant_type=authorization_code`) e copie o `refresh_token` | Sim, junto com as duas acima — `client_credentials` não tem acesso aos endpoints de itens/busca |
| `ML_SITE_ID` | `MLB` para o Brasil | Sim (já vem preenchida) |
| `ML_AFFILIATE_TAG` | [Programa de Afiliados do ML](https://www.mercadolivre.com.br/afiliados/hub) → seu identificador de rastreio | Sim, para monetizar (sem ela o link vai limpo) |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys | Sim, se `SCRIPT_PROVIDER=openai` |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys | Sim, se `SCRIPT_PROVIDER=anthropic` |
| `SCRIPT_PROVIDER` | `openai` ou `anthropic` | Sim |
| `ELEVENLABS_API_KEY` | https://elevenlabs.io/app/settings/api-keys | Sim (narração) |
| `ELEVENLABS_VOICE_ID` | https://elevenlabs.io/app/voice-library → botão **ID** da voz escolhida (ou preencha `tts.voice_id` no YAML) | Sim (narração) |
| `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` | [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → habilite a *YouTube Data API v3* → credencial OAuth do tipo **TVs and Limited Input** ou **Desktop app** | Só para postar no YouTube |
| `YOUTUBE_REFRESH_TOKEN` | gere uma vez no [OAuth Playground](https://developers.google.com/oauthplayground) (engrenagem → *Use your own OAuth credentials*) com o escopo `https://www.googleapis.com/auth/youtube.upload` e copie o *refresh token* | Só para postar no YouTube |
| `TIKTOK_ACCESS_TOKEN` | [TikTok for Developers](https://developers.tiktok.com/) → app com o produto *Content Posting API* e escopo `video.publish` | Só para postar no TikTok |

O `ML_REFRESH_TOKEN` é o único valor que o próprio pipeline reescreve: cada renovação invalida o
token usado, então o novo é gravado de volta no `.env` (com `chmod 600`) ao final da troca. Por
isso, não exporte `ML_REFRESH_TOKEN` como variável de ambiente do sistema — o valor exportado
venceria o `.env` e a coleta seguinte falharia com token inválido (o log avisa se isso acontecer).

O `.env` nunca é comitado (está no `.gitignore`); na VPS deixe-o como `chmod 600 .env`.
Enquanto as credenciais das redes não estiverem prontas, mantenha `publishing.platforms: [dry-run]`
no `config/settings.yaml` — o pipeline roda inteiro sem postar nada.

### Health check

```bash
./scripts/smoke_pipeline.sh          # collect -> script -> tts -> render -> publish, em modo simulado
./scripts/smoke_pipeline.sh --keep   # mantem os artefatos em data/smoke/ para inspecionar
```

Usa as classes de produção e o FFmpeg de verdade, trocando só o que custa dinheiro (coletor do ML,
LLM e ElevenLabs) por dublês — então valida a fiação real: minutagem medida por `ffprobe`, MP4
em 1080x1920, metadados, fila e publicação em dry-run. Saída esperada:

```
  ok collect  MLBSMOKE - nota 4.8 - 2 imagens
  ok script   4 cenas - 16.5s estimados
  ok tts      4 audios - 18.15s medidos
  ok render   1080x1920 - 18.10s - 291KB
  ok publish  published (dry-run) - 3 hashtags
```

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
mlshorts publish --process-queue           # cron: posta no YouTube/TikTok o que ja pode ir ao ar
mlshorts publish --process-queue --dry-run # so registra no log, sem postar
mlshorts queue-list --status pending

mlshorts dashboard                         # painel em http://localhost:8501
mlshorts dashboard --port 8080 -c config/settings.yaml
```

Cron sugerido (de hora em hora):

```cron
0 * * * * cd /caminho/do/projeto && .venv/bin/mlshorts publish --process-queue >> data/out/publish.log 2>&1
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

## Postagem nas redes (`publish --process-queue`)

```yaml
publishing:
  platforms: [youtube, tiktok]   # dry-run enquanto estiver testando
  affiliate_param: matt_word
  default_hashtags: [achadinhos, mercadolivre, ofertas]
  hashtags_by_niche:
    Celulares: [celular, tecnologia]
  youtube:
    category_id: "22"
    privacy_status: public
    shorts_tag: "#Shorts"
  tiktok:
    privacy_level: SELF_ONLY     # obrigatório enquanto o app estiver em sandbox
```

- **Metadados** (`MetadataBuilder`): título = gancho do roteiro (fallback no título do produto)
  + `#Shorts`, cortado em 100 caracteres; descrição com nota, unidades vendidas, link de
  afiliado e as hashtags do nicho + as globais (sem repetir, limitadas por `max_hashtags`);
  o link recebe `matt_word=<ML_AFFILIATE_TAG>` sem duplicar parâmetros já presentes.
- **YouTube** (`YouTubePublisher`): `videos.insert(part="snippet,status")` com `MediaFileUpload`
  resumível em chunks; o vídeo entra como Shorts pelo formato vertical + `#Shorts` no título e
  na descrição. Precisa de `YOUTUBE_CLIENT_ID`/`YOUTUBE_CLIENT_SECRET`/`YOUTUBE_REFRESH_TOKEN`
  (escopo `youtube.upload`).
- **TikTok** (`TikTokPublisher`): `POST /v2/post/publish/video/init/` → `PUT` no `upload_url` →
  poll em `/v2/post/publish/status/fetch/` até `PUBLISH_COMPLETE`. A API responde HTTP 200 com
  `error.code` preenchido, então o corpo é sempre verificado. Precisa de `TIKTOK_ACCESS_TOKEN`
  (escopo `video.publish`).
- Com mais de uma rede, o `MultiPublisher` posta em todas: uma rede fora do ar registra o erro
  no item, e o item só vira `failed` se **nenhuma** rede aceitar.
- A fila continua no comando: só sai o que está vencido (`scheduled_for`), com `approved_at`
  quando `require_approval` está ligado, e no máximo `max_per_run` por rodada. As URLs dos
  posts ficam em `published_urls` e aparecem na aba Fila do dashboard.
- `--dry-run` troca todos os destinos pelo `DryRunPublisher` (só loga) sem mexer no YAML.

## Estratégia de coleta

1. **API oficial** (`MercadoLivreAPICollector`): autentica com `grant_type=refresh_token`
   (token de usuário — `client_credentials` não abre os endpoints de item/busca) e lê
   `/sites/{site}/search?category=<id>&sort=sold_quantity_desc`, que devolve **anúncios**
   direto, paginando até o limite da categoria. Se a API recusar (400) ou ignorar esse `sort`,
   o coletor cai para a ordenação padrão e registra no log quais `available_sorts` existem.
   `/highlights/{site}/category/{id}` ficou como fallback (vitrines curadas): lá os destaques
   vêm em três tipos e os de catálogo (`PRODUCT`/`USER_PRODUCT`) são resolvidos via
   `GET /products/{id}` → `buy_box_winner.item_id`; como esse campo quase sempre vem nulo
   (só é preenchido para token de vendedor — motivo pelo qual a busca virou a fonte
   principal), o coletor então lista os concorrentes em `GET /products/{id}/items` e escolhe
   o anúncio ativo de menor `current_price`. O catálogo só é descartado se essa lista também
   vier vazia.
   Com os ids em mão, faz multiget em `/items`, complementa com `/items/{id}/description` e
   `/reviews/item/{id}` (só notas ≥ 4, ordenadas por likes) e registra o resumo monetário da
   categoria (anúncios, unidades vendidas, ticket médio e faturamento estimado, somados em
   centavos por `collectors/stats.py`).
2. **Scraping da vitrine de ofertas** (`MercadoLivreScraperCollector`): usado quando não há
   credenciais ou a API falha — hoje é a fonte que funciona de fato, porque `/items` e
   `/sites/{site}/search` respondem **403** sem nível de parceiro aprovado. Navega em
   `/ofertas?category=<id>&page=N` (rolando a página para disparar o carregamento dinâmico e
   paginando até juntar `highlights_per_category` × 3 candidatas) e lê de cada card título,
   preço, nota, unidades vendidas, imagem e link. Como a vitrine mistura categorias, além do
   filtro `category=` da URL o coletor confere o caminho de categorias da página do anúncio
   contra o nome da categoria e das filhas (`GET /categories/{id}`, endpoint público) e
   descarta o que vier de fora. O id usado é o do **anúncio** (`wid=` do link), não o do
   catálogo (`/p/MLB...`). A imagem do card (448 px) é trocada pela variante grande
   (`D_NQ_NP_2X_...-F`, ~1080–1200 px) e medida no browser para alimentar
   `filters.min_image_width`.

   A página do anúncio costuma responder com a tela de segurança (captcha) quando o acesso
   vem de IP de datacenter; nesse caso o coletor desiste do enriquecimento depois de três
   bloqueios seguidos e segue apenas com os dados do card. Por isso `filters.min_reviews` vem
   em `0` no `settings.yaml`: a vitrine mostra nota e unidades vendidas, mas não o total de
   avaliações — o corte de qualidade fica com `min_rating` e `min_sold_quantity`.

O `CollectionService` encadeia os dois: o primeiro coletor que devolver produtos vence.

## Deploy na VPS (Hetzner / Ubuntu 22.04+)

Uma CX22 (2 vCPU / 4 GB) dá conta: o render é o único passo pesado e leva segundos por vídeo.

**1. Provisionar**

```bash
ssh root@<ip-da-vps>
apt-get update && apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/Marcolino0531/ml-shorts-pipeline/main/deploy/setup_linux.sh | bash
```

O script instala Python, FFmpeg/ffprobe, git e a fonte DejaVu (usada nas legendas), clona o repo
em `~/ml-shorts-pipeline`, cria a `.venv` com `pip install -e ".[dev]"`, instala o Chromium do
Playwright (`WITH_PLAYWRIGHT=0` desliga), cria `data/*` e o `.env` a partir do exemplo, e roda os
testes no fim. É idempotente — rodar de novo atualiza o repositório e as dependências.

**2. Credenciais e fuso**

```bash
timedatectl set-timezone America/Sao_Paulo   # os horários dos timers seguem o fuso do servidor
cd ~/ml-shorts-pipeline && nano .env         # veja a tabela em "Preenchendo o .env"
./scripts/smoke_pipeline.sh                  # deve terminar com "Pipeline saudavel"
```

**3. Agendar (a cada 12h)** — escolha **um** dos dois:

```bash
# systemd (recomendado: log no journal, Persistent=true recupera execuções perdidas)
cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mlshorts-collect.timer mlshorts-publish.timer
systemctl list-timers 'mlshorts*'
journalctl -u mlshorts-collect -f

# ou cron
crontab -e   # cole o conteúdo de deploy/crontab.example
```

`mlshorts-collect.timer` roda `scripts/pipeline_daily.sh` às 06:00 e 18:00 (coleta → roteiro →
narração → render → `queue-add` no nicho de `NICHE=`), e `mlshorts-publish.timer` roda
`publish --process-queue` às 00:00 e 12:00. Quem decide de fato se um vídeo vai ao ar continua
sendo o `publishing.min_interval_hours` + a fila, então rodar o timer com folga é seguro.
Ajuste `User=` e os caminhos `/root/...` nos units se instalou em outro usuário/diretório.

**4. Dashboard como serviço**

```bash
cp deploy/systemd/mlshorts-dashboard.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now mlshorts-dashboard
ssh -N -L 8501:127.0.0.1:8501 root@<ip-da-vps>   # abra http://localhost:8501 na sua máquina
```

O serviço escuta só em `127.0.0.1` de propósito: o painel aprova publicações e mostra dados de
afiliado, então exponha-o por túnel SSH ou atrás de um proxy com HTTPS e senha — nunca direto na
internet. Com `publishing.require_approval: true`, nada é postado sem clicar em **Aprovar** aqui.

## Testes e qualidade

```bash
pytest
ruff check . && ruff format --check .
mypy
./scripts/smoke_pipeline.sh      # integracao ponta a ponta (FFmpeg de verdade, APIs simuladas)
```
