# -*- coding: utf-8 -*-
"""
PSM-Inclusão / Ampliação — parte 1: leitura estrutural de um caderno ENEM em layout fechado.

Lê o texto VIVO do PDF (sem OCR), reconstrói a sequência de itens (cabeçalho de questão,
parágrafo, verso, legenda, figura, alternativa...) na ordem de leitura (coluna esquerda -> direita).
"""
import re, statistics
import pymupdf

BODY_TOP, BODY_BOT = 75.0, 744.0        # faixa útil da página (entre os filetes pontilhados)
BOX_R = 0.752                            # cor de preenchimento das caixas ciano de seção


class Ln:
    """Uma linha de texto do PDF original."""
    def __init__(s, **k):
        s.__dict__.update(k)
    def text(s):
        return ''.join(r.get('t', '') for r in s.parts)


def _style(font):
    f = font.lower()
    return ('bold' in f, 'italic' in f)


def load_page(doc, pno):
    """Retorna (elementos ordenados, info da página). Elementos = Ln | fig(dict)."""
    page = doc[pno]
    drs = page.get_drawings()
    rules = [dr['rect'].x0 for dr in drs if dr['rect'].width < 1 and dr['rect'].height > 300]
    gutter = rules[0] if rules else None
    boxes = [dr['rect'] for dr in drs if dr.get('fill') and abs(dr['fill'][0] - BOX_R) < 0.01
             and dr['rect'].height > 8 and dr['rect'].width > 100]
    raw = page.get_text('dict')

    imgs, lines = [], []
    for blk in raw['blocks']:
        if blk['type'] == 1:
            imgs.append(pymupdf.Rect(blk['bbox']))
            continue
        for l in blk['lines']:
            x0, y0, x1, y1 = l['bbox']
            if y0 < BODY_TOP or y0 >= BODY_BOT:
                continue
            spans = [s for s in l['spans'] if s['text'] != '']
            if not spans:
                continue
            if max(s['size'] for s in spans) > 30:      # marca d'água "RASCUNHO"
                continue
            lines.append((l, spans))

    big = [r for r in imgs if r.width > 24 or r.height > 24]
    small = [r for r in imgs if not (r.width > 24 or r.height > 24)]

    # 1) fragmentos de linha -> funde fragmentos na mesma linha de base (o PDF parte a linha em torno de emojis)
    frags = []
    for l, spans in lines:
        x0, y0, x1, y1 = l['bbox']
        parts = []
        for sp in spans:
            b, i = _style(sp['font'])
            circ = 'bundesbahn' in sp['font'].lower()
            parts.append(dict(x=sp['bbox'][0], t=sp['text'], b=b, i=i, size=sp['size'], circ=circ))
        wide = gutter is not None and x0 < gutter - 3 and x1 > gutter + 3
        if gutter is None or wide:
            grp = 0
        else:
            grp = 1 if (x0 + x1) / 2 < gutter else 2
        frags.append(dict(x0=x0, y0=y0, x1=x1, y1=y1, parts=parts, grp=grp, wide=wide))
    frags.sort(key=lambda f: (f['grp'], round(f['y0']), f['x0']))
    merged = []
    for f in frags:
        m = merged[-1] if merged else None
        if m and m['grp'] == f['grp'] and abs(f['y0'] - m['y0']) < 2.0 and 0 <= f['x0'] - m['x1'] < 40 \
                and f['x0'] > m['x0']:
            m['parts'] += f['parts']; m['x1'] = max(m['x1'], f['x1']); m['y1'] = max(m['y1'], f['y1'])
        else:
            merged.append(dict(f))

    out = []
    for f in merged:
        x0, y0, x1, y1, parts = f['x0'], f['y0'], f['x1'], f['y1'], f['parts']
        for r in small:                               # emojis em linha
            cy = (r.y0 + r.y1) / 2
            if y0 - 3 <= cy <= y1 + 3 and x0 - 2 <= r.x0 <= x1 + 2:
                parts.append(dict(x=r.x0, img=r))
        parts.sort(key=lambda p: p['x'])
        sizes = [(len(p['t']), p['size']) for p in parts if 't' in p and p['t'].strip()]
        size = max(sizes)[1] if sizes else 10.0
        ln = Ln(page=pno, x0=x0, y0=y0, x1=x1, y1=y1, parts=parts, size=size, grp=f['grp'], wide=f['wide'], gutter=gutter)
        txts = [p for p in parts if 't' in p and p['t'].strip()]
        ln.allbold = bool(txts) and all(p['b'] for p in txts)
        ln.boxed = any(bx.x0 - 2 <= x0 and x1 <= bx.x1 + 2 and bx.y0 - 2 <= y0 and y1 <= bx.y1 + 2 for bx in boxes)
        out.append(ln)

    for r in big:
        grp = 0 if gutter is None else (1 if (r.x0 + r.x1) / 2 < gutter else 2)
        out.append(dict(fig=True, page=pno, rect=r, grp=grp, y0=r.y0, x0=r.x0, x1=r.x1, y1=r.y1))

    def key(e):
        g = e.grp if isinstance(e, Ln) else e['grp']
        y = e.y0 if isinstance(e, Ln) else e['y0']
        return (g, y)
    out.sort(key=key)
    return out


