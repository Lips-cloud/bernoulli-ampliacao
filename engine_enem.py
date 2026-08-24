"""
Motor de ampliação - perfil ENEM/simulado padrão Bernoulli (colunas fluidas,
QUESTÃO NN em linha única, alternativas em fonte símbolo A-E).

Validado com: SE2026_V1_BOOK_PROVAI (ENEM, Linguagens + Humanas).
Ainda NÃO cobre: selo QUESTÃO separado (Bahiana), fórmulas em fonte
corrompida (tratar como imagem), páginas de referência em grade (tabela
periódica) - essas entram como perfis à parte, ainda em validação.
"""
import re
import io
import os
import tempfile
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
CODE_RE = re.compile(r'^[A-ZØ0-9]{3,6}$|^\d{2,3}SE[A-Z0-9]{2,4}[A-Z]{3}\d{4}[IVX]*$|^CALIBRADA[_A-Z]*$|^BERENEM\d+$')
GLYPH_MAP = {1: 'A) ', 2: 'B) ', 3: 'C) ', 4: 'D) ', 5: 'E) '}
BODY_FONTS = {'ArialMT', 'Arial-BoldMT', 'Arial-ItalicMT', 'Arial-BoldItalicMT'}

FOOTER_Y_CUTOFF = 800
CODE_FONT_SIZE_MAX = 8.2
GAP_BASELINE = 14.8
GAP_BREAK_RATIO = 1.12
ORIGINAL_BODY_FONT = 9.99
MAX_IMG_W = 172 * mm


def _page_reading_order(page):
    d = page.get_text('dict')
    lines = []
    for b in d['blocks']:
        if b.get('type') != 0:
            continue
        for l in b['lines']:
            x0, y0, x1, y1 = l['bbox']
            if y0 >= FOOTER_Y_CUTOFF:
                continue
            spans = l['spans']
            if not spans:
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
            for ch in s['text']:
                code = ord(ch)
                if code in GLYPH_MAP:
                    buf += GLYPH_MAP[code]
            continue
        txt = s['text']
        if 'Italic' in s['font']:
            txt = f'<i>{txt}</i>'
        if 'Bold' in s['font']:
            txt = f'<b>{txt}</b>'
        buf += txt
    return buf


def _is_alt_line(plain):
    return bool(re.match(r'^[A-E]\)\s', plain))


def _is_source_line(plain):
    return 'Disponível em' in plain or 'Acesso em' in plain


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

        report(0.05, 'Lendo estrutura do PDF...')

        page_images = {}
        for i, page in enumerate(doc):
            for img in page.get_images(full=True):
                xref = img[0]
                for r in page.get_image_rects(xref):
                    page_images.setdefault(i, []).append({'xref': xref, 'rect': r})

        stream = []
        for i, page in enumerate(doc):
            ordered_lines = _page_reading_order(page)
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

        report(0.4, 'Extraindo imagens...')

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
            flow = []
            line_items = [it for it in items if it['type'] == 'line']
            right_edges = [it['x1'] for it in line_items if not _is_alt_line(TAG_RE.sub('', it['text']))]
            col_right = max(right_edges) if right_edges else 560
            margin_tol = 25

            buf_text = ''
            buf_kind = None
            prev_near_margin = None
            prev_y1 = None
            prev_forced = True

            def flush():
                nonlocal buf_text, buf_kind
                if buf_text.strip():
                    style = {'body': body_style, 'verse': verse_style,
                             'alt': alt_style, 'source': source_style}[buf_kind]
                    if buf_kind == 'verse':
                        flow.append(Paragraph(buf_text.replace('\n', '<br/>'), style))
                    else:
                        flow.append(Paragraph(buf_text.strip(), style))
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
                    flow.append(Spacer(1, 4))
                    flow.append(RLImage(path, width=target_w_pt, height=target_h))
                    flow.append(Spacer(1, 6))
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
            return flow

        report(0.6, 'Montando páginas ampliadas...')

        story = []
        for q in questions:
            title = f'QUESTÃO {q["num"]}' if q['num'] != 'REDAÇÃO' else 'PROPOSTA DE REDAÇÃO'
            block = [Paragraph(title, heading_style)] + build_flowables(q['items'])
            story.append(KeepTogether(block))
            story.append(Spacer(1, 14))

        def header_footer(canvas, doc_):
            canvas.saveState()
            w, h = A4
            canvas.setFillColor(colors.HexColor('#0f6b5c'))
            canvas.rect(0, h - 13 * mm, w, 13 * mm, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont('Helvetica-Bold', 10)
            canvas.drawString(15 * mm, h - 8.5 * mm, f'VERSÃO AMPLIADA ({font_size}pt)')
            canvas.setFillColor(colors.HexColor('#0f6b5c'))
            canvas.rect(0, 0, w, 9 * mm, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont('Helvetica', 8.5)
            canvas.drawCentredString(w / 2, 3 * mm, f'BERNOULLI SISTEMA DE ENSINO — PÁGINA {doc_.page}')
            canvas.restoreState()

        report(0.85, 'Gerando PDF final...')

        out_buffer = io.BytesIO()
        out_doc = SimpleDocTemplate(out_buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=16 * mm,
                                     leftMargin=18 * mm, rightMargin=18 * mm)
        out_doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)

        report(1.0, 'Concluído!')
        return out_buffer.getvalue()
