# -*- coding: utf-8 -*-
"""PSM Ampliação — app Streamlit (perfil ENEM). Fluxo em duas etapas: 1) analisar  2) gerar."""
import hashlib
import pymupdf
import streamlit as st
from enem import analisar, ampliar

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
        "- Imagens e figuras ampliadas **1,2× na mesma proporção** (recorte exato do original).\n"
        "- Figura que **não cabe na página a 1,2×**: o app tenta **girar 90°**. Se girada também não chegar a 1,2×, "
        "a figura fica **sozinha na página** (com crédito/fonte) e o restante da questão segue na página seguinte, "
        "para você girar/ajustar à mão sem mexer em mais nada.\n"
        "- **Tabelas, gráficos, esquemas, equações e alternativas com fração/raiz** são tratados como **imagem**: "
        "o app recorta o bloco do PDF original e amplia 1,2× na mesma proporção (sem tentar reconstruir como texto). "
        "Fração ou raiz no meio de um parágrafo vira uma pequena imagem dentro da linha. "
        "Subscritos e sobrescritos (CO₂, 10⁹) continuam sendo texto.\n"
        "- **Prova I** (questões 1–90, com inglês/espanhol e redação) e **Prova II** (91–180) são identificadas "
        "pela numeração. Na Prova I, a **folha de rascunho da redação** é ampliada e sai em **2 páginas** "
        "(linhas 1–15 e 16–30), depois da página de instruções.\n"
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
    if a["prova"]:
        q0, q1 = a["faixa_questoes"]
        extra = " (inglês/espanhol nas questões 1–5)" if a["ingles_espanhol"] else ""
        st.write(f"**Prova {a['prova']}** — questões {q0} a {q1}{extra}.")
    for av in a["avisos"]:
        st.warning(av)
    if a["pagina_rascunho"]:
        st.write(f"Página de instruções/rascunho da redação no original: página {a['pagina_rascunho']}. "
                 f"No ampliado: instruções em página própria + {a['paginas_rascunho_saida']} páginas de rascunho.")
    else:
        st.write("Sem página de redação neste caderno.")
    if a["questoes_divididas"]:
        st.info("Questões grandes demais para uma página a 12 pt (serão divididas entre o texto-base e o "
                "enunciado+alternativas): " + ", ".join(f"{q} ({h} pt)" for q, h in a["questoes_divididas"]))
    else:
        st.write("Nenhuma questão estoura uma página.")
    if a.get("blocos_imagem"):
        qs = sorted({b["questao"] for b in a["blocos_imagem"] if b.get("questao")})
        st.info(f"{len(a['blocos_imagem'])} bloco(s) serão tratados como imagem (tabelas, gráficos, fórmulas, "
                f"alternativas com fração/raiz) em {len(qs)} questão(ões): " + ", ".join(q.replace("QUESTÃO ", "") for q in qs))
    if a.get("inline_imagens"):
        st.info("Frações/raízes dentro de parágrafos viram imagem pequena na linha (páginas do original: "
                + ", ".join(str(p) for p in a["inline_imagens"]) + ").")
    for f in a["figuras_revisao"]:
        if f["modo"] == "girada":
            st.info(f"{f['questao']}: figura grande demais na posição normal; será **girada 90°** e ampliada 1,2× "
                    "(página própria para a figura).")
        else:
            st.warning(f"{f['questao']}: figura não chega a 1,2× nem girada. Ficará **sozinha na página** a "
                       f"{f['escala']}× (girada chegaria a {f['escala_girada']}×) — **revisão manual**.")

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
    for av in r["avisos"]:
        st.warning(av)
    if r["conferencia_ok"]:
        st.success("Conferência final: todas as checagens passaram.")
    else:
        st.error("A conferência final encontrou problemas. NÃO use este PDF sem conferir os itens em vermelho.")
    for c in r["checagens"]:
        st.markdown(f"{'✅' if c['ok'] else '❌'} **{c['nome']}** — {c['detalhe']}")
    if r["questoes_divididas"]:
        st.info("Questões divididas entre páginas: " + ", ".join(r["questoes_divididas"]))
    else:
        st.write("Nenhuma questão foi dividida entre páginas.")
    if r.get("blocos_imagem"):
        st.caption(f"Blocos tratados como imagem: {len(r['blocos_imagem'])} — confira na prévia as tabelas, gráficos e fórmulas.")
    for f in r["figuras_revisao"]:
        if f["modo"] == "girada":
            st.info(f"{f['questao']} (pág. {f['pagina_saida']} do ampliado): figura girada 90°, 1,2×.")
        else:
            st.warning(f"{f['questao']} (pág. {f['pagina_saida']} do ampliado): figura sozinha na página a "
                       f"{f['escala']}× — **girar/ajustar manualmente**.")
    st.caption("Não verificado automaticamente: o aspecto visual de cada página. Confira a prévia abaixo.")

    nome = arq.name.rsplit(".", 1)[0] + "_AMPLIADO_fonte12.pdf"
    st.download_button("Baixar PDF ampliado", r["pdf"], file_name=nome, mime="application/pdf")

    with st.expander("Prévia das páginas"):
        doc = pymupdf.open(stream=r["pdf"], filetype="pdf")
        n = st.slider("Página", 1, len(doc), 2)
        pix = doc[n - 1].get_pixmap(dpi=80)
        st.image(pix.tobytes("png"), caption=f"Página {n} de {len(doc)}")