def classify(ln, left, right):
    """Define o tipo da linha."""
    t = ln.text().strip()
    p0 = ln.parts[0]
    if ln.allbold and 10.5 <= ln.size <= 11.5:
        if re.match(r'^QUEST[ãÃ]O\s+\d+', t):
            return 'qhead'
        return 'sec'
    if p0.get('circ') and len(p0['t'].strip()) == 1 and p0['t'].strip() in 'ABCDE':
        return 'alt'
    if ln.size <= 9.3 and not ln.allbold:
        return 'cap'
    if ln.allbold and re.match(r'^TEXTO\s+[IVX]+\s*$', t):
        return 'label'
    if ln.allbold and ln.size >= 9.5:
        mid = (ln.x0 + ln.x1) / 2
        if abs(mid - (left + right) / 2) < 7:
            return 'title'
    return 'prose'


def fix_caps(s):
    """O PDF mapeia 'Ã' maiúsculo como 'ã' nos títulos em caixa alta."""
    letters = [c for c in s if c.isalpha()]
    if letters and all(c.isupper() or c == 'ã' for c in letters):
        return s.replace('ã', 'Ã')
    return s


def runs_of(ln, drop_first_letter=False):
    """Converte as partes de uma linha em runs [(texto,b,i,size)] ou imagem."""
    runs = []
    first = True
    for p in ln.parts:
        if 'img' in p:
            runs.append(dict(img=p['img']))
            continue
        t = p['t']
        if drop_first_letter and first:
            t = t.strip()[1:] if t.strip() else ''
            first = False
        runs.append(dict(t=t, b=p['b'], i=p['i'], size=p['size']))
    return runs


