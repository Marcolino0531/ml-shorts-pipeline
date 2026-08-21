"""Prompts do gerador de roteiro no formato Viral Hook.

O texto destes prompts e escrito com a acentuacao correta do portugues de proposito: o LLM
imita o estilo do que recebe, e um prompt sem acento produz falas sem acento, que chegam
erradas na narracao da ElevenLabs e na legenda queimada no video.
"""

from __future__ import annotations

from mlshorts.models import Product

SYSTEM_PROMPT = """\
Você é um roteirista brasileiro especialista em vídeos curtos virais (TikTok, Reels e Shorts) \
para divulgação de produtos de e-commerce.

Escreva SEMPRE em português do Brasil, em linguagem falada, direta e informal (você/seu), \
sem emojis, sem hashtags, sem markdown e sem ler números de código do produto.

Ortografia: use a acentuação, a cedilha e a pontuação corretas do português em todas as falas \
e instruções (ação, aço, não, você, prático, organização). Nunca escreva sem acento nem troque \
letras acentuadas por equivalentes sem acento: o texto é narrado por uma voz sintética e também \
aparece como legenda no vídeo.

Estrutura obrigatória (formato Viral Hook), nesta ordem e exatamente uma cena por bloco:
1. gancho - primeiros 3 segundos. Uma frase de impacto que interrompe o scroll: dor, curiosidade, \
número surpreendente ou pergunta provocativa. Nunca comece com "olá", "oi" ou "hoje eu vou".
2. apresentacao - o que é o produto e o benefício principal, apoiado em 1 ou 2 especificações \
concretas da ficha técnica fornecida. Fale de benefício, não de lista de atributos.
3. prova_social - credibilidade com base nos dados reais fornecidos: nota, quantidade de \
avaliações, unidades vendidas e o teor dos comentários positivos. Pode parafrasear um comentário, \
nunca invente depoimentos, números ou marcas.
4. cta - chamada para ação curta e urgente direcionando para o link na descrição/bio.

Regras de duração: o roteiro inteiro é narrado em até {max_seconds} segundos, o que significa no \
máximo {max_words} palavras somando todas as falas. Frases curtas, no máximo 20 palavras cada.

Para cada cena entregue também uma instrução visual objetiva para a edição em formato vertical \
1080x1920: qual imagem do produto usar, enquadramento, movimento de câmera (zoom in, pan, corte \
seco), texto em tela e ritmo. Uma instrução por cena, em uma única frase.

Use apenas as informações fornecidas sobre o produto. Se algum dado não existir, simplesmente não \
mencione. Nunca prometa preço, frete ou prazo que não esteja nos dados.\
"""


def build_system_prompt(max_seconds: int, max_words: int) -> str:
    return SYSTEM_PROMPT.format(max_seconds=max_seconds, max_words=max_words)


def build_user_prompt(product: Product, max_specs: int = 8, max_reviews: int = 5) -> str:
    """Serializa os dados brutos coletados do Mercado Livre para o LLM."""
    lines: list[str] = [
        "Gere o roteiro para o produto abaixo.",
        "",
        f"Título: {product.title}",
        f"Categoria: {product.category_name or product.category_id}",
        f"Preço: R$ {product.price:,.2f}",
    ]
    if product.brand:
        lines.append(f"Marca: {product.brand}")
    if product.rating is not None:
        lines.append(f"Nota média: {product.rating:.1f} de 5 ({product.reviews_total} avaliações)")
    if product.sold_quantity:
        lines.append(f"Unidades vendidas: {product.sold_quantity}")
    if product.free_shipping:
        lines.append("Frete grátis: sim")

    specs = _relevant_specs(product, max_specs)
    if specs:
        lines += ["", "Ficha técnica:"]
        lines += [f"- {name}: {value}" for name, value in specs]

    reviews = product.positive_reviews[:max_reviews]
    if reviews:
        lines += ["", "Comentários positivos reais:"]
        lines += [f'- (nota {review.rate}) "{_shorten(review.content)}"' for review in reviews]

    return "\n".join(lines)


_IGNORED_SPECS = {"descricao", "GTIN", "SKU", "Codigo universal de produto"}


def _relevant_specs(product: Product, limit: int) -> list[tuple[str, str]]:
    specs = [
        (name, value)
        for name, value in product.attributes.items()
        if name not in _IGNORED_SPECS and value and len(value) <= 80
    ]
    return specs[:limit]


def _shorten(text: str, limit: int = 220) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else f"{clean[: limit - 1]}…"
