"""
Motor de ampliação - perfil ENEM/simulado padrão Bernoulli (colunas fluidas,
QUESTÃO NN em linha única, alternativas em fonte símbolo A-E).

Validado com: SE2026_V1_BOOK_PROVAI, SE2026_V3 (ENEM, Linguagens + Humanas).

v2 - correções:
  1) questão nunca mais quebra no meio de uma alternativa (quebra só é
     permitida, em último caso, entre o enunciado e o bloco de alternativas
     - que agora ficam sempre juntas)
  2) filtro de cabeçalho/rodapé institucional agora é dinâmico (relativo à
     altura real da página, não um pixel fixo) + uma rede de segurança por
     frequência de repetição, para funcionar em PDFs de outras famílias
  3) banner "VERSÃO AMPLIADA" removido
  4) rodapé de saída reconstruído no estilo visual do original (linhas
     verde-água duplas + mesma paleta), com o rótulo de prova (LCT/CH/etc.)
     detectado automaticamente a partir do próprio PDF de origem
  5) capa do PDF original é rasterizada e inserida como página 1 da saída
"""

import re
import io
import os
import tempfile
import collections
import pymupdf as fitz
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from PIL import Image as PILImage

HEADING_RE = re.compile(r'^QUEST[ÃA]O\s+(\d+)\s*$')
TAG_RE = re.compile(r'</?[bi]>')
SECTION_RE = re.compile(
    r'^(LINGUAGENS|CI[ÊE]NCIAS (HUMANAS|DA NATUREZA)|MATEM[ÁA]TICA)[,\sA-ZÀ-Ú]*$'
    r'|^Quest[õo]es de \d+ a \d+.*$',
    re.IGNORECASE
)
CODE_RE = re.compile(
    r'^[A-ZØ0-9]{3,6}$'
    r'|^\d{2,3}SE[A-Z0-9]{2,4}[A-Z]{3}\d{4}[IVX]*$'
    r'|^CALIBRADA[_A-Z]*$'
    r'|^BERENEM[A-Z0-9]+$'  # cobre BERENEM2026, BERENEM2026LC10047 etc.
)
PROVA_LABEL_RE = re.compile(r'^([A-ZÀ-Ú]{2,4})\s*[–-]\s*PROVA\s+[IVX]+\s*[–-]\s*P[ÁA]GINA\s*\d+', re.IGNORECASE)
INSTITUTION_RE = re.compile(r'BERNOULLI SISTEMA DE ENSINO', re.IGNORECASE)
DIGIT_RE = re.compile(r'\d+')

GLYPH_MAP = {1: 'A) ', 2: 'B) ', 3: 'C) ', 4: 'D) ', 5: 'E) '}
BODY_FONTS = {'ArialMT', 'Arial-BoldMT', 'Arial-ItalicMT', 'Arial-BoldItalicMT'}

CODE_FONT_SIZE_MAX = 8.2
GAP_BASELINE = 14.8
GAP_BREAK_RATIO = 1.12
ORIGINAL_BODY_FONT = 9.99
MAX_IMG_W = 172 * mm

# faixa (relativa à altura da página) considerada cabeçalho/rodapé
HEADER_BAND_RATIO = 0.05
FOOTER_BAND_RATIO = 0.945

# paleta extraída das linhas duplas verde-água do PDF original
TEAL_LIGHT = colors.Color(0.0042, 0.6839, 0.5918)
TEAL_DARK = colors.Color(0.0, 0.6596, 0.5571)


