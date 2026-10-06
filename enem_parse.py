# -*- coding: utf-8 -*-
"""
PSM-Inclusão / Ampliação — parte 1: leitura estrutural de um caderno ENEM em layout fechado.

Lê o texto VIVO do PDF (sem OCR), reconstrói a sequência de itens (cabeçalho de questão,
parágrafo, verso, legenda, figura, alternativa...) na ordem de leitura (coluna esquerda -> direita).
"""
import os, re, statistics
import pymupdf

_HERE = os.path.dirname(os.path.abspath(__file__))
_FD = _HERE + os.sep if os.path.exists(os.path.join(_HERE, 'Carlito-Regular.ttf')) else '/usr/share/fonts/truetype/crosextra/'
_METRIC = {}


def _metric(bold, italic):
    k = (bold, italic)
    if k not in _METRIC:
        fn = {(False, False): 'Carlito-Regular.ttf', (True, False): 'Carlito-Bold.ttf',
              (False, True): 'Carlito-Italic.ttf', (True, True): 'Carlito-BoldItalic.ttf'}[k]
        _METRIC[k] = pymupdf.Font(fontfile=_FD + fn)
    return _METRIC[k]


def span_text(sp):
    """Texto do span (rawdict) já corrigido: o InDesign guarda 'Caixa Alta'/'Capitalizar' como minúsculas
    no texto e só muda o desenho; aqui comparamos a largura de cada glifo com a de maiúscula/minúscula."""
    chars = sp['chars']
    raw = ''.join(c['c'] for c in chars)
    f = sp['font'].lower()
    if any(k in f for k in ('symbol', 'euclid', 'bundesbahn', 'times')) or not any(c.isalpha() for c in raw):
        return raw
    font = _metric('bold' in f, 'italic' in f)
    out, certain = [], []
    for c in chars:
        ch = c['c']; w = c['bbox'][2] - c['bbox'][0]
        up = False
        if ch.isalpha() and ch.lower() != ch.upper():
            wl, wu = font.text_length(ch.lower(), sp['size']), font.text_length(ch.upper(), sp['size'])
            if abs(wu - wl) > 0.04 * sp['size']:
                up = abs(w - wu) < abs(w - wl)
                certain.append(True)
                ch = ch.upper() if up else ch.lower()
            else:
                certain.append(False)
        else:
            certain.append(False)
        out.append(ch)
    # palavra com 2+ maiúsculas certas: as letras ambíguas (e/E, o/O...) também são maiúsculas
    res = list(out)
    i = 0
    while i < len(res):
        if res[i].isalpha():
            j = i
            while j < len(res) and res[j].isalpha():
                j += 1
            ups = sum(1 for k in range(i, j) if certain[k] and res[k].isupper())
            if ups >= 2:
                for k in range(i, j):
                    if not certain[k]:
                        res[k] = res[k].upper()
            i = j
        else:
            i += 1
    return ''.join(res)

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


KEEP = {}      # (pág, rect arredondado) -> caixas do texto que pertence ao bloco vetorial
EXCL = []      # (página, retângulo) de trechos que viram imagem dentro do parágrafo
SYM_MAP = {'p': 'π', '\u22c5': '\u00b7'}          # SymbolMT: 'p' = pi ; ponto-médio matemático -> U+00B7
SAFE_SYMBOL = set('+<=>×σΩ→−≤≥⋅°·.,;:()[]%/ ' + '0123456789')


