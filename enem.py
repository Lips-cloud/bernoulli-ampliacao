# -*- coding: utf-8 -*-
"""
PSM Ampliação — perfil ENEM (caderno 2 colunas, Calibri 10 pt) -> coluna única, 12 pt (x1,2).

API:
    analisar(pdf_bytes)            -> dict  (etapa 1: não gera PDF)
    ampliar(pdf_bytes, progress)   -> dict  (etapa 2: PDF + relatório + auditoria)
"""
import os, re, hashlib, tempfile, threading
import pymupdf
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.colors import Color, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph, KeepTogether,
                                Flowable, PageBreak)
from reportlab.platypus.flowables import CondPageBreak

from .enem_parse import build_items, load_page, Ln, find_rascunho_page

S = 1.2                                   # fator de ampliação (10 pt -> 12 pt)
_HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(os.path.dirname(_HERE), 'fonts') + os.sep
if not os.path.exists(FONT_DIR + 'Carlito-Regular.ttf'):
    FONT_DIR = '/usr/share/fonts/truetype/crosextra/'
pdfmetrics.registerFont(TTFont('Car', FONT_DIR + 'Carlito-Regular.ttf'))
pdfmetrics.registerFont(TTFont('Car-B', FONT_DIR + 'Carlito-Bold.ttf'))
pdfmetrics.registerFont(TTFont('Car-I', FONT_DIR + 'Carlito-Italic.ttf'))
pdfmetrics.registerFont(TTFont('Car-BI', FONT_DIR + 'Carlito-BoldItalic.ttf'))
pdfmetrics.registerFontFamily('Car', normal='Car', bold='Car-B', italic='Car-I', boldItalic='Car-BI')

INK = Color(0.137, 0.122, 0.125)
CYAN = Color(0.0, 0.74, 0.95)
LIGHT = Color(0.752, 0.909, 0.984)

# geometria da página de conteúdo (coordenadas do CropBox)
W, H = 566.9278, 779.5258
FRAME_W = 517.0
FRAME_TOP, FRAME_BOT = 78.0, 742.0        # em coordenadas "de cima"
FRAME_H = FRAME_BOT - FRAME_TOP
X_EVEN, X_ODD = 31.0, 24.0

TMP = tempfile.mkdtemp(prefix='ampl_')
LOCK = threading.Lock()
WARN = []        # avisos de questões divididas
GROUP_LABEL = {}

# ------------------------------------------------------------------ estilos
def mk(name, **kw):
    base = dict(fontName='Car', fontSize=12, leading=15, textColor=INK, alignment=TA_JUSTIFY,
                spaceBefore=0, spaceAfter=0, allowWidows=1, allowOrphans=1)
    base.update(kw)
    return ParagraphStyle(name, **base)

ST_BODY = mk('body')
ST_LEFT = mk('left', alignment=TA_LEFT)
ST_TITLE = mk('title', fontName='Car-B', alignment=TA_CENTER, spaceBefore=3, spaceAfter=5)
ST_LABEL = mk('label', fontName='Car-B', alignment=TA_LEFT, spaceBefore=6, spaceAfter=3)
ST_CAP = mk('cap', fontSize=9.6, leading=11.6, alignment=TA_RIGHT, spaceBefore=5, spaceAfter=8)
ST_ALT = mk('alt', alignment=TA_LEFT, leftIndent=16.0, spaceBefore=2.2)


def esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def emoji_png(rect, pno, src_doc):
    key = hashlib.md5(f'{pno}{tuple(round(v,1) for v in rect)}'.encode()).hexdigest()[:10]
    path = os.path.join(TMP, f'emo_{key}.png')
    if not os.path.exists(path):
        pix = src_doc[pno].get_pixmap(clip=rect, dpi=300)
        pix.save(path)
    return path


