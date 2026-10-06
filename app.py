# -*- coding: utf-8 -*-
"""PSM Ampliação — app Streamlit (perfil ENEM). Fluxo em duas etapas: 1) analisar  2) gerar."""
import hashlib
import pymupdf
import streamlit as st
from ampliador.enem import analisar, ampliar

st.set_page_config(page_title="PSM Ampliação", page_icon="🔎", layout="centered")
st.title("PSM Ampliação")
st.caption("Amplia provas em PDF de layout fechado para alunos com baixa visão.")

PERFIS = {
    "ENEM": True,
    "Bahiana": False, "SBAHIA 2E": False, "Módulo": False, "A4 Bernoulli": False, "Itinerários": False,
}
perfil = st.selectbox(
    "Perfil da prova", list(PERFIS),
    format_func=lambda p: p if PERFIS[p] else f"{p} (em breve)")
if not PERFIS[perfil]:
    st.info("Este perfil ainda não foi implementado. Por enquanto, só o perfil ENEM está disponível.")
    st.stop()

with st.expander("Regras aplicadas (perfil ENEM)"):
    st.markdown(
        "- Texto de **10 pt para 12 pt** (fator 1,2), em coluna única.\n"
        "- Imagens e figuras ampliadas **1,2× na mesma proporção** (recorte exato do original). "
        "Se uma figura não couber na largura da página, ela é limitada e listada no relatório.\n"
        "- **Capa e contracapa não mudam.**\n"
        "- **Cabeçalho e rodapé** do original mantidos em todas as páginas (só o número da página é renumerado).\n"
        "- **Nenhuma questão é quebrada** entre páginas. Só se ela for maior que uma página inteira, "
        "quebra entre o texto-base e o enunciado+alternativas.\n"
        "- Nenhum texto é redigitado: o app usa o texto vivo do PDF e confere, caractere a caractere, "
        "que nada foi perdido ou acrescentado.")

arq = st.file_uploader("PDF da prova (layout fechado do ENEM)", type=["pdf"])
if not arq:
    st.stop()

dados = arq.getvalue()
chave = hashlib.md5(dados).hexdigest()
if st.session_state.get("chave") != chave:
    st.session_state.clear()
    st.session_state["chave"] = chave

# ------------------------------------------------------------ etapa 1
st.subheader("Etapa 1 — Análise")
if st.button("Analisar prova"):
    with st.spinner("Lendo a estrutura do PDF..."):
        try:
            st.session_state["analise"] = analisar(dados)
        except Exception as e:                                   # noqa
            st.session_state.pop("analise", None)
            st.error(f"Não consegui ler este PDF como um caderno do perfil ENEM: {e}")

a = st.session_state.get("analise")
if a:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Páginas", a["paginas"])
    c2.metric("Questões", a["questoes_blocos"])
    c3.metric("Alternativas", a["alternativas"])
    c4.metric("Figuras", a["figuras"])
    if a["alternativas"] != a["alternativas_esperadas"]:
        st.warning(f"Atenção: {a['alternativas']} alternativas encontradas, mas {a['alternativas_esperadas']} "
                   "seriam esperadas (5 por questão). Confira o PDF antes de gerar.")
    else:
        st.success("Estrutura consistente: 5 alternativas para cada questão detectada.")
    st.write("Página de instruções/rascunho da redação:",
             f"página {a['pagina_rascunho']}" if a["pagina_rascunho"] else "não encontrada")
    if a["questoes_divididas"]:
        st.info("Questões grandes demais para uma página a 12 pt (serão divididas entre o texto-base e o "
                "enunciado+alternativas): " + ", ".join(f"{q} ({h} pt)" for q, h in a["questoes_divididas"]))
    else:
        st.write("Nenhuma questão estoura uma página.")
    if a["figuras_limitadas"]:
        st.info("Figuras que não chegam a 1,2× por causa da largura da página: " +
                ", ".join(f"pág. {f['pagina_origem']} (escala {f['escala']}×)" for f in a["figuras_limitadas"]))

    # -------------------------------------------------------- etapa 2
    st.subheader("Etapa 2 — Geração")
    if st.button("Gerar PDF ampliado", type="primary"):
        barra = st.progress(0.0, text="Iniciando...")
        try:
            r = ampliar(dados, lambda p, m: barra.progress(p, text=m))
            st.session_state["resultado"] = r
        except Exception as e:                                   # noqa
            st.session_state.pop("resultado", None)
            st.error(f"Erro ao gerar: {e}")
        barra.empty()

r = st.session_state.get("resultado")
if r:
    aud = r["auditoria"]
    c1, c2 = st.columns(2)
    c1.metric("Páginas (original)", r["paginas_entrada"])
    c2.metric("Páginas (ampliado)", r["paginas_saida"])
    if aud["ok"]:
        fo, fn = f"{aud['orig']:,}".replace(",", "."), f"{aud['novo']:,}".replace(",", ".")
        st.success(f"Auditoria de texto OK: {fo} caracteres no original e {fn} no ampliado, nenhuma diferença.")
    else:
        st.error("Auditoria de texto encontrou diferenças. NÃO use este PDF sem conferir.")
        st.json({"faltando": aud["faltando"], "a_mais": aud["a_mais"]})
    if r["questoes_divididas"]:
        st.info("Questões divididas entre páginas: " + ", ".join(r["questoes_divididas"]))
    else:
        st.write("Nenhuma questão foi dividida entre páginas.")
    if r["figuras_limitadas"]:
        st.info("Figuras abaixo de 1,2×: " +
                ", ".join(f"pág. {f['pagina_origem']} ({f['escala']}×)" for f in r["figuras_limitadas"]))
    st.caption("Não verificado automaticamente: o aspecto visual de cada página. Confira a prévia abaixo.")

    nome = arq.name.rsplit(".", 1)[0] + "_AMPLIADO_fonte12.pdf"
    st.download_button("Baixar PDF ampliado", r["pdf"], file_name=nome, mime="application/pdf")

    with st.expander("Prévia das páginas"):
        doc = pymupdf.open(stream=r["pdf"], filetype="pdf")
        n = st.slider("Página", 1, len(doc), 2)
        pix = doc[n - 1].get_pixmap(dpi=80)
        st.image(pix.tobytes("png"), caption=f"Página {n} de {len(doc)}")