def build_items(doc, pages):
    """Percorre as páginas e devolve a lista linear de itens."""
    elements = []     # (Ln|fig, left, right)
    for pno in pages:
        els = load_page(doc, pno)
        # limites de cada grupo (coluna)
        grp_lines = {}
        for e in els:
            if isinstance(e, Ln) and e.size > 9.3 and not e.boxed:
                grp_lines.setdefault(e.grp, []).append(e)
        lr = {}
        for g in (0, 1, 2):
            ls = [e for e in els if isinstance(e, Ln) and e.grp == g and e.size > 9.3]
            if not ls:
                ls = [e for e in els if isinstance(e, Ln) and e.grp == g]
            if ls:
                left = min(e.x0 for e in ls if not e.boxed) if any(not e.boxed for e in ls) else min(e.x0 for e in ls)
                right = max(e.x1 for e in ls)
                lr[g] = (left, right)
        # grupo 0 em página de duas colunas = linhas largas: usa extensão da página
        for e in els:
            g = e.grp if isinstance(e, Ln) else e['grp']
            left, right = lr.get(g, (31, 548))
            elements.append((e, left, right))

    items = []
    buf = None   # dict(kind, lines)

    def flush():
        nonlocal buf
        if not buf:
            return
        k, ls = buf['kind'], buf['lines']
        if k == 'prose':
            items.extend(prose_items(ls))
        elif k == 'cap':
            items.extend(caption_items(ls))
        elif k == 'title':
            items.append(dict(k='title', lines=[runs_of(l) for l, _, _ in ls], src=ls[0][0].page))
        elif k == 'label':
            items.append(dict(k='label', text=ls[0][0].text().strip(), src=ls[0][0].page))
        elif k == 'sec':
            items.append(dict(k='sec', lines=[fix_caps(l.text().strip()) for l, _, _ in ls],
                              boxed=ls[0][0].boxed, wide=ls[0][0].wide, src=ls[0][0].page,
                              center=abs((ls[0][0].x0 + ls[0][0].x1) / 2 - (ls[0][1] + ls[0][2]) / 2) < 8))
        elif k == 'alt':
            l0 = ls[0][0]
            letter = l0.parts[0]['t'].strip()[0]
            runs = runs_of(l0, drop_first_letter=True)
            for l, _, _ in ls[1:]:
                runs.append(dict(t=' ', b=False, i=False, size=10))
                runs.extend(runs_of(l))
            items.append(dict(k='alt', letter=letter, runs=runs, src=l0.page))
        buf = None

    for e, left, right in elements:
        if not isinstance(e, Ln):
            flush()
            items.append(dict(k='fig', page=e['page'], rect=e['rect'], src=e['page']))
            continue
        kind = classify(e, left, right)
        # continuação de alternativa (recuo pendurado)
        if buf and buf['kind'] == 'alt' and kind in ('prose', 'cap') and e.x0 - left > 8 and kind == 'prose':
            buf['lines'].append((e, left, right))
            continue
        if kind == 'qhead':
            flush()
            m = re.search(r'(\d+)', e.text())
            items.append(dict(k='qhead', num=m.group(1), src=e.page))
            continue
        if kind == 'alt':
            flush()
            buf = dict(kind='alt', lines=[(e, left, right)])
            continue
        if buf and buf['kind'] == kind and kind in ('prose', 'cap', 'title', 'sec'):
            if kind == 'sec' and buf['lines'][-1][0].boxed != e.boxed:
                flush(); buf = dict(kind=kind, lines=[(e, left, right)])
            else:
                buf['lines'].append((e, left, right))
            continue
        flush()
        buf = dict(kind=kind, lines=[(e, left, right)])
    flush()
    return items


# ---------------------------------------------------------------- parágrafos / versos
def _join_runs(lines):
    """Junta as linhas de um parágrafo num fluxo contínuo de runs."""
    runs = []
    for idx, ln in enumerate(lines):
        rs = runs_of(ln)
        if idx > 0 and runs:
            last = next((r for r in reversed(runs) if 't' in r), None)
            if last is not None and last['t'].rstrip().endswith('-') and not last['t'].rstrip().endswith(' -'):
                last['t'] = last['t'].rstrip()          # hífen no fim da linha: cola sem espaço
            else:
                runs.append(dict(t=' ', b=False, i=False, size=10))
        runs.extend(rs)
    return runs


