# PSM Ampliação (perfil ENEM)

App Streamlit que amplia cadernos do ENEM em PDF de layout fechado (2 colunas, Calibri 10 pt)
para alunos com baixa visão: texto 12 pt, figuras 1,2×, coluna única, capa/cabeçalho/rodapé mantidos,
nenhuma questão quebrada entre páginas.

## Estrutura
- `app.py` — interface (etapa 1: analisar; etapa 2: gerar + auditoria + download + prévia)
- `ampliador/enem_parse.py` — leitura estrutural do PDF (texto vivo, sem OCR)
- `ampliador/enem.py` — remontagem (reportlab) + figuras/cabeçalho/rodapé originais (PyMuPDF) + auditoria
- `fonts/` — Carlito (métrica compatível com Calibri, licença OFL)
- `requirements.txt`

## Rodar localmente
    pip install -r requirements.txt
    streamlit run app.py

## Perfis
Só o perfil ENEM está implementado. Bahiana, SBAHIA 2E, Módulo, A4 Bernoulli e Itinerários
estão listados no app como "em breve".