def markup(runs, src_doc, pno, text_scale=1.0):
    out = []
    for r in runs:
        if 'img' in r:
            rect = r['img']
            p = emoji_png(rect, pno, src_doc)
            out.append(f'<img src="{p}" width="{rect.width*S:.1f}" height="{rect.height*S:.1f}" valign="middle"/>')
            continue
        t = esc(r['t']).replace('\n', '<br/>')
        if not t:
            continue
        if r.get('b'):
            t = f'<b>{t}</b>'
        if r.get('i'):
            t = f'<i>{t}</i>'
        out.append(t)
    return ''.join(out)


# ------------------------------------------------------------------ flowables
FIGS = []        # (pagina_novo, x, y_base, w, h, pagina_origem, rect)
PAGE_SRC = {}    # pagina_novo -> primeira página de origem
FRAME_Y = {}     # pagina_novo -> y (de baixo) logo abaixo do último flowable
GROUP_PAGES = {}  # id do grupo -> {'ini': pagina do cabeçalho, 'fim': pagina da última alternativa}
NO_RULE_PAGES = set()


class Tag(Flowable):
    def __init__(self, src):
        super().__init__(); self.src = src; self.width = self.height = 0
    def wrap(self, aw, ah): return (0, 0)
    def draw(self):
        PAGE_SRC.setdefault(self.canv.getPageNumber(), self.src)


class QHead(Flowable):
    def __init__(self, num):
        super().__init__()
        self.text = f'QUESTÃO {num}'
        self.fs = 11 * S
        self.h = self.fs * 1.35
        self.spaceBefore, self.spaceAfter = 16, 6
    def wrap(self, aw, ah):
        self.aw = aw
        return aw, self.h
    def draw(self):
        c = self.canv
        if hasattr(self, 'gid'):
            GROUP_PAGES.setdefault(self.gid, {})['ini'] = c.getPageNumber()
        base = 3.6
        c.setFillColor(INK); c.setFont('Car-B', self.fs)
        c.drawString(0, base, self.text)
        tw = pdfmetrics.stringWidth(self.text, 'Car-B', self.fs)
        x0 = tw + 9
        yc = base + 0.28 * self.fs
        bh = 3.4 * S
        c.setFillColor(CYAN); c.rect(x0, yc - bh / 2, self.aw - x0, bh, stroke=0, fill=1)
        c.setStrokeColor(INK); c.setLineWidth(1.0 * S)
        c.line(x0, yc + bh / 2, self.aw, yc + bh / 2)
        c.setLineWidth(0.42)
        x = x0 + 0.5
        while x < self.aw:
            c.line(x, yc - bh / 2, x, yc + bh / 2 - 0.6); x += 1.6 * S


class SecBox(Flowable):
    def __init__(self, lines):
        super().__init__()
        self.lines = lines; self.fs = 11 * S; self.lead = self.fs * 1.3
        self.pad_t, self.pad_b, self.pad_l = 5, 6, 9
        self.h = self.pad_t + self.pad_b + self.lead * len(lines)
        self.spaceBefore, self.spaceAfter = 6, 4
    def wrap(self, aw, ah):
        self.aw = aw; return aw, self.h
    def draw(self):
        c = self.canv
        c.setFillColor(LIGHT); c.rect(0, 0, self.aw, self.h, stroke=0, fill=1)
        c.setStrokeColor(CYAN); c.setLineWidth(3.6); c.line(1.8, 0, 1.8, self.h)
        c.setFillColor(INK); c.setFont('Car-B', self.fs)
        y = self.h - self.pad_t - self.fs * 0.95
        for ln in self.lines:
            c.drawString(self.pad_l, y, ln); y -= self.lead