def _boilerplate_lines(doc):
    """Primeira passada: identifica linhas de cabeçalho/rodapé institucional
    por REPETIÇÃO (mesmo texto, ignorando dígitos, aparecendo em várias
    páginas dentro das faixas de topo/base) - funciona pra qualquer família
    de prova, não só ENEM."""
    counter = collections.Counter()
    n_pages = len(doc)
    for page in doc:
        h = page.rect.height
        top_cut = h * HEADER_BAND_RATIO
        bot_cut = h * FOOTER_BAND_RATIO
        d = page.get_text('dict')
        for b in d['blocks']:
            if b.get('type') != 0:
                continue
            for l in b['lines']:
                x0, y0, x1, y1 = l['bbox']
                if not (y0 <= top_cut or y0 >= bot_cut):
                    continue
                txt = ''.join(s['text'] for s in l['spans']).strip()
                if not txt:
                    continue
                norm = DIGIT_RE.sub('#', txt)
                counter[norm] += 1
    threshold = max(2, int(n_pages * 0.35))
    return {norm for norm, c in counter.items() if c >= threshold}


def _detect_footer_meta(doc):
    """Extrai o texto institucional fixo + mapeia, por página, o rótulo de
    prova (ex.: LCT, CH) que aparece no rodapé original, pra reaproveitar
    no PDF ampliado."""
    institution = None
    volume_label = None
    page_prova = {}
    for i, page in enumerate(doc):
        h = page.rect.height
        bot_cut = h * FOOTER_BAND_RATIO
        d = page.get_text('dict')
        for b in d['blocks']:
            if b.get('type') != 0:
                continue
            for l in b['lines']:
                x0, y0, x1, y1 = l['bbox']
                if y0 < bot_cut:
                    continue
                txt = ''.join(s['text'] for s in l['spans']).strip()
                if not txt:
                    continue
                if INSTITUTION_RE.search(txt) and institution is None:
                    institution = txt
                m = PROVA_LABEL_RE.match(txt)
                if m:
                    page_prova[i] = m.group(1).upper()
                elif volume_label is None and '–' in txt and 'PÁGINA' not in txt.upper() \
                        and not INSTITUTION_RE.search(txt):
                    volume_label = txt
    return institution or 'BERNOULLI SISTEMA DE ENSINO', volume_label, page_prova