def prose_items(ls):
    """Segmenta linhas de prosa em parágrafos / blocos de verso."""
    lines = [l for l, _, _ in ls]
    lefts = {id(l): lf for l, lf, _ in ls}
    rights = {id(l): rt for l, _, rt in ls}
    # passo de linha típico
    diffs = [b.y0 - a.y0 for a, b in zip(lines, lines[1:]) if a.grp == b.grp and a.page == b.page and 0 < b.y0 - a.y0 < 20]
    pitch = statistics.median(diffs) if diffs else 12.5

    segs, cur = [], [lines[0]]
    for a, b in zip(lines, lines[1:]):
        left, right = lefts[id(b)], rights[id(b)]
        same = a.grp == b.grp and a.page == b.page
        new = False
        if same:
            if b.y0 - a.y0 > pitch * 1.3:
                new = True
            elif b.x0 > a.x0 + 8:
                new = True
            elif a.allbold != b.allbold:
                new = True
        else:
            full_a = a.x1 >= rights[id(a)] - 6
            if not full_a or b.x0 - left > 8:
                new = True
        if new:
            segs.append(cur); cur = [b]
        else:
            cur.append(b)
    segs.append(cur)

    out = []
    for seg in segs:
        left = lefts[id(seg[0])]; right = rights[id(seg[0])]
        n = len(seg)
        short = [i for i in range(n - 1) if seg[i].x1 < rights[id(seg[i])] - 8]
        if n >= 2 and len(short) >= 0.5 * (n - 1):
            out.append(dict(k='verse', lines=[runs_of(l) for l in seg], left=min(l.x0 for l in seg) - left, src=seg[0].page))
            continue
        # prosa: quebra interna após linha curta seguida de linha não recuada
        parts, cur = [], [seg[0]]
        for i in range(1, n):
            if (i - 1) in short:
                parts.append(cur); cur = [seg[i]]
            else:
                cur.append(seg[i])
        parts.append(cur)
        for pt in parts:
            first_indent = 0.0
            rest_left = min(l.x0 for l in pt[1:]) if len(pt) > 1 else pt[0].x0
            if len(pt) > 1 and pt[0].x0 > rest_left + 8:
                first_indent = pt[0].x0 - rest_left
            lft = rest_left - left if len(pt) > 1 else (pt[0].x0 - left if first_indent == 0 else 0)
            if len(pt) == 1 and pt[0].x0 - left > 8:
                first_indent, lft = pt[0].x0 - left, 0.0
            if lft < 8:
                lft = 0.0
            full = sum(1 for l in pt[:-1] if l.x1 >= rights[id(l)] - 6)
            justify = len(pt) >= 2 and full >= 0.6 * (len(pt) - 1)
            center = (len(pt) == 1 and abs((pt[0].x0 + pt[0].x1) / 2 - (left + right) / 2) < 7
                      and (pt[0].x1 - pt[0].x0) < 0.9 * (right - left) and pt[0].x0 - left > 8)
            out.append(dict(k='para', runs=_join_runs(pt), indent=first_indent, left=lft, justify=justify, src=pt[0].page,
                            nlines=len(pt), center=center))
    return out


def caption_items(ls):
    """Legendas (fonte): agrupa por alinhamento."""
    out, grp, align = [], [], None
    for l, left, right in ls:
        mid = (l.x0 + l.x1) / 2
        if l.x1 >= right - 5:
            al = 'right'
        elif abs(mid - (left + right) / 2) < 6:
            al = 'center'
        else:
            al = 'left'
        if al != align and grp:
            out.append((align, grp)); grp = []
        align = al
        grp.append(l)
    if grp:
        out.append((align, grp))
    items = []
    for al, g in out:
        runs = []
        for idx, l in enumerate(g):
            rs = runs_of(l)
            if idx > 0:
                brk = l.text().strip().startswith('Acesso em') or al == 'center'
                runs.append(dict(t='\n' if brk else ' ', b=False, i=False, size=8))
            runs.extend(rs)
        items.append(dict(k='cap', runs=runs, align=al, src=g[0].page))
    return items



def find_rascunho_page(doc):
    """Índice (0-based) da página de instruções + rascunho da redação (marca d'água grande), ou None."""
    for pno in range(1, len(doc) - 1):
        for blk in doc[pno].get_text('dict')['blocks']:
            if blk['type'] == 0:
                for l in blk['lines']:
                    for sp in l['spans']:
                        if sp['size'] > 30 and 'RASCUNHO' in sp['text'].upper():
                            return pno
    return None