class AltFlow(Flowable):
    """Alternativa com a letra em círculo preto (igual ao original)."""
    IND = 16.0
    def __init__(self, letter, para):
        super().__init__()
        self.letter, self.para = letter, para
        self.spaceBefore, self.spaceAfter = 2.2, 0
    def wrap(self, aw, ah):
        w, h = self.para.wrap(aw - self.IND, ah)
        self.h = h; self.aw = aw
        return aw, h
    def split(self, aw, ah): return []
    def draw(self):
        c = self.canv
        if hasattr(self, 'gid'):
            GROUP_PAGES.setdefault(self.gid, {})['fim'] = c.getPageNumber()
        self.para.drawOn(c, self.IND, 0)
        asc = getattr(self.para.blPara, 'ascent', 12)
        base = self.h - asc
        r = 5.7
        cy = base + 3.85
        c.setFillColor(INK); c.circle(r, cy, r, stroke=0, fill=1)
        c.setFillColor(white); c.setFont('Car-B', 8.0)
        c.drawCentredString(r, cy - 2.8, self.letter)


class Fig(Flowable):
    """Reserva o espaço da figura; o recorte vetorial original é colocado na segunda passada."""
    def __init__(self, src_page, rect, maxw, scale=S, clip=None):
        super().__init__()
        self.src_page, self.rect = src_page, rect
        s = min(scale, maxw / rect.width)
        self.scale_used = scale
        self.w, self.h = rect.width * s, rect.height * s
        self.hAlign = 'CENTER'
        self.spaceBefore, self.spaceAfter = 3, 2
    def wrap(self, aw, ah): return self.w, self.h
    def split(self, aw, ah): return []
    def draw(self):
        x, y = self.canv.absolutePosition(0, 0)
        if self.scale_used == 1.0:
            NO_RULE_PAGES.add(self.canv.getPageNumber())
        FIGS.append((self.canv.getPageNumber(), x, y, self.w, self.h, self.src_page, self.rect))


class Rule(Flowable):
    pass


class Doc(BaseDocTemplate):
    def afterFlowable(self, fl):
        if self.frame is not None:
            FRAME_Y[self.page] = self.frame._y


# ------------------------------------------------------------------ montagem dos itens
def para(runs, style, doc_src, pno, extra=None):
    st = ParagraphStyle('x', parent=style, **(extra or {}))
    return Paragraph(markup(runs, doc_src, pno), st)


def item_flowables(it, src, state):
    """Converte um item em flowables."""
    k = it['k']
    pno = it['src']
    if k == 'title':
        txt = '<br/>'.join(markup(l, src, pno) for l in it['lines'])
        return [Paragraph(txt, ST_TITLE)]
    if k == 'label':
        return [Paragraph(esc(it['text']), ST_LABEL)]
    if k == 'para':
        ex = {}
        if it.get('center'):
            ex.update(alignment=TA_CENTER)
        else:
            if it['indent']:
                ex['firstLineIndent'] = it['indent'] * S
            if it['left']:
                ex['leftIndent'] = it['left'] * S
            if not it['justify']:
                ex['alignment'] = TA_LEFT
        runs = it['runs']
        sp = 4 if state.get('prev') in ('qhead', 'label', 'title') else 0
        ex['spaceBefore'] = sp
        # recuo de primeira linha ou bloco antes de enunciado: mantém ritmo da prova
        return [Paragraph(markup(runs, src, pno), ParagraphStyle('p', parent=ST_BODY, **ex))]
    if k == 'verse':
        txt = '<br/>'.join(markup(l, src, pno) for l in it['lines'])
        return [Paragraph(txt, ParagraphStyle('v', parent=ST_LEFT, leftIndent=it['left'] * S, spaceBefore=2, spaceAfter=2))]
    if k == 'cap':
        al = {'right': TA_RIGHT, 'center': TA_CENTER, 'left': TA_LEFT}[it['align']]
        runs = [dict(r, size=8) if 't' in r else r for r in it['runs']]
        return [Paragraph(markup(runs, src, pno), ParagraphStyle('c', parent=ST_CAP, alignment=al))]
    if k == 'fig':
        return [Fig(it['page'], it['rect'], FRAME_W)]
    if k == 'alt':
        p = Paragraph(markup(it['runs'], src, pno), ParagraphStyle('a', parent=ST_ALT, spaceBefore=0))
        return [AltFlow(it['letter'], p)]
    raise ValueError(k)