def _is_decor(r, width_limit=548.5, dr=None):
    """Desenho que faz parte da diagramação (filetes, barra da questão, caixa de seção, abas laterais)."""
    if r.x0 < -1 or r.y0 < -1 or r.x1 > 569.5 or r.y1 > 782 or r.width > 560 or r.height > 700:
        return True                                   # moldura/sangria/marcas de corte
    if r.x1 < 21 or r.x0 > width_limit:
        return True                                   # abas coloridas nas margens
    if r.width < 1.5 and r.height > 300:
        return True                                   # fio de separação das colunas
    fill = dr.get('fill') if dr else None
    col = dr.get('color') if dr else None
    if dr is None and r.width > 150 and 18 < r.height < 60 and r.x0 < 60:
        return True
    if fill and r.width > 150 and 10 < r.height < 60 and ((fill[2] > 0.97 and fill[0] > 0.7) or abs(fill[0] - BOX_R) < 0.01):
        return True                                   # caixa de seção (qualquer coluna)
    if col and r.width < 5 and 10 < r.height < 60 and col[2] > 0.9 and (dr.get('width') or 0) >= 2.5:
        return True                                   # barra lateral da caixa de seção
    if r.y1 < BODY_TOP + 1 or r.y0 > BODY_BOT - 1:
        return True
    return False


