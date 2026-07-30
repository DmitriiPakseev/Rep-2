#!/usr/bin/env python3
"""
engines.py — распознавание и сбор вариантов-кандидатов.

Раздел 6.2: для неподтверждённого места нужны НЕСКОЛЬКО вариантов, между
которыми пользователь выбирает мышью. Источники здесь:

  * tesseract в разных режимах (psm 6 / 7 / 11 / 13) — они дают разное;
  * та же вырезка после бинаризации и после увеличения — изоляция вырезки
    заметно поднимает качество (реестр 8.3.14);
  * реконструкция по шаблону бланка — ТОЛЬКО с явной пометкой, что это
    догадка, а не прочтение (раздел 6.2, запрет 6.4).

Согласие движков между собой — единственный признак подтверждённости,
которому можно верить: метрика уверенности почерк не ловит, `June` читается
как `Junk` с уверенностью 94 (раздел 6.4, реестр 8.4).
"""
import os, re, subprocess, tempfile
from PIL import Image, ImageOps, ImageFilter

PSM_SET = ('6', '7', '11', '13', '4')
UPSCALE = 3


def _run_tesseract(img_path, out_base, lang, psm, dpi, tsv=False):
    cmd = ['tesseract', img_path, out_base, '-l', lang, '--psm', str(psm)]
    if dpi:
        cmd += ['-c', f'user_defined_dpi={int(dpi)}']
    if tsv:
        cmd.append('tsv')
    subprocess.run(cmd, capture_output=True)
    return out_base + ('.tsv' if tsv else '.txt')


def ocr_page_tsv(img_path, dpi, work, stem, lang, psm):
    """
    Страница целиком -> TSV. Апскейл повышает качество мелкого текста;
    dpi передаётся явно, иначе tesseract возьмёт 70 и наложение
    разъедется (реестр 8.3.9).
    """
    with Image.open(img_path) as im:
        w, h = im.width, im.height
        big = im.convert('RGB').resize((w * UPSCALE, h * UPSCALE), Image.LANCZOS)
    big_path = os.path.join(work, stem + '_big.png')
    big.save(big_path)
    tsv = _run_tesseract(big_path, os.path.join(work, stem), lang, psm,
                         dpi * UPSCALE, tsv=True)
    os.remove(big_path)
    return tsv, w, h


def second_pass(img_path, dpi, work, stem, lang, psm2='11'):
    """
    Независимый второй проход другим режимом сегментации.

    Нужен потому, что уверенность почерк не видит: `June` читается как
    `Junk` с уверенностью 94 (раздел 6.4). Единственный признак, который
    реально срабатывает, — расхождение двух проходов между собой
    (раздел 5.2, первый пункт).

    Оговорка честности: оба прохода — tesseract, это не два независимых
    движка. Настоящий второй движок для рукописного (TrOCR) требует
    huggingface.co, который в этом окружении закрыт политикой сети.
    """
    from textlayer import read_tsv
    tsv, _, _ = ocr_page_tsv(img_path, dpi, work, stem + '_2', lang, psm2)
    return read_tsv(tsv, 1.0 / UPSCALE)


def _norm(s):
    """Сравниваем по существу: регистр и небуквенный шум не считаем."""
    return re.sub(r'[^0-9a-zа-яё]', '', (s or '').lower())


def line_disagreement(line_words, other_words):
    """
    Сходство строки с тем, что во втором проходе попало в ту же область.
    0.0 — полное расхождение, 1.0 — совпадение. Ниже порога место
    считается неподтверждённым (6.1).
    """
    import difflib
    l = min(w['left'] for w in line_words)
    t = min(w['top'] for w in line_words)
    r = max(w['left'] + w['w'] for w in line_words)
    b = max(w['top'] + w['h'] for w in line_words)
    pad = (b - t) * 0.3
    got = []
    for w in other_words:
        cx, cy = w['left'] + w['w'] / 2.0, w['top'] + w['h'] / 2.0
        if l - pad <= cx <= r + pad and t - pad <= cy <= b + pad:
            got.append(w)
    if not got:
        return 0.0, ''
    got.sort(key=lambda w: w['left'])
    other = ' '.join(w['text'] for w in got)
    a, c = _norm(' '.join(w['text'] for w in line_words)), _norm(other)
    if not a and not c:
        return 1.0, other
    return difflib.SequenceMatcher(None, a, c).ratio(), other