def height_of(fls, w):
    tot = 0
    for f in fls:
        _, h = f.wrap(w, 100000)
        tot += h + (getattr(f, 'spaceBefore', None) if not hasattr(f, 'getSpaceBefore') else f.getSpaceBefore()) \
            + (getattr(f, 'spaceAfter', None) if not hasattr(f, 'getSpaceAfter') else f.getSpaceAfter())
    return tot


def build_story(items, src, instr_flowables):
    GROUP_LABEL.clear()
    """Agrupa os itens em unidades (questão / texto da redação) e aplica a regra 'não quebrar a questão'."""
    groups = []          # cada grupo: dict(fls=[], split=None)
    cur = None
    pending_sec = None
    in_red = False
    state = {'prev': None}

    def newgroup(src_page):
        g = dict(fls=[Tag(src_page)], split=None, alts=False, red=in_red)
        groups.append(g)
        return g

    for it in items:
        k = it['k']
        if k == 'sec':
            txt = it['lines']
            if 'REDAÇÃO' in ' '.join(txt):
                in_red = True
                cur = newgroup(it['src']); cur['newpage'] = True
                cur['fls'].append(Paragraph(esc(txt[0]), mk('h', fontName='Car-B', fontSize=13.2, leading=16,
                                                           alignment=TA_CENTER, spaceAfter=10)))
                state['prev'] = 'sec'
                continue
            if in_red:                          # fim da redação: insere instruções + rascunho
                in_red = False
                g = newgroup(it['src']); g['fls'] = instr_flowables(); g['red'] = True; g['free'] = True
            cur = newgroup(it['src'])
            cur['fls'].append(SecBox(txt))
            state['prev'] = 'sec'
            continue
        if k == 'qhead':
            if cur is not None and len(cur['fls']) == 2 and isinstance(cur['fls'][1], SecBox):
                pass                                    # mantém a caixa de seção com a 1ª questão
            else:
                cur = newgroup(it['src'])
            cur['fls'].append(QHead(it['num']))
            state['prev'] = 'qhead'
            continue
        if k == 'label' and in_red and cur is not None and any(isinstance(f, Paragraph) and f.style.name == 'label'
                                                              for f in cur['fls']):
            cur = newgroup(it['src'])
        if cur is None:
            cur = newgroup(it['src'])
        fls = item_flowables(it, src, state)
        if k == 'alt':
            cur['alts'] = True
        elif k == 'para' and not cur['alts']:
            cur['split'] = len(cur['fls'])          # candidato a início do enunciado
        cur['fls'].extend(fls)
        state['prev'] = k

    # converte grupos -> story
    story = []
    for gi, g in enumerate(groups):
        fls = g['fls']
        for f in fls:
            f.gid = gi
        GROUP_LABEL[gi] = next((f.text for f in fls if isinstance(f, QHead)), 'seção %d' % gi)
        if g.get('free'):                      # instruções da redação + rascunho (página própria)
            story.append(PageBreak()); story.extend(fls); story.append(PageBreak())
            continue
        if g.get('newpage'):
            story.append(PageBreak())
        total = height_of(fls, FRAME_W)
        if total <= FRAME_H - 2 or g['red']:
            story.append(CondPageBreak(min(total + 6, FRAME_H - 1)))
            story.extend(fls)
        else:
            sp = g['split'] or len(fls) // 2
            h1, h2 = height_of(fls[:sp], FRAME_W), height_of(fls[sp:], FRAME_W)
            story.append(CondPageBreak(min(h1 + 6, FRAME_H - 1)))
            story.extend(fls[:sp])
            story.append(CondPageBreak(min(h2 + 6, FRAME_H - 1)))
            story.extend(fls[sp:])
            qn = next((f.text for f in fls if isinstance(f, QHead)), '?')
            WARN.append((qn, round(total)))
    return story


