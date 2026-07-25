"""Prompts do gerador de roteiro no formato Viral Hook."""

from __future__ import annotations

from mlshorts.models import Product

SYSTEM_PROMPT = """\
Voce e um roteirista brasileiro especialista em videos curtos virais (TikTok, Reels e Shorts) \
para divulgacao de produtos de e-commerce.

Escreva SEMPRE em portugues do Brasil, em linguagem falada, direta e informal (voce/seu), \
sem emojis, sem hashtags, sem markdown e sem ler numeros de codigo do produto.

Estrutura obrigatoria (formato Viral Hook), nesta ordem e exatamente uma cena por bloco:
1. gancho - primeiros 3 segundos. Uma frase de impacto que interrompe o scroll: dor, curiosidade, \
numero surpreendente ou pergunta provocativa. Nunca comece com "ola", "oi" ou "hoje eu vou".
2. apresentacao - o que e o produto e o beneficio principal, apoiado em 1 ou 2 especificacoes \
concretas da ficha tecnica fornecida. Fale de beneficio, nao de lista de atributos.
3. prova_social - credibilidade com base nos dados reais fornecidos: nota, quantidade de \
avaliacoes, unidades vendidas e o teor dos comentarios positivos. Pode parafrasear um comentario, \
nunca invente depoimentos, numeros ou marcas.
4. cta - chamada para acao curta e urgente direcionando para o link na descricao/bio.

Regras de duracao: o roteiro inteiro e narrado em ate {max_seconds} segundos, o que significa no \
maximo {max_words} palavras somando todas as falas. Frases curtas, no maximo 20 palavras cada.

Para cada cena entregue tambem uma instrucao visual objetiva para a edicao em formato vertical \
1080x1920: qual imagem do produto usar, enquadramento, movimento de camera (zoom in, pan, corte \
seco), texto em tela e ritmo. Uma instrucao por cena, em uma unica frase.

Use apenas as informacoes fornecidas sobre o produto. Se algum dado nao existir, simplesmente nao \
mencione. Nunca prometa preco, frete ou prazo que nao esteja nos dados.\
"""


def build_system_prompt(max_seconds: int, max_words: int) -> str:
    return SYSTEM_PROMPT.format(max_seconds=max_seconds, max_words=max_words)


def build_user_prompt(product: Product, max_specs: int = 8, max_reviews: int = 5) -> str:
    """Serializa os dados brutos coletados do Mercado Livre para o LLM."""
    lines: list[str] = [
        "Gere o roteiro para o produto abaixo.",
        "",
        f"Titulo: {product.title}",
        f"Categoria: {product.category_name or product.category_id}",
        f"Preco: R$ {product.price:,.2f}",
    ]
    if product.brand:
        lines.append(f"Marca: {product.brand}")
    if product.rating is not None:
        lines.append(f"Nota media: {product.rating:.1f} de 5 ({product.reviews_total} avaliacoes)")
    if product.sold_quantity:
        lines.append(f"Unidades vendidas: {product.sold_quantity}")
    if product.free_shipping:
        lines.append("Frete gratis: sim")

    specs = _relevant_specs(product, max_specs)
    if specs:
        lines += ["", "Ficha tecnica:"]
        lines += [f"- {name}: {value}" for name, value in specs]

    reviews = product.positive_reviews[:max_reviews]
    if reviews:
        lines += ["", "Comentarios positivos reais:"]
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
