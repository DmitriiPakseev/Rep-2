#!/usr/bin/env python3
"""
textlayer.py — невидимый текстовый слой из слов с координатами.

Один слой, один шрифт (DejaVuSans, кириллица работает). Второй слой поверх
готового слоя tesseract строить нельзя: у tesseract безглифовый шрифт /F1 со
своей таблицей ToUnicode, второй /F1 читается через чужую таблицу и даёт
мусор (реестр 8.3.11, 8.3.12).

Позиционирование — по базовой линии, не по верху рамки (реестр 8.1.4).
Ширина слова подгоняется под рамку горизонтальным масштабом: pdftotext
-layout восстанавливает колонки по advance width, поэтому совпадение
ширины важнее совпадения кегля (реестр 8.1.1, 8.1.3).
"""
import csv, os
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FONT_NAME = 'OCRLayerFont'
DESCENT = 0.212          # доля высоты рамки ниже базовой линии у DejaVuSans
_registered = False


def _font():
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
        _registered = True
    return FONT_NAME


def read_tsv(tsv, scale=1.0, min_conf=-1.0):
    """Слова уровня 5 из TSV tesseract, координаты в пикселях оригинала."""
    if not os.path.exists(tsv):
        return []
    out = []
    with open(tsv, encoding='utf-8') as f:
        for r in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            if r.get('level') != '5' or not (r.get('text') or '').strip():
                continue
            try:
                conf = float(r['conf'])
                if conf < min_conf:
                    continue
                out.append({
                    'text': r['text'].strip(),
                    'left': int(r['left']) * scale,
                    'top': int(r['top']) * scale,
                    'w': int(r['width']) * scale,
                    'h': int(r['height']) * scale,
                    'conf': conf,
                    'line': (r['block_num'], r['par_num'], r['line_num']),
                })
            except (ValueError, KeyError):
                continue
    return out


def group_lines(words):
    """Слова -> строки по разметке tesseract, слева направо, сверху вниз."""
    groups = {}
    for w in words:
        groups.setdefault(w.get('line', 0), []).append(w)
    out = [sorted(ws, key=lambda w: w['left']) for ws in groups.values()]
    out.sort(key=lambda ws: (round(ws[0]['top']), ws[0]['left']))
    return out


def line_box(ws):
    return [int(min(w['left'] for w in ws)), int(min(w['top'] for w in ws)),
            int(max(w['left'] + w['w'] for w in ws)),
            int(max(w['top'] + w['h'] for w in ws))]


def apply_corrections(words, corrections):
    """
    corrections: [{'box': [l,t,r,b], 'text': '...'}] в пикселях оригинала.

    Слово, центр которого попал в рамку, удаляется: иначе рядом с
    подтверждённой расшифровкой останется мусор OCR и оба уйдут в .txt.
    Возвращает (слова, отчёт [(текст, [что удалено])]).
    """
    corrections = [c for c in corrections if str(c.get('text', '')).strip()
                   and c.get('box')]
    kept, removed = [], {i: [] for i in range(len(corrections))}
    for w in words:
        cx, cy = w['left'] + w['w'] / 2.0, w['top'] + w['h'] / 2.0
        hit = None
        for i, c in enumerate(corrections):
            l, t, r, b = c['box']
            if l <= cx <= r and t <= cy <= b:
                hit = i
                break
        if hit is None:
            kept.append(w)
        else:
            removed[hit].append(w['text'])

    for i, c in enumerate(corrections):
        l, t, r, b = c['box']
        kept.append({'text': str(c['text']).strip(), 'left': float(l),
                     'top': float(t), 'w': float(r - l), 'h': float(b - t),
                     'conf': 100.0, 'line': ('fix', 0, i), 'corrected': True})
    return kept, [(corrections[i]['text'], removed[i])
                  for i in range(len(corrections))]


def draw_words(c, words, dpi, page_h_px):
    """Невидимый текст (3 Tr), по слову на рамку."""
    fn = _font()
    k = 72.0 / dpi
    for w in words:
        txt = w['text']
        if not txt.strip():
            continue
        size = max(1.0, w['h'] * k)
        base_w = pdfmetrics.stringWidth(txt, fn, size)
        if base_w <= 0:
            continue
        target = max(0.5, w['w'] * k)
        # базовая линия: низ рамки минус выносной элемент (реестр 8.1.4)
        baseline_px = w['top'] + w['h'] - w['h'] * DESCENT
        t = c.beginText()
        t.setTextRenderMode(3)                        # невидимо
        t.setFont(fn, size)
        t.setHorizScale(target / base_w * 100.0)      # ширина = ширина рамки
        t.setTextOrigin(w['left'] * k, (page_h_px - baseline_px) * k)
        t.textLine(txt)
        c.drawText(t)


def build(pages, out_pdf):
    """pages: [{'dpi':200,'w_px':1700,'h_px':2200,'words':[...]}, ...]"""
    c = canvas.Canvas(out_pdf)
    for p in pages:
        k = 72.0 / p['dpi']
        c.setPageSize((p['w_px'] * k, p['h_px'] * k))
        draw_words(c, p['words'], p['dpi'], p['h_px'])
        c.showPage()
    c.save()
    return out_pdf