def rascunho_rect(src, rp):
    """Retângulo da tabela de rascunho (união dos traços da tabela)."""
    rs = [d['rect'] for d in src[rp].get_drawings() if d['rect'].y0 >= 185 and d['rect'].y1 <= 744.5 and d['rect'].width > 8]
    if not rs:
        return pymupdf.Rect(24, 190, 541, 743)
    x0 = min(r.x0 for r in rs); y0 = min(r.y0 for r in rs)
    x1 = max(r.x1 for r in rs); y1 = max(r.y1 for r in rs)
    return pymupdf.Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1)


def instr_builder(src, rp):
    """Instruções da redação (página do rascunho) + folha de rascunho (sem ampliar)."""
    def build():
        lines = [e for e in load_page(src, rp) if isinstance(e, Ln)]
        lines.sort(key=lambda l: l.y0)
        heading = [l for l in lines if l.allbold and l.size > 10.5][0]
        body = [l for l in lines if l is not heading and l.size <= 10.5 and l.y0 < 190]
        fl = [Tag(rp),
              Paragraph('INSTRUÇÕES PARA A REDAÇÃO', mk('h2', fontName='Car-B', fontSize=13.2, leading=16,
                                                       alignment=TA_CENTER, spaceAfter=10))]
        items = []
        for l in body:
            t = l.text().replace('\u00a0', ' ')
            if re.match(r'^\s*(\u2003)*\d+(\.\d+)*\.\u2003', t):
                num, rest = re.match(r'^\s*(?:\u2003)*(\d+(?:\.\d+)*\.)\u2003\s*(.*)$', t, re.S).groups()
                items.append(dict(num=num, text=rest, bold=l.allbold, sub=('.' in num[:-1])))
            elif items:
                items[-1]['text'] += ' ' + t.strip()
        for it in items:
            tx = esc(' '.join(it['text'].split()))
            if it['bold']:
                tx = f'<b>{tx}</b>'
            ind = 18 * S if not it['sub'] else 42 * S
            lw = 22 if not it['sub'] else 30
            st = ParagraphStyle('ins', parent=ST_BODY, alignment=TA_LEFT, leftIndent=ind + lw, bulletIndent=ind,
                                spaceBefore=3)
            fl.append(Paragraph(tx, st, bulletText=it['num']))
        fl.append(PageBreak())
        fl.append(Tag(rp))
        fl.append(Fig(rp, rascunho_rect(src, rp), FRAME_W, scale=1.0))
        return fl
    return build


# ------------------------------------------------------------------ passada 1: reportlab
def pass1(src, items, path, rp):
    story = build_story(items, src, instr_builder(src, rp) if rp is not None else (lambda: []))
    doc = Doc(path, pagesize=(W, H), leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0)
    fr_even = Frame(X_EVEN, H - FRAME_BOT, FRAME_W, FRAME_H, 0, 0, 0, 0, id='e')
    fr_odd = Frame(X_ODD, H - FRAME_BOT, FRAME_W, FRAME_H, 0, 0, 0, 0, id='o')
    doc.addPageTemplates([PageTemplate(id='even', frames=[fr_even], autoNextPageTemplate='odd'),
                          PageTemplate(id='odd', frames=[fr_odd], autoNextPageTemplate='even')])
    doc.build(story)
    return doc.page


# ------------------------------------------------------------------ passada 2: PyMuPDF
def _footer_key(src, c):
    t = src[c].get_text('text', clip=pymupdf.Rect(0, 746, W, 765))
    return re.sub(r'[^A-Za-zÀ-ú]', '', t)[:14]