def _clusters(rects, gap=2.0, with_members=False):
    """Une retângulos que se tocam (distância <= gap)."""
    def _fat(r):                                      # linhas têm largura/altura 0: o Rect do PyMuPDF as ignora na união
        return pymupdf.Rect(r.x0 - 0.3, r.y0 - 0.3, r.x1 + 0.3, r.y1 + 0.3)
    cl = [(_fat(r), [_fat(r)]) for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        for r, ms in cl:
            for o in out:
                if not (r.x1 + gap < o[0].x0 or o[0].x1 + gap < r.x0 or r.y1 + gap < o[0].y0 or o[0].y1 + gap < r.y0):
                    o[0] |= r
                    o[1].extend(ms)
                    changed = True
                    break
            else:
                out.append([pymupdf.Rect(r), list(ms)])
        cl = out
    return cl if with_members else [c[0] for c in cl]


def _inside(r, bg, tol=3.0):
    return bg.x0 - tol <= r.x0 and r.x1 <= bg.x1 + tol and bg.y0 - tol <= r.y0 and r.y1 <= bg.y1 + tol


def _vov(a, b):
    return min(a[3], b[3]) - max(a[1], b[1])


def _hov(a, b):
    return min(a[2], b[2]) - max(a[0], b[0])


def _is_prose(ln):
    t = ln.text().strip()
    words = re.findall(r'[A-Za-zÀ-ÿ]{4,}', t)
    return ln.size >= 9.0 and len(t) >= 25 and len(words) >= 3 and not any(p.get('circ') for p in ln.parts[:1])


def load_page(doc, pno):
    """Retorna elementos ordenados: Ln | fig(dict). Blocos complexos (tabelas, gráficos, fórmulas, frações,
    raízes) viram 'fig' vetorial; linhas de parágrafo que os contêm ficam marcadas (taint)."""
    page = doc[pno]
    drs = page.get_drawings()
    rules = [dr['rect'].x0 for dr in drs if dr['rect'].width < 1 and dr['rect'].height > 300]
    gutter = rules[0] if rules else None
    boxes = [dr['rect'] for dr in drs if dr.get('fill') and dr['rect'].height > 8 and dr['rect'].width > 100
             and ((abs(dr['fill'][0] - BOX_R) < 0.01) or (dr['fill'][2] > 0.97 and dr['fill'][0] > 0.85 and dr['rect'].width > 150))]
    raw = page.get_text('rawdict')

    imgs, lines = [], []
    for blk in raw['blocks']:
        if blk['type'] == 1:
            imgs.append(pymupdf.Rect(blk['bbox']))
            continue
        for l in blk['lines']:
            x0, y0, x1, y1 = l['bbox']
            if y0 < BODY_TOP or y0 >= BODY_BOT:
                continue
            spans = [s for s in l['spans'] if s['chars']]
            if not spans:
                continue
            if max(s['size'] for s in spans) > 30:      # marca d'água "RASCUNHO"
                continue
            lines.append((l, spans))

    big = [r for r in imgs if r.width > 24 or r.height > 24]
    small = [r for r in imgs if not (r.width > 24 or r.height > 24)]

    # 1) fragmentos de linha
    frags = []
    for l, spans in lines:
        x0, y0, x1, y1 = l['bbox']
        parts = []
        weird = False
        rotated = abs(l['dir'][0]) < 0.9
        for sp in spans:
            b, i = _style(sp['font'])
            fl = sp['font'].lower()
            circ = 'bundesbahn' in fl
            txt = span_text(sp)
            if 'symbol' in fl:
                txt = ''.join(SYM_MAP.get(c, c) for c in txt)
                if any((ord(c) < 32) or (0xE000 <= ord(c) <= 0xF8FF) or (c.isascii() and c.isalpha()) for c in txt):
                    weird = True
            elif 'euclid' in fl:
                weird = True
            parts.append(dict(x=sp['bbox'][0], x1=sp['bbox'][2], t=txt, b=b, i=i, size=sp['size'], circ=circ, oy=sp['origin'][1],
                              ch=([(c['c'], c['bbox'][0], c['bbox'][2]) for c in sp['chars']] if len(txt) == len(sp['chars']) else None)))
        wide = gutter is not None and x0 < gutter - 3 and x1 > gutter + 3
        if gutter is None or wide:
            grp = 0
        else:
            grp = 1 if (x0 + x1) / 2 < gutter else 2
        frags.append(dict(x0=x0, y0=y0, x1=x1, y1=y1, parts=parts, grp=grp, wide=wide, weird=weird, rot=rotated))
    def _base(f):
        ss = [p['size'] for p in f['parts'] if p.get('t', '').strip()]
        mx = max(ss) if ss else 10.0
        main = [p for p in f['parts'] if p.get('t', '').strip() and p['size'] >= mx - 0.8]
        if not main:
            return f['y1']
        return max(main, key=lambda p: len(p['t'].strip()))['oy']      # a linha de base é a do trecho mais longo
    for f in frags:
        f['base'] = _base(f)
    # funde fragmentos que estão na mesma linha de base (o PDF parte a linha em torno de emojis/sup/sub)
    frags.sort(key=lambda f: (f['grp'], f['base'], f['x0']))
    buckets = []
    for f in frags:
        bk = buckets[-1] if buckets else None
        if bk and bk[0]['grp'] == f['grp'] and not f['rot'] and not bk[0]['rot'] and abs(f['base'] - bk[0]['base']) < 1.2:
            bk.append(f)
        else:
            buckets.append([f])
    merged = []
    for bk in buckets:
        bk.sort(key=lambda f: f['x0'])
        m = None
        for f in bk:
            if m is not None and -12 <= f['x0'] - m['x1'] < 40 and f['x0'] > m['x0']:
                m['parts'] += f['parts']; m['x1'] = max(m['x1'], f['x1']); m['y0'] = min(m['y0'], f['y0'])
                m['y1'] = max(m['y1'], f['y1']); m['weird'] = m['weird'] or f['weird']
            else:
                m = dict(f); merged.append(m)
    merged.sort(key=lambda f: (f['grp'], round(f['y0']), f['x0']))

    # 2) subscritos/sobrescritos soltos (linhas pequenas) são anexados à linha-base
    def base_size(f):
        ss = [p['size'] for p in f['parts'] if p.get('t', '').strip()]
        return max(ss) if ss else 10.0
    smalls = [f for f in merged if base_size(f) <= 7.6 and not f['rot']]
    keep = [f for f in merged if f not in smalls]
    for sm in smalls:
        cx = (sm['x0'] + sm['x1']) / 2
        best, bd = None, 99
        for b in keep:
            if b['grp'] != sm['grp'] and not (b['wide'] or sm['wide']) or b['rot'] or base_size(b) < 8:
                continue
            if b['x0'] - 1.5 <= cx <= b['x1'] + 1.5:
                dy = (sm['y0'] + sm['y1']) / 2 - (b['y0'] + b['y1']) / 2
                if abs(dy) < 9 and abs(dy) < bd:
                    best, bd = b, abs(dy)
        if best is not None:
            best['parts'] += sm['parts']; best['x1'] = max(best['x1'], sm['x1'])
            best['y0'] = min(best['y0'], sm['y0']); best['y1'] = max(best['y1'], sm['y1'])
            best['weird'] = best['weird'] or sm['weird']
        else:
            keep.append(sm)
    merged = keep

    # 2b) imagens pequenas na mesma linha de texto (ex.: hieróglifos ao lado da letra da alternativa) entram na linha
    still_big = []
    for r in big:
        host, bd = None, 99
        if r.height <= 42 and r.width <= 170:
            cyr = (r.y0 + r.y1) / 2
            for f in merged:
                if f['rot']:
                    continue
                if f['y0'] - 1 <= cyr <= f['y1'] + 1 or (r.y0 <= (f['y0'] + f['y1']) / 2 <= r.y1):
                    gap = r.x0 - f['x1']
                    inside = f['x0'] <= r.x0 <= f['x1']
                    if (-5 <= gap < 30 or inside):
                        dy = abs(cyr - (f['y0'] + f['y1']) / 2)
                        if dy < bd:
                            host, bd = f, dy
        if host is not None:
            host['parts'].append(dict(x=r.x0, x1=r.x1, img=r, math=True))
            host['x1'] = max(host['x1'], r.x1)
        else:
            still_big.append(r)
    big = still_big

    # 3) blocos complexos: clusters de desenho + linhas "estranhas" + linhas rotacionadas
    cl = _clusters([dr['rect'] for dr in drs if not _is_decor(dr['rect'], dr=dr) and (dr['rect'].width > 0 or dr['rect'].height > 0)],
                   gap=1.5, with_members=True)
    anchors = []
    for rect, ms in cl:
        if max(rect.width, rect.height) < 3.0 or (max(rect.width, rect.height) < 8 and min(rect.width, rect.height) >= 3):
            continue                                   # anéis de grau, pontos etc. (barras de fração finas ficam)
        if rect.width > 150 and all(m.height < 6 for m in ms):
            continue                                   # só filetes (barra da questão, linha de fim de bloco)
        if any(_inside(rect, bg) for bg in big):
            continue                                   # desenho sobre uma figura raster: o recorte da figura já o leva
        anchors.append(pymupdf.Rect(rect))
    for f in merged:
        if f['weird'] or f['rot']:
            anchors.append(pymupdf.Rect(f['x0'], f['y0'], f['x1'], f['y1']))
    anchors = _clusters(anchors, gap=1.0)

    cache = {}

    def get_ln(k):
        if k not in cache:
            cache[k] = _make_ln(merged[k], pno, gutter, boxes, small)
        return cache[k]

    consumed = set()          # linhas que saem do fluxo (viram parte de uma imagem)
    in_region = set()
    block_figs = []

    def prose_f(f):
        t = ''.join(p.get('t', '') for p in f['parts']).strip()
        first = min(f['parts'], key=lambda p: p['x']) if f['parts'] else {}
        return (base_size(f) >= 9.0 and len(t) >= 25 and len(re.findall(r'[A-Za-zÀ-ÿ]{4,}', t)) >= 3
                and not first.get('circ'))

    def is_alt_letter(f):
        ts = ''.join(p.get('t', '') for p in f['parts']).strip()
        first = min(f['parts'], key=lambda p: p['x']) if f['parts'] else {}
        return bool(first.get('circ')) and len(ts) <= 10

    bars = [pymupdf.Rect(dr['rect'].x0, dr['rect'].y0 - 0.3, dr['rect'].x1, dr['rect'].y1 + 0.3) for dr in drs
            if not _is_decor(dr['rect'], dr=dr) and dr['rect'].height < 2 and 5 <= dr['rect'].width < 150]
    tbox = [tight_box(f) for f in merged]
    claimed = []
    anchors.sort(key=lambda r: -(r.width * r.height))      # blocos grandes primeiro

    for a0 in anchors:
        if any(c.x0 - 1 <= a0.x0 and a0.x1 <= c.x1 + 1 and c.y0 - 1 <= a0.y0 and a0.y1 <= c.y1 + 1 for c in claimed):
            continue                                          # já faz parte de um bloco maior
        T = pymupdf.Rect(a0)                                  # retângulo justo do bloco
        R = pymupdf.Rect(a0)                                  # área de busca de membros
        a_bars = [bb for bb in bars if not (bb.x1 < a0.x0 - 1 or bb.x0 > a0.x1 + 1 or bb.y1 < a0.y0 - 1 or bb.y0 > a0.y1 + 1)]
        thin = bool(a_bars) and a0.height < 40                 # âncora com barra(s) de fração
        seed_rem = pymupdf.Rect(a0)
        for bb in (a_bars if thin else []):
            vv = 13 if bb.width >= 20 else 8                   # frações altas de alternativas precisam de mais folga
            R |= pymupdf.Rect(bb.x0 - 1, bb.y0 - vv, bb.x1 + 1, bb.y1 + vv)
            seed_rem |= pymupdf.Rect(bb.x0 - 1, bb.y0 - 8, bb.x1 + 1, bb.y1 + 8)
        chart_like = a0.width > 60 and a0.height > 30
        members, hosts = [], []
        changed = True
        while changed:
            changed = False
            for k, f in enumerate(merged):
                if k in in_region or k in members or k in hosts:
                    continue
                fb = tuple(tbox[k])
                rb = (R.x0, R.y0, R.x1, R.y1)
                if _vov(fb, rb) >= (1.5 if chart_like else 3.0) and _hov(fb, rb) > 0:
                    if prose_f(f) and chart_like and not (T.x0 - 3 <= f['x0'] and f['x1'] <= T.x1 + 3):
                        continue                             # parágrafo largo encostado no gráfico: não é do gráfico
                    if not chart_like and prose_f(f):
                        hosts.append(k)                      # parágrafo que contém a fração/raiz
                    else:
                        members.append(k); R |= pymupdf.Rect(fb); T |= tight_box(f); changed = True
                elif is_alt_letter(f) and _vov(fb, (T.x0, T.y0, T.x1, T.y1)) >= 3.5 and 0 <= T.x0 - f['x1'] < 45:
                    members.append(k); R |= pymupdf.Rect(fb); T |= tight_box(f); changed = True   # letra da alternativa
                elif chart_like and not f['rot'] and base_size(f) < 9.4 and not _is_cap_like(f) and _dist(fb, rb) <= 10 \
                        and len(''.join(p.get('t', '') for p in f['parts']).strip()) < 40:
                    members.append(k); R |= pymupdf.Rect(fb); T |= tight_box(f); changed = True      # rótulos de eixo / legenda
        R = T
        in_region.update(members)
        in_region.update(hosts)
        if hosts:
            hk = max(hosts, key=lambda k: _vov(tuple(tbox[k]), (T.x0, T.y0, T.x1, T.y1)))
            rem = pymupdf.Rect(T)
            if thin:
                rem |= seed_rem
            rem = pymupdf.Rect(rem.x0 - 0.6, rem.y0, rem.x1 + 0.6, rem.y1)
            R = pymupdf.Rect(T)
            # caracteres de TODAS as linhas do parágrafo que ficam dentro da fração/raiz entram na imagem (2D)
            for hk2 in hosts:
                host2 = merged[hk2]
                keep_parts = []
                for p in host2['parts']:
                    if 't' not in p or not p['t'].strip() or 'oy' not in p:
                        keep_parts.append(p); continue
                    chs = p.get('ch') or [(p['t'], p['x'], p.get('x1', p['x'] + 4))]
                    tb = tight_box(dict(parts=[p], x0=p['x'], x1=p.get('x1', p['x'] + 4), y0=host2['y0'], y1=host2['y1']))
                    cy = (tb.y0 + tb.y1) / 2
                    inside = [rem.x0 <= (c0 + c1) / 2 <= rem.x1 and rem.y0 <= cy <= rem.y1 for _, c0, c1 in chs]
                    if not any(inside):
                        keep_parts.append(p); continue
                    run, run_in = [], None
                    def flush_run():
                        if run and not run_in:
                            q = dict(p); q['t'] = ''.join(c for c, _, _ in run); q['x'] = run[0][1]; q['x1'] = run[-1][2]
                            q['ch'] = list(run); keep_parts.append(q)
                    for (c, c0, c1), ins in zip(chs, inside):
                        if ins:
                            R |= pymupdf.Rect(c0, tb.y0, c1, tb.y1)
                        if run_in is None or ins != run_in:
                            flush_run(); run = []; run_in = ins
                        run.append((c, c0, c1))
                    flush_run()
                host2['parts'] = keep_parts
            host = merged[hk]
            R = pymupdf.Rect(R.x0 - 0.5, R.y0 - 0.4, R.x1 + 0.5, R.y1 + 0.4)
            host['parts'].append(dict(x=R.x0, x1=R.x1, img=R, math=True))
            EXCL.append((pno, pymupdf.Rect(R)))
            for k in members:
                consumed.add(k)
            for k in hosts:
                if k != hk:
                    pass
        else:
            lns = [get_ln(k) for k in members]
            rect = pymupdf.Rect(R)
            rect = pymupdf.Rect(max(rect.x0 - 0.7, 0), max(rect.y0 - 0.5, BODY_TOP), rect.x1 + 0.7, min(rect.y1 + 0.5, BODY_BOT))
            grp = 0 if gutter is None else (1 if (rect.x0 + rect.x1) / 2 < gutter else 2)
            if gutter is not None and rect.x0 < gutter - 3 and rect.x1 > gutter + 3:
                grp = 0
            alt = next((ln for ln in lns if ln.parts and ln.parts[0].get('circ')), None)
            letter = alt.parts[0]['t'].strip()[:1] if alt else None
            for k in members:
                consumed.add(k)
            claimed.append(pymupdf.Rect(rect))
            KEEP[(pno, tuple(round(v, 1) for v in rect))] = [pymupdf.Rect(tbox[k]) for k in members]
            block_figs.append(dict(fig=True, vector=True, page=pno, rect=rect, grp=grp, y0=rect.y0, x0=rect.x0,
                                   x1=rect.x1, y1=rect.y1, alt=letter,
                                   tipo=('alternativa' if letter else 'tabela/gráfico/fórmula')))

    out = [get_ln(k) for k in range(len(merged)) if k not in consumed]
    # figuras raster
    for r in big:
        grp = 0 if gutter is None else (1 if (r.x0 + r.x1) / 2 < gutter else 2)
        out.append(dict(fig=True, page=pno, rect=r, grp=grp, y0=r.y0, x0=r.x0, x1=r.x1, y1=r.y1))
    out.extend(block_figs)

    def key(e):
        g = e.grp if isinstance(e, Ln) else e['grp']
        y = e.y0 if isinstance(e, Ln) else e['y0']
        return (g, y)
    out.sort(key=key)
    return out


def tight_box(f):
    """Caixa justa dos glifos (a caixa de linha do PyMuPDF invade 1-2 pt das linhas vizinhas)."""
    ys0, ys1 = [], []
    for p in f['parts']:
        if 't' in p and p.get('t', '').strip() and 'oy' in p:
            ys0.append(p['oy'] - 0.80 * p['size']); ys1.append(p['oy'] + 0.26 * p['size'])
    if not ys0:
        return pymupdf.Rect(f['x0'], f['y0'], f['x1'], f['y1'])
    return pymupdf.Rect(f['x0'], max(min(ys0), f['y0']), f['x1'], min(max(ys1), f['y1']))


def _is_cap_like(f):
    return all(p['size'] <= 9.3 and not p['b'] for p in f['parts'] if p.get('t', '').strip()) and \
        len(''.join(p.get('t', '') for p in f['parts']).strip()) > 25


def _dist(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return (dx * dx + dy * dy) ** 0.5


def _make_ln(f, pno, gutter, boxes, small):
    x0, y0, x1, y1, parts = f['x0'], f['y0'], f['x1'], f['y1'], f['parts']
    parts = list(parts)
    for r in small:                               # emojis em linha
        cy = (r.y0 + r.y1) / 2
        if y0 - 3 <= cy <= y1 + 3 and x0 - 2 <= r.x0 <= x1 + 2:
            parts.append(dict(x=r.x0, img=r))
    parts.sort(key=lambda p: p['x'])
    sizes = [(len(p['t']), p['size']) for p in parts if 't' in p and p.get('t', '').strip()]
    size = max(sizes)[1] if sizes else 10.0
    # linha de base = origem dos trechos de tamanho principal
    main = [p['oy'] for p in parts if 't' in p and p.get('t', '').strip() and p['size'] >= size - 0.8]
    base = statistics.median(main) if main else None
    for p in parts:
        if 't' in p and base is not None and p['size'] < size * 0.8:
            d = p['oy'] - base
            p['pos'] = 'sub' if d > 1.2 else ('sup' if d < -1.2 else None)
    ln = Ln(page=pno, x0=x0, y0=y0, x1=x1, y1=y1, parts=parts, size=size, grp=f['grp'], wide=f['wide'], gutter=gutter)
    txts = [p for p in parts if 't' in p and p.get('t', '').strip()]
    ln.allbold = bool(txts) and all(p['b'] for p in txts)
    ln.boxed = any(bx.x0 - 2 <= x0 and x1 <= bx.x1 + 2 and bx.y0 - 2 <= y0 and y1 <= bx.y1 + 2 for bx in boxes)
    ln.taint = False
    ln.extra = []
    return ln


def classify(ln, left, right):
    """Define o tipo da linha."""
    t = ln.text().strip()
    p0 = ln.parts[0]
    if ln.allbold and 10.5 <= ln.size <= 11.5:
        if re.match(r'^QUEST[ãÃ]O\s+\d+', t, re.I):
            return 'qhead'
        return 'sec'
    if p0.get('circ') and len(p0['t'].strip()) == 1 and p0['t'].strip() in 'ABCDE':
        return 'alt'
    if ln.size <= 9.3 and not ln.allbold:
        return 'cap'
    if ln.allbold and re.match(r'^TEXTO\s+[IVX]+\s*$', t, re.I):
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
    if re.match(r'^\s*Quest[õo]es\s+de\s+\d+', s):
        return s
    if letters and sum(c.isupper() for c in letters) / len(letters) >= 0.4:
        return s.upper()
    return s


def runs_of(ln, drop_first_letter=False):
    """Converte as partes de uma linha em runs [(texto,b,i,size)] ou imagem."""
    runs = []
    first = True
    for p in ln.parts:
        if 'img' in p:
            runs.append(dict(img=p['img'], math=p.get('math', False)))
            continue
        t = p['t']
        if drop_first_letter and first:
            t = t.strip()[1:] if t.strip() else ''
            first = False
        runs.append(dict(t=t, b=p['b'], i=p['i'], size=p['size'], pos=p.get('pos')))
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
            items.append(dict(k='label', text=ls[0][0].text().strip().upper(), src=ls[0][0].page))
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
            if e.get('vector'):
                items.append(dict(k=('altfig' if e.get('alt') else 'fig'), page=e['page'], rect=e['rect'], src=e['page'],
                                  vector=True, letter=e.get('alt'), tipo=e.get('tipo')))
            else:
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
            elif b.text().lstrip().startswith(('•', '▪', '– ')):
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
            elif len(pt) > 1 and pt[0].text().lstrip().startswith(('•', '▪', '– ')) and pt[0].x0 < rest_left - 3:
                first_indent = pt[0].x0 - rest_left          # recuo pendurado: marcador sai da margem do texto
            lft = rest_left - left if len(pt) > 1 else (pt[0].x0 - left if first_indent == 0 else 0)
            if len(pt) == 1 and pt[0].x0 - left > 8:
                first_indent, lft = pt[0].x0 - left, 0.0
            if lft < 8 and not (first_indent < 0):
                lft = 0.0
            full = sum(1 for l in pt[:-1] if l.x1 >= rights[id(l)] - 6)
            justify = len(pt) >= 2 and full >= 0.6 * (len(pt) - 1)
            center = (len(pt) == 1 and not pt[0].text().lstrip().startswith(('•', '▪', '– ')) and abs((pt[0].x0 + pt[0].x1) / 2 - (left + right) / 2) < 7
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
