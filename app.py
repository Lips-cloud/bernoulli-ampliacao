import streamlit as st
from engine_enem import process_enem

st.set_page_config(page_title="Ampliação de Provas - Bernoulli", page_icon="🔎", layout="centered")

st.title("🔎 Ampliação de Provas e Simulados")
st.caption("Ferramenta interna Bernoulli — versão de teste (v0.1)")

st.markdown(
    """
    Arraste o PDF **regular** (fechado, colunas normais) e a ferramenta gera
    automaticamente uma versão **ampliada**, mantendo o texto e as imagens
    exatamente como no original — só reorganizando o layout pra caber na
    fonte maior.
    """
)

PERFIS = {
    "ENEM / Simulado padrão (colunas fluidas)": {"key": "enem", "pronto": True},
    "Prova regular com capa (selo QUESTÃO + fórmulas)": {"key": "prova_regular", "pronto": False},
    "Simulado com tabela de referência (ex.: tabela periódica)": {"key": "tabela_ref", "pronto": False},
}

col1, col2 = st.columns([2, 1])
with col1:
    perfil_nome = st.selectbox("Tipo de material", list(PERFIS.keys()))
with col2:
    font_size = st.number_input("Tamanho de fonte alvo (pt)", min_value=10, max_value=24, value=14, step=1)

perfil = PERFIS[perfil_nome]

if not perfil["pronto"]:
    st.warning(
        "⚠️ Esse perfil ainda está em validação (layout diferente do ENEM). "
        "Rodar aqui pode gerar resultado incorreto. Fale com o time antes de usar em produção."
    )

uploaded_file = st.file_uploader("Arraste o PDF aqui", type=["pdf"])

if uploaded_file and st.button("Gerar versão ampliada", type="primary", disabled=not perfil["pronto"]):
    progress_bar = st.progress(0.0)
    status_text = st.empty()

    def progress_cb(frac, msg):
        progress_bar.progress(frac)
        status_text.text(msg)

    try:
        pdf_bytes = uploaded_file.read()
        if perfil["key"] == "enem":
            resultado = process_enem(pdf_bytes, font_size=font_size, progress_cb=progress_cb)
        else:
            st.error("Perfil ainda não implementado.")
            resultado = None

        if resultado:
            st.success("Pronto! Confira o arquivo abaixo antes de usar.")
            st.download_button(
                label="⬇️ Baixar PDF ampliado",
                data=resultado,
                file_name=f"{uploaded_file.name.rsplit('.', 1)[0]}_AMPLIADO.pdf",
                mime="application/pdf",
            )
            st.info(
                "Sempre revise o arquivo gerado antes de enviar pra impressão — "
                "esta é uma ferramenta em validação (v0.1)."
            )
    except Exception as e:
        st.error(f"Deu erro ao processar: {e}")
        st.caption("Manda esse erro pro time de dev revisar — provavelmente é uma particularidade de layout nova.")

st.divider()
st.caption(
    "Motor validado com simulados ENEM no padrão Bernoulli. "
    "Perfis para provas regulares e tabelas de referência em desenvolvimento."
)