def pass2(src_path, content_path, out_path, npages, rp):
    O = 29.1742
    MBW, MBH = 625.276, 837.874
    src = pymupdf.open(src_path)
    srcm = pymupdf.open(src_path)                 # versão com bleed visível (coordenadas do MediaBox)
    for p in srcm:
        p.set_cropbox(p.mediabox)
    content = pymupdf.open(content_path)
    out = pymupdf.open()
    out.insert_pdf(src, from_page=0, to_page=0)   # capa intacta
    last_src = len(src) - 1
    fkeys = {c: _footer_key(src, c) for c in range(1, last_src)}
    font_b = pymupdf.Font(fontfile=FONT_DIR + 'Carlito-Bold.ttf')

    def src_for(n, first):
        """Página original cujo cabeçalho/rodapé usar: mesma paridade e mesma seção (rodapé)."""
        base = fkeys.get(first)
        order = [first] + [first + d for k in range(1, 8) for d in (k, -k)]
        for c in order:
            if 1 <= c < last_src and ((c + 1) % 2) == (n % 2) and fkeys.get(c) == base:
                return c
        return first

    for i in range(npages):
        n = i + 2
        pg = out.new_page(width=MBW, height=MBH)
        pg.show_pdf_page(pymupdf.Rect(O, O, O + W, O + H), content, i)
        for (pn, x, y, w, h, sp, rect) in FIGS:
            if pn != i + 1:
                continue
            top = H - (y + h)
            tgt = pymupdf.Rect(O + x, O + top, O + x + w, O + top + h)
            clip = pymupdf.Rect(rect.x0 + O, rect.y0 + O, rect.x1 + O, rect.y1 + O)
            pg.show_pdf_page(tgt, srcm, sp, clip=clip)
        ybot = H - FRAME_Y.get(i + 1, H - FRAME_TOP)
        if FRAME_BOT - ybot > 26 and (i + 1) not in NO_RULE_PAGES:
            xl = (X_EVEN if n % 2 == 0 else X_ODD) + O
            yy = O + ybot + 8
            shp = pg.new_shape()
            shp.draw_line((xl, yy), (xl + FRAME_W, yy)); shp.finish(color=(0.137, 0.122, 0.125), width=0.6)
            shp.draw_line((xl, yy + 2.6), (xl + FRAME_W, yy + 2.6)); shp.finish(color=(0.137, 0.122, 0.125), width=1.6)
            shp.commit()
        first = PAGE_SRC.get(i + 1, 1)
        hs = src_for(n, first)
        pg.show_pdf_page(pymupdf.Rect(O - 10, O - 10, O + W + 10, O + 75.5), srcm, hs,
                         clip=pymupdf.Rect(O - 10, O - 10, O + W + 10, O + 75.5))
        pg.show_pdf_page(pymupdf.Rect(O - 10, O + 744, O + W + 10, O + H + 10), srcm, hs,
                         clip=pymupdf.Rect(O - 10, O + 744, O + W + 10, O + H + 10))
        num_span = None
        for blk in src[hs].get_text('dict', clip=pymupdf.Rect(0, 746, W, 765))['blocks']:
            if blk['type'] == 0:
                for l in blk['lines']:
                    for sp in l['spans']:
                        if sp['text'].strip().isdigit() and sp['font'].endswith('Bold'):
                            num_span = sp
        if num_span:
            bx = pymupdf.Rect(num_span['bbox'])
            pg.draw_rect(pymupdf.Rect(O + bx.x0 - 1.5, O + bx.y0, O + bx.x1 + 1.5, O + bx.y1 + 0.5),
                         color=None, fill=(1, 1, 1))
            txt = str(n)
            tw = font_b.text_length(txt, 9)
            x = bx.x0 if n % 2 == 0 else bx.x1 - tw
            pg.insert_text((O + x, O + 757.7), txt, fontsize=9, fontname='carb',
                           fontfile=FONT_DIR + 'Carlito-Bold.ttf', color=(0.137, 0.122, 0.125))
        pg.set_cropbox(pymupdf.Rect(O, O, O + W, O + H))
    out.insert_pdf(src, from_page=last_src, to_page=last_src)     # contracapa intacta
    out.save(out_path, garbage=3, deflate=True)
    return len(out)


# ------------------------------------------------------------------ auditoria
def _norm(s):
    from collections import Counter
    return Counter(c for c in s.casefold() if not c.isspace() and c not in '-\u2003\u00ad')


