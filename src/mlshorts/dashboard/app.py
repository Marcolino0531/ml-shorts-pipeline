"""Dashboard Streamlit do pipeline: `mlshorts dashboard` ou `streamlit run app.py`."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from mlshorts.dashboard.data import DashboardData, load_dashboard_data
from mlshorts.models import (
    Product,
    PublicationStatus,
    QueuedPublication,
    ScriptAudio,
    VideoScript,
)

STATUS_ICONS = {
    PublicationStatus.PENDING: "🕒",
    PublicationStatus.PUBLISHED: "✅",
    PublicationStatus.FAILED: "❌",
    PublicationStatus.CANCELLED: "🚫",
}


def _selected_file(label: str, data: DashboardData, kind: str) -> Path | None:
    artifacts = {
        "products": data.product_files,
        "scripts": data.script_files,
        "narrations": data.narration_files,
    }[kind]()
    if not artifacts:
        return None
    choice = st.selectbox(label, artifacts, format_func=lambda item: item.label)
    return choice.path if choice else None


def render_products(data: DashboardData) -> None:
    st.subheader("Produtos coletados")
    source = _selected_file("Coleta", data, "products")
    products = data.load_products(source)
    if not products:
        st.info("Nenhuma coleta em data/raw. Rode `mlshorts collect`.")
        return

    st.dataframe(
        [
            {
                "ID": product.id,
                "Titulo": product.title,
                "Preco": product.price,
                "Nota": product.rating,
                "Avaliacoes": product.reviews_total,
                "Vendidos": product.sold_quantity,
            }
            for product in products
        ],
        use_container_width=True,
        hide_index=True,
    )

    product = st.selectbox(
        "Detalhe do produto", products, format_func=lambda item: f"{item.id} - {item.title[:60]}"
    )
    if product is None:
        return
    _render_product_detail(data, product)


def _render_product_detail(data: DashboardData, product: Product) -> None:
    status = data.pipeline_status(product.id)
    columns = st.columns(len(status))
    for column, (stage, done) in zip(columns, status.items(), strict=True):
        column.metric(stage.replace("_", " ").title(), "ok" if done else "-")

    images = data.images_for(product.id)
    if images:
        st.image([str(path) for path in images[:4]], width=180)
    if product.attributes:
        st.write("**Ficha tecnica**")
        st.table([{"Atributo": key, "Valor": value} for key, value in product.attributes.items()])
    if product.positive_reviews:
        st.write("**Comentarios positivos**")
        for review in product.positive_reviews[:5]:
            st.markdown(f"> {review.content} — *nota {review.rate}*")


def render_scripts(data: DashboardData) -> None:
    st.subheader("Roteiros gerados")
    source = _selected_file("Geracao", data, "scripts")
    scripts = data.load_scripts(source)
    if not scripts:
        st.info("Nenhum roteiro em data/out. Rode `mlshorts script`.")
        return

    for script in scripts:
        header = f"{script.product_id} - {script.estimated_duration_seconds:.0f}s estimados"
        with st.expander(header, expanded=len(scripts) == 1):
            st.dataframe(
                [
                    {
                        "Bloco": scene.role.value,
                        "Fala do narrador": scene.narration,
                        "Instrucao visual": scene.visual,
                    }
                    for scene in script.scenes
                ],
                use_container_width=True,
                hide_index=True,
            )


def render_audio(data: DashboardData) -> None:
    st.subheader("Narracao por cena")
    narrations = data.load_narrations(_selected_file("Narracao", data, "narrations"))
    if not narrations:
        st.info("Nenhuma narracao em data/out. Rode `mlshorts narrate`.")
        return

    track = st.selectbox(
        "Produto",
        narrations,
        format_func=lambda item: f"{item.product_id} - {item.total_duration_seconds:.1f}s",
    )
    if track is None:
        return
    _render_track(track)


def _render_track(track: ScriptAudio) -> None:
    st.caption(f"voz {track.voice_id} - modelo {track.model_id}")
    for scene in track.scenes:
        st.markdown(
            f"**{scene.index:02d} {scene.role.value}** - inicio {scene.start_seconds:.2f}s, "
            f"duracao {scene.duration_seconds:.2f}s"
        )
        st.caption(scene.text)
        path = Path(scene.audio_path)
        if path.exists():
            st.audio(str(path))
        else:
            st.warning(f"Audio ausente: {path}")


def render_video(data: DashboardData) -> None:
    st.subheader("Video renderizado")
    scripts = data.load_scripts()
    if not scripts:
        st.info("Sem roteiros ainda; a renderizacao vem depois do `mlshorts narrate`.")
        return

    for script in scripts:
        video = data.video_for(script.product_id)
        if video is None:
            st.write(f"{script.product_id}: aguardando a etapa de montagem (FFmpeg).")
            continue
        st.write(f"**{script.product_id}** - {video.name}")
        st.video(str(video))


def render_queue(data: DashboardData) -> None:
    st.subheader("Fila de publicacao")
    config = data.settings.publishing
    columns = st.columns(3)
    columns[0].metric("Intervalo minimo", f"{config.min_interval_hours:.0f}h")
    columns[1].metric("Max por rodada", config.max_per_run)
    columns[2].metric("Aprovacao manual", "on" if config.require_approval else "off")

    items = data.queue()
    if not items:
        st.info("Fila vazia. Use `mlshorts queue-add` para enfileirar um video.")
        return

    st.dataframe(
        [
            {
                "Status": f"{STATUS_ICONS[item.status]} {item.status.value}",
                "Produto": item.product_id,
                "Nicho": item.niche,
                "Agendado para": item.scheduled_for.isoformat(timespec="minutes"),
                "Aprovado em": item.approved_at.isoformat(timespec="minutes")
                if item.approved_at
                else "-",
                "Publicado em": item.published_at.isoformat(timespec="minutes")
                if item.published_at
                else "-",
                "Erro": item.error or "",
            }
            for item in items
        ],
        use_container_width=True,
        hide_index=True,
    )

    pending = [item for item in items if item.status is PublicationStatus.PENDING]
    if pending:
        _render_approval(data, pending)


def _render_approval(data: DashboardData, pending: list[QueuedPublication]) -> None:
    st.write("**Aprovacao manual**")
    item = st.selectbox(
        "Item pendente",
        pending,
        format_func=lambda entry: (
            f"{entry.product_id} ({entry.niche}) - "
            f"{entry.scheduled_for.isoformat(timespec='minutes')}"
        ),
    )
    if item is None:
        return
    approve, cancel = st.columns(2)
    if approve.button("Aprovar", type="primary"):
        approved = data.scheduler.approve(item.id)
        st.success(f"{approved.product_id}: status {approved.status.value}")
        st.rerun()
    if cancel.button("Cancelar"):
        data.scheduler.cancel(item.id)
        st.warning(f"{item.product_id} cancelado")
        st.rerun()


def render_overview(data: DashboardData) -> None:
    products = data.load_products()
    scripts: list[VideoScript] = data.load_scripts()
    narrations = data.load_narrations()
    queue = data.queue()
    columns = st.columns(5)
    columns[0].metric("Produtos", len(products))
    columns[1].metric("Roteiros", len(scripts))
    columns[2].metric("Narracoes", len(narrations))
    columns[3].metric(
        "Na fila", sum(1 for item in queue if item.status is PublicationStatus.PENDING)
    )
    columns[4].metric(
        "Publicados", sum(1 for item in queue if item.status is PublicationStatus.PUBLISHED)
    )


def main() -> None:
    st.set_page_config(page_title="ML Shorts Pipeline", page_icon="🎬", layout="wide")
    st.title("ML Shorts Pipeline")
    data = load_dashboard_data()
    render_overview(data)

    tabs = st.tabs(["Produtos", "Roteiros", "Audio", "Video", "Fila"])
    renderers = (render_products, render_scripts, render_audio, render_video, render_queue)
    for tab, renderer in zip(tabs, renderers, strict=True):
        with tab:
            renderer(data)


if __name__ == "__main__":
    main()