def _page_reading_order(page, boilerplate):
    d = page.get_text('dict')
    h = page.rect.height
    top_cut = h * HEADER_BAND_RATIO
    bot_cut = h * FOOTER_BAND_RATIO
    lines = []
    for b in d['blocks']:
        if b.get('type') != 0:
            continue
        for l in b['lines']:
            x0, y0, x1, y1 = l['bbox']
            spans = l['spans']
            if not spans:
                continue
            txt_probe = ''.join(s['text'] for s in spans).strip()
            norm = DIGIT_RE.sub('#', txt_probe)
            if (y0 >= bot_cut or y0 <= top_cut) and norm in boilerplate:
                continue
            if y0 >= bot_cut:
                continue
            if all(s['size'] <= CODE_FONT_SIZE_MAX for s in spans) and x0 > 400 and y1 < 60:
                continue
            lines.append({'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'spans': spans})
    mid = page.rect.width / 2
    right_group = [l for l in lines if l['x0'] >= mid]
    left_group = [l for l in lines if l['x0'] < mid]
    if len(right_group) >= 5:
        left_group.sort(key=lambda l: l['y0'])
        right_group.sort(key=lambda l: l['y0'])
        return left_group + right_group
    lines.sort(key=lambda l: (l['y0'], l['x0']))
    return lines


def _line_text(line):
    buf = ''
    for s in line['spans']:
        if s['font'] not in BODY_FONTS:
            mapped = ''
            for ch in s['text']:
                code = ord(ch)
                if code in GLYPH_MAP:
                    # fonte símbolo com glifos codificados em caracteres de
                    # controle (ex.: SE2026_V1) - remapeia pra "A) " etc.
                    mapped += GLYPH_MAP[code]
                elif 32 <= code < 127:
                    # fonte customizada que já grava o caractere legível
                    # (ex.: SE2026_V3 usa a fonte "teste" com texto literal
                    # "A. ") - mantém como está
                    mapped += ch
                # outros códigos de controle desconhecidos são descartados
            buf += mapped
            continue
        txt = s['text']
        if 'Italic' in s['font']:
            txt = f'<i>{txt}</i>'
        if 'Bold' in s['font']:
            txt = f'<b>{txt}</b>'
        buf += txt
    return buf


def _is_alt_line(plain):
    # aceita tanto "A) " (glifo remapeado) quanto "A. " (texto literal)
    return bool(re.match(r'^[A-E][).]\s', plain))


def _is_source_line(plain):
    return 'Disponível em' in plain or 'Acesso em' in plain


def _rasterize_cover(doc, tmpdir):
    page = doc[0]
    pix = page.get_pixmap(dpi=200)
    path = os.path.join(tmpdir, 'cover.png')
    pix.save(path)
    return path, page.rect.width, page.rect.height


def process_enem(input_pdf_bytes: bytes, font_size: int = 14, font_name: str = 'Helvetica',
                  progress_cb=None) -> bytes:
    """Recebe os bytes de um PDF no padrão ENEM/simulado Bernoulli e devolve
    os bytes do PDF ampliado. progress_cb(float 0-1, str texto) é opcional,
    para a barra de progresso do Streamlit."""

    def report(frac, msg):
        if progress_cb:
            progress_cb(frac, msg)

    leading = font_size * 1.32
    scale_factor = font_size / ORIGINAL_BODY_FONT

    with tempfile.TemporaryDirectory() as tmpdir:
        doc = fitz.open(stream=input_pdf_bytes, filetype='pdf')

        report(0.03, 'Lendo estrutura do PDF...')
        cover_path, cover_w, cover_h = _rasterize_cover(doc, tmpdir)

        report(0.07, 'Detectando cabeçalho e rodapé originais...')
        boilerplate = _boilerplate_lines(doc)
        institution, volume_label, page_prova = _detect_footer_meta(doc)

        report(0.12, 'Extraindo imagens e texto...')
        page_images = {}
        for i, page in enumerate(doc):
            for img in page.get_images(full=True):
                xref = img[0]
                for r in page.get_image_rects(xref):
                    page_images.setdefault(i, []).append({'xref': xref, 'rect': r})

        stream = []
        for i, page in enumerate(doc):
            ordered_lines = _page_reading_order(page, boilerplate)
            imgs = page_images.get(i, [])
            used_imgs = set()
            for line in ordered_lines:
                for idx, im in enumerate(imgs):
                    if idx in used_imgs:
                        continue
                    ir = im['rect']
                    if ir.y1 <= line['y0'] + 2:
                        stream.append({'type': 'image', 'page': i, 'xref': im['xref'], 'rect': ir})
                        used_imgs.add(idx)
                txt = _line_text(line)
                if txt.strip():
                    stream.append({'type': 'line', 'page': i, 'y0': line['y0'], 'y1': line['y1'],
                                    'x0': line['x0'], 'x1': line['x1'], 'text': txt})
            for idx, im in enumerate(imgs):
                if idx not in used_imgs:
                    stream.append({'type': 'image', 'page': i, 'xref': im['xref'], 'rect': im['rect']})

        report(0.25, 'Detectando questões...')
        questions = []
        current = None
        pending_prefix = []
        for item in stream:
            if item['type'] == 'line':
                plain = TAG_RE.sub('', item['text']).strip()
                m = HEADING_RE.match(plain)
                if m:
                    if current:
                        questions.append(current)
                    current = {'num': m.group(1), 'items': list(pending_prefix)}
                    pending_prefix = []
                    continue
                if SECTION_RE.match(plain):
                    pending_prefix.append(item)
                    continue
                if current is None:
                    continue
                current['items'].append(item)
            else:
                if current is None:
                    continue
                current['items'].append(item)
        if current:
            questions.append(current)

        for q in questions:
            if q['num'] == '45':
                for idx, it in enumerate(q['items']):
                    if it['type'] == 'line' and 'INSTRUÇÕES PARA A REDAÇÃO' in TAG_RE.sub('', it['text']):
                        redacao_items = q['items'][idx:]
                        q['items'] = q['items'][:idx]
                        questions.append({'num': 'REDAÇÃO', 'items': redacao_items})
                        break

        # rótulo de prova por questão: pega o mais frequente entre as páginas
        # de origem dos itens daquela questão
        for q in questions:
            pages_seen = [it['page'] for it in q['items'] if 'page' in it]
            labels = [page_prova[p] for p in pages_seen if p in page_prova]
            q['prova_label'] = collections.Counter(labels).most_common(1)[0][0] if labels else None

        report(0.4, 'Extraindo imagens em alta resolução...')
        img_cache = {}

        def get_image_path(page_idx, xref, rect):
            key = (page_idx, xref)
            if key in img_cache:
                return img_cache[key]
            page = doc[page_idx]
            pix = page.get_pixmap(clip=rect, dpi=200)
            path = os.path.join(tmpdir, f'img_{page_idx}_{xref}.png')
            pix.save(path)
            img_cache[key] = path
            return path

        heading_style = ParagraphStyle('heading', fontName=font_name + '-Bold', fontSize=font_size + 2,
                                        leading=leading + 3, spaceAfter=10, spaceBefore=4)
        body_style = ParagraphStyle('body', fontName=font_name, fontSize=font_size,
                                     leading=leading, spaceAfter=7, alignment=4)
        verse_style = ParagraphStyle('verse', fontName=font_name, fontSize=font_size, leading=leading, spaceAfter=0)
        alt_style = ParagraphStyle('alt', fontName=font_name, fontSize=font_size,
                                    leading=leading, spaceAfter=6, leftIndent=14)
        source_style = ParagraphStyle('source', fontName=font_name + '-Oblique', fontSize=max(font_size - 3, 8),
                                       leading=max(font_size - 3, 8) * 1.3, spaceAfter=8, spaceBefore=2)

        def build_flowables(items):
            """Retorna (pre_alt, alt_block): pre_alt é o enunciado (texto +
            imagens + fonte), alt_block é a lista de alternativas A-E. Os
            dois grupos são montados como blocos atômicos separados, então
            uma quebra de página só pode acontecer ENTRE os dois blocos -
            nunca dentro do enunciado nem dentro do bloco de alternativas."""
            pre_alt, alt_block = [], []

            line_items = [it for it in items if it['type'] == 'line']
            right_edges = [it['x1'] for it in line_items if not _is_alt_line(TAG_RE.sub('', it['text']))]
            col_right = max(right_edges) if right_edges else 560
            margin_tol = 25

            buf_text = ''
            buf_kind = None
            prev_near_margin = None
            prev_y1 = None
            prev_forced = True

            def target_list():
                return alt_block if buf_kind == 'alt' else pre_alt

            def flush():
                nonlocal buf_text, buf_kind
                if buf_text.strip():
                    style = {'body': body_style, 'verse': verse_style,
                             'alt': alt_style, 'source': source_style}[buf_kind]
                    text = buf_text.strip()
                    if buf_kind == 'alt':
                        # normaliza "A." ou "A)" pra sempre exibir "A) "
                        text = re.sub(r'^([A-E])[).]\s*', r'\1) ', text)
                    flowable = Paragraph(buf_text.replace('\n', '<br/>'), style) if buf_kind == 'verse' \
                        else Paragraph(text, style)
                    target_list().append(flowable)
                buf_text = ''
                buf_kind = None

            for it in items:
                if it['type'] == 'image':
                    flush()
                    prev_near_margin = None
                    prev_forced = True
                    path = get_image_path(it['page'], it['xref'], it['rect'])
                    orig_w_pt = it['rect'].width
                    orig_h_pt = it['rect'].height
                    target_h = orig_h_pt * scale_factor
                    target_w_pt = orig_w_pt * scale_factor
                    if target_w_pt > MAX_IMG_W:
                        cap = MAX_IMG_W / target_w_pt
                        target_w_pt *= cap
                        target_h *= cap
                    pre_alt.append(Spacer(1, 4))
                    pre_alt.append(RLImage(path, width=target_w_pt, height=target_h))
                    pre_alt.append(Spacer(1, 6))
                    continue

                raw = it['text']
                plain = TAG_RE.sub('', raw).strip()
                if not plain:
                    continue
                if CODE_RE.match(plain):
                    continue

                near_margin = (col_right - it['x1']) < margin_tol
                gap = (it['y0'] - prev_y1) if prev_y1 is not None else None
                starts_alt = _is_alt_line(plain)
                starts_source = _is_source_line(plain)

                is_new_group = (
                    prev_forced or starts_alt or (starts_source and buf_kind != 'source') or
                    (buf_kind == 'source' and not starts_source) or
                    (prev_near_margin is False and (gap is None or gap > GAP_BASELINE * GAP_BREAK_RATIO))
                )

                if is_new_group:
                    flush()
                    if starts_alt:
                        buf_kind = 'alt'
                    elif starts_source:
                        buf_kind = 'source'
                    else:
                        buf_kind = 'body' if near_margin else 'verse'
                    buf_text = raw
                else:
                    buf_text += ('\n' if buf_kind == 'verse' else ' ') + raw

                prev_near_margin = near_margin
                prev_y1 = it['y1']
                prev_forced = False
            flush()
            return pre_alt, alt_block

        report(0.6, 'Montando páginas ampliadas...')
        story = []
        for q in questions:
            title = f'QUESTÃO {q["num"]}' if q['num'] != 'REDAÇÃO' else 'PROPOSTA DE REDAÇÃO'
            pre_alt, alt_block = build_flowables(q['items'])
            # bloco 1: heading + enunciado inteiro -> nunca quebra sozinho
            story.append(KeepTogether([Paragraph(title, heading_style)] + pre_alt))
            # bloco 2: todas as alternativas juntas -> nunca quebra entre si
            # nem no meio de uma alternativa; só pode "empurrar" pra próxima
            # página como um todo, se o bloco 1 já encheu a página atual
            if alt_block:
                story.append(KeepTogether(alt_block))
            story.append(Spacer(1, 14))

        def header_footer(canvas, doc_):
            canvas.saveState()
            w, h = A4
            # rodapé no estilo visual do original: duas linhas verde-água
            y_line_top = 16 * mm
            canvas.setStrokeColor(TEAL_LIGHT)
            canvas.setLineWidth(0.7)
            canvas.line(15 * mm, y_line_top + 1.6, w - 15 * mm, y_line_top + 1.6)
            canvas.setStrokeColor(TEAL_DARK)
            canvas.setLineWidth(1)
            canvas.line(15 * mm, y_line_top, w - 15 * mm, y_line_top)

            canvas.setFillColor(TEAL_DARK)
            canvas.setFont('Helvetica-Bold', 8)
            canvas.drawString(15 * mm, y_line_top - 10, institution.upper())

            center_text = volume_label or ''
            canvas.drawCentredString(w / 2, y_line_top - 10, center_text)

            canvas.drawRightString(w - 15 * mm, y_line_top - 10, f'PÁGINA {doc_.page}')
            canvas.restoreState()

        report(0.85, 'Gerando PDF final...')
        out_buffer = io.BytesIO()
        out_doc = SimpleDocTemplate(out_buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=24 * mm,
                                     leftMargin=18 * mm, rightMargin=18 * mm)
        out_doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)

        report(0.93, 'Montando capa...')
        body_doc = fitz.open(stream=out_buffer.getvalue(), filetype='pdf')
        final_doc = fitz.open()
        cover_page = final_doc.new_page(width=cover_w, height=cover_h)
        cover_page.insert_image(cover_page.rect, filename=cover_path)
        final_doc.insert_pdf(body_doc)

        report(1.0, 'Concluído!')
        return final_doc.tobytes()