TARGET_H = 70          # желаемая высота строки в пикселях для tesseract


def _variants(crop):
    """
    Варианты предобработки одной вырезки.

    Масштаб приводится к TARGET_H, а не умножается вслепую: вырезки на
    диск уже пишутся увеличенными вчетверо, и повторное умножение давало
    16-кратный размер, на котором распознавание разваливалось —
    `Dated: July 20, 2026` превращалось в `2926` и правильного варианта в
    списке не оказывалось вовсе.

    Поля белого по краям обязательны: на узкой полосе без полей режимы
    7/8/13 цепляют край и роняют первый знак.
    """
    g = crop.convert('L')
    k = TARGET_H / max(1, g.height)
    if k < 0.95 or k > 1.6:
        g = g.resize((max(1, int(g.width * k)), max(1, int(g.height * k))),
                     Image.LANCZOS)

    def pad(im):
        return ImageOps.expand(im, border=30, fill=255)

    yield 'норм', pad(g)
    yield 'норм+контраст', pad(ImageOps.autocontrast(g))
    yield 'норм+порог', pad(ImageOps.autocontrast(g).point(
        lambda p: 255 if p > 150 else 0))
    yield 'норм+резкость', pad(g.filter(ImageFilter.UnsharpMask(2, 150, 3)))
    yield 'крупно', pad(g.resize((g.width * 2, g.height * 2), Image.LANCZOS))


def _clean(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def candidates_for_crop(crop_path, lang='eng', dpi=300, extra=()):
    """
    Несколько прочтений одной вырезки. Возвращает список
    {'text','source','kind','votes'}, отсортированный по числу голосов.

    kind='прочтение' — то, что движок действительно увидел.
    kind='реконструкция' — догадка по шаблону, помечается явно (6.2).
    """
    from collections import Counter
    seen = Counter()
    origin = {}
    with Image.open(crop_path) as im:
        crop = im.copy()
    with tempfile.TemporaryDirectory() as work:
        for vname, vimg in _variants(crop):
            p = os.path.join(work, 'v.png')
            vimg.save(p)
            for psm in PSM_SET:
                out = _run_tesseract(p, os.path.join(work, 'o'), lang, psm, dpi)
                if not os.path.exists(out):
                    continue
                txt = _clean(open(out, encoding='utf-8', errors='replace').read())
                if not txt:
                    continue
                seen[txt] += 1
                origin.setdefault(txt, f'tesseract psm{psm} / {vname}')

    cands = [{'text': t, 'source': origin[t], 'kind': 'прочтение', 'votes': n}
             for t, n in seen.most_common()]
    for e in extra:
        cands.append(dict(e))
    return cands


def agreement(cands):
    """
    Доля голосов у лидера. 1.0 — все режимы прочли одинаково.
    Ниже порога место считается неподтверждённым (6.1).
    """
    reads = [c for c in cands if c.get('kind') == 'прочтение']
    total = sum(c['votes'] for c in reads) or 0
    if not total:
        return 0.0
    return max(c['votes'] for c in reads) / total


# --------------------------------------------------- реконструкция бланка

FORM_PATTERNS = (
    (r'\bday of\b', ['on the ___ day of _______, 20__',
                     'дата: день / месяц / год — прочитать по PDF']),
    (r'\bpart\b', ['Part __']),
    (r'\broom\b', ['Room ___']),
    (r"o'?clock|a\.?m|p\.?m", ['__:__ a.m.', '__:__ p.m.']),
    (r'commission expires', ['My commission expires __/__/____']),
)


def reconstructions(ocr_text):
    """
    Шаблоны бланка по контексту строки. Это НЕ прочтение: возвращается с
    kind='реконструкция', в HTML-форме помечается отдельно и никогда не
    подставляется само (6.4).
    """
    out, low = [], (ocr_text or '').lower()
    for rx, opts in FORM_PATTERNS:
        if re.search(rx, low):
            for o in opts:
                out.append({'text': o, 'source': 'шаблон бланка',
                            'kind': 'реконструкция', 'votes': 0})
    return out