def auditar(src_path, out_path):
    src, out = pymupdf.open(src_path), pymupdf.open(out_path)
    clip = pymupdf.Rect(0, 75, W, 744)
    o = ''.join(src[p].get_text('text', clip=clip) for p in range(1, len(src) - 1))
    n = ''.join(out[p].get_text('text', clip=clip) for p in range(1, len(out) - 1))
    co, cn = _norm(o), _norm(n)
    falt = {k: co[k] - cn[k] for k in co if co[k] > cn[k]}
    extra = {k: cn[k] - co[k] for k in cn if cn[k] > co[k]}
    return dict(orig=sum(co.values()), novo=sum(cn.values()), faltando=falt, a_mais=extra,
                ok=(not falt and not extra))


# ------------------------------------------------------------------ API
def _reset():
    global TMP
    TMP = tempfile.mkdtemp(prefix='ampl_')
    FIGS.clear(); PAGE_SRC.clear(); FRAME_Y.clear(); GROUP_PAGES.clear(); NO_RULE_PAGES.clear(); WARN.clear()
    GROUP_LABEL.clear()


def _figuras_limitadas(items):
    out = []
    for it in items:
        if it['k'] == 'fig':
            s = min(S, FRAME_W / it['rect'].width)
            if s < S - 0.001:
                out.append(dict(pagina_origem=it['page'] + 1, escala=round(s, 2)))
    return out


def _prepara(pdf_bytes, workdir):
    path = os.path.join(workdir, 'entrada.pdf')
    with open(path, 'wb') as f:
        f.write(pdf_bytes)
    src = pymupdf.open(path)
    rp = find_rascunho_page(src)
    pages = [p for p in range(1, len(src) - 1) if p != rp]
    items = build_items(src, pages)
    return path, src, rp, items


def analisar(pdf_bytes):
    """Etapa 1: lê a estrutura e informa; não gera o PDF."""
    with LOCK:
        _reset()
        wd = tempfile.mkdtemp(prefix='ampl_in_')
        path, src, rp, items = _prepara(pdf_bytes, wd)
        build_story(items, src, instr_builder(src, rp) if rp is not None else (lambda: []))
        qh = [i for i in items if i['k'] == 'qhead']
        return dict(
            paginas=len(src),
            questoes_blocos=len(qh),
            alternativas=sum(1 for i in items if i['k'] == 'alt'),
            figuras=sum(1 for i in items if i['k'] == 'fig'),
            figuras_limitadas=_figuras_limitadas(items),
            questoes_divididas=list(WARN),
            pagina_rascunho=None if rp is None else rp + 1,
            alternativas_esperadas=len(qh) * 5,
        )


def ampliar(pdf_bytes, progress=None):
    """Etapa 2: gera o PDF ampliado e a auditoria."""
    with LOCK:
        _reset()
        wd = tempfile.mkdtemp(prefix='ampl_run_')
        if progress: progress(0.1, 'Lendo a estrutura da prova...')
        path, src, rp, items = _prepara(pdf_bytes, wd)
        content = os.path.join(wd, 'conteudo.pdf')
        if progress: progress(0.35, 'Remontando as páginas em coluna única, 12 pt...')
        npages = pass1(src, items, content, rp)
        if progress: progress(0.7, 'Aplicando figuras, cabeçalho e rodapé originais...')
        out_path = os.path.join(wd, 'ampliado.pdf')
        total = pass2(path, content, out_path, npages, rp)
        if progress: progress(0.9, 'Conferindo o texto (auditoria)...')
        aud = auditar(path, out_path)
        quebradas = [GROUP_LABEL.get(g, str(g)) for g, v in GROUP_PAGES.items() if v.get('ini') != v.get('fim')]
        with open(out_path, 'rb') as f:
            data = f.read()
        if progress: progress(1.0, 'Pronto.')
        return dict(pdf=data, paginas_entrada=len(src), paginas_saida=total,
                    questoes=sum(1 for i in items if i['k'] == 'qhead'),
                    figuras_limitadas=_figuras_limitadas(items),
                    questoes_divididas=quebradas, auditoria=aud)
