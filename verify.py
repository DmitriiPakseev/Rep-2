#!/usr/bin/env python3
"""
verify.py — проверки раздела 5 задания. Машинные, с однозначным итогом.

5.1  Тождественность видимого слоя — по хешу, не по рендеру.
     Сравнение рендеров пропускает пережатие (реестр 8.2.8), поэтому
     сравнивается либо байт в байт (JPEG проходит через img2pdf без
     перекодирования), либо попиксельно для форматов без потерь, где
     img2pdf законно меняет контейнер на Flate. Какой способ применён —
     пишется в отчёт, умолчаний нет.

5.2  Обратная проверка координат («чернила»): там, где слой утверждает
     текст, в оригинале должны быть тёмные пиксели; там, где ячейка
     объявлена пустой, их быть не должно. Эта проверка ловит «переезд»
     значения в соседнюю строку.

5.2  Структурная проверка таблицы: строки, собранные по координатам
     оригинала, не должны смешиваться в плоском тексте.
"""
import hashlib, os, subprocess
import numpy as np
from PIL import Image

DARK = 160          # порог «чернил» по яркости 0-255
LOSSY = {'jpeg', 'jpg'}


def md5_bytes(b):
    return hashlib.md5(b).hexdigest()


def md5_file(p):
    with open(p, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def pixel_hash(path_or_img):
    """Хеш распакованных пикселей — сравнение без оглядки на контейнер."""
    im = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img)
    a = np.asarray(im.convert('RGB'))
    return hashlib.md5(a.tobytes()).hexdigest(), a.shape


# ------------------------------------------------------- 5.1 видимый слой

def check_visible_identity(out_pdf, sources):
    """
    sources: [{'page': n, 'img': путь к исходной картинке, 'note': ...}]
    Возвращает список записей по странице с однозначным итогом.
    """
    import fitz
    rows = []
    doc = fitz.open(out_pdf)
    for i, s in enumerate(sources):
        rec = {'page': s['page'], 'src': os.path.basename(s['img']),
               'method': '', 'result': '', 'detail': ''}
        if s.get('note'):
            rec['result'] = 'НЕПРИМЕНИМО'
            rec['method'] = 'страница растрирована'
            rec['detail'] = s['note']
            rows.append(rec)
            continue
        if i >= doc.page_count:
            rec['result'] = 'РАСХОДЯТСЯ'
            rec['detail'] = 'страница отсутствует в результате'
            rows.append(rec)
            continue

        imgs = doc[i].get_images(full=True)
        if len(imgs) != 1:
            rec['result'] = 'НЕПРИМЕНИМО'
            rec['method'] = f'изображений на странице: {len(imgs)}'
            rows.append(rec)
            continue

        emb = doc.extract_image(imgs[0][0])
        ext = (emb.get('ext') or '').lower()
        src_ext = os.path.splitext(s['img'])[1].lstrip('.').lower()

        if src_ext in LOSSY:
            # JPEG вкладывается img2pdf без перекодирования: обязан совпасть побайтово
            rec['method'] = 'MD5 байтов вложенного потока'
            a, b = md5_file(s['img']), md5_bytes(emb['image'])
            rec['result'] = 'СОВПАДАЮТ' if a == b else 'РАСХОДЯТСЯ'
            rec['detail'] = f'исходный {a[:12]} / вложенный {b[:12]}'
        else:
            # формат без потерь: контейнер меняется законно, сверяем пиксели
            rec['method'] = 'MD5 распакованных пикселей (формат без потерь)'
            try:
                import io
                ha, sa = pixel_hash(s['img'])
                hb, sb = pixel_hash(Image.open(io.BytesIO(emb['image'])))
                ok = (ha == hb and sa == sb)
                rec['result'] = 'СОВПАДАЮТ' if ok else 'РАСХОДЯТСЯ'
                rec['detail'] = f'{sa} {ha[:12]} / {sb} {hb[:12]}'
            except Exception as e:
                rec['result'] = 'РАСХОДЯТСЯ'
                rec['detail'] = f'ошибка распаковки: {e}'
        rows.append(rec)
    doc.close()
    return rows


def check_invisible(base_pdf, out_pdf, work, dpi=80, edge=1):
    """
    Рендер до и после наложения слоя обязан совпасть попиксельно.

    Единственное допущенное исключение — крайний пиксель листа. При дробной
    ширине страницы (611.321 pt у скриншота 900 px) растеризатор даёт
    неполную последнюю колонку, и она отличается на единицы яркости в
    обоих файлах одинаково. Это артефакт растеризатора, а не видимый текст,
    поэтому рамка шириной edge пикселей исключается ЯВНО и об этом
    сообщается в отчёте — а не списывается молча на «шум» (реестр 8.2.8).

    Возвращает {'result': 'ДА'|'НЕТ'|'НЕ ПРОВЕРЕНО', 'detail': ...}.
    """
    a, b = os.path.join(work, 'inv_a'), os.path.join(work, 'inv_b')
    subprocess.run(['pdftoppm', '-r', str(dpi), '-png', base_pdf, a],
                   capture_output=True)
    subprocess.run(['pdftoppm', '-r', str(dpi), '-png', out_pdf, b],
                   capture_output=True)
    fa = sorted(f for f in os.listdir(work)
                if f.startswith('inv_a') and f.endswith('.png'))
    fb = sorted(f for f in os.listdir(work)
                if f.startswith('inv_b') and f.endswith('.png'))
    if not fa or len(fa) != len(fb):
        return {'result': 'НЕ ПРОВЕРЕНО', 'detail': 'рендер не собрался'}

    edge_only = 0
    for i, (x, y) in enumerate(zip(fa, fb), 1):
        p = np.asarray(Image.open(os.path.join(work, x)).convert('L')).astype(int)
        q = np.asarray(Image.open(os.path.join(work, y)).convert('L')).astype(int)
        if p.shape != q.shape:
            return {'result': 'НЕТ',
                    'detail': f'стр.{i}: размеры рендера разошлись {p.shape} / {q.shape}'}
        d = np.abs(p - q)
        if not d.any():
            continue
        inner = d[edge:-edge or None, edge:-edge or None]
        if inner.any():
            n = int((inner > 0).sum())
            return {'result': 'НЕТ',
                    'detail': f'стр.{i}: различий внутри листа {n}, '
                              f'макс. {int(inner.max())} по яркости'}
        edge_only += int((d > 0).sum())

    if edge_only:
        return {'result': 'ДА',
                'detail': f'внутри листа различий нет; на крайнем пикселе '
                          f'{edge_only} (артефакт растеризатора при дробной '
                          f'ширине страницы, исключён явно)'}
    return {'result': 'ДА', 'detail': 'рендеры совпали полностью'}


# ------------------------------------------------- 5.2 обратная проверка

def ink_fraction(gray, box):
    """Доля тёмных пикселей в рамке (координаты в пикселях оригинала)."""
    l, t, r, b = [int(round(v)) for v in box]
    h, w = gray.shape
    l, t = max(0, l), max(0, t)
    r, b = min(w, r), min(h, b)
    if r <= l or b <= t:
        return None
    crop = gray[t:b, l:r]
    return float((crop < DARK).mean())


def check_ink(img_path, words, min_ink=0.002):
    """
    Каждое слово слоя должно стоять на чернилах. Слово на пустом месте —
    признак того, что координаты уехали или текст выдуман.
    """
    gray = np.asarray(Image.open(img_path).convert('L'))
    blank = []
    for w in words:
        box = (w['left'], w['top'], w['left'] + w['w'], w['top'] + w['h'])
        fr = ink_fraction(gray, box)
        if fr is None:
            continue
        if fr < min_ink:
            blank.append({'text': w['text'], 'box': [int(v) for v in box],
                          'ink': fr})
    return {'total': len(words), 'blank': blank,
            'result': 'СОВПАДАЮТ' if not blank else 'РАСХОДЯТСЯ'}


def check_empty_cells(img_path, cells, max_ink=0.002):
    """
    cells: [{'name': 'PREFER. RENT 2014', 'box': [l,t,r,b]}] — ячейки,
    которые обязаны быть пустыми. Прямая проверка утверждения «пусто».
    """
    gray = np.asarray(Image.open(img_path).convert('L'))
    out = []
    for c in cells:
        fr = ink_fraction(gray, c['box'])
        out.append({'name': c['name'], 'ink': fr,
                    'result': 'ПУСТА' if (fr is not None and fr <= max_ink)
                              else 'НЕ ПУСТА'})
    return out


def _drop_rules(mask, min_len):
    """
    Гасит линейки бланка: сплошные прогоны тёмных пикселей длиннее min_len
    по горизонтали или по вертикали.

    Считается по пикселям, а не по огрублённой сетке: на сетке текст
    выглядит сплошным и фильтр съел бы строки. У глифов сплошной прогон
    короткий, у рамки таблицы — во всю строку или колонку.
    """
    out = mask.copy()
    for m in (out, out.T):                      # .T — вид, пишет в тот же буфер
        n, k = m.shape
        pad = np.zeros((n, k + 2), dtype=bool)
        pad[:, 1:-1] = m
        d = np.diff(pad.astype(np.int8), axis=1)
        starts = np.argwhere(d == 1)
        ends = np.argwhere(d == -1)
        if len(starts) != len(ends):
            continue
        long = (ends[:, 1] - starts[:, 1]) >= min_len
        for (r, s), (_, e) in zip(starts[long], ends[long]):
            m[r, s:e] = False
    return out


def check_coverage(img_path, words, cell=8, min_cells=12):
    """
    Обратная сторона проверки чернил: есть ли в оригинале чернила, которым
    НЕ соответствует ни одного слова слоя.

    Ловит молчаливый пропуск целой строки. На образце DHCR так пропала вся
    шапка таблицы (YEAR-CODE / STATDATE / PREFER. RENT): в .txt её просто
    нет, и ни проверка чернил, ни проверка строк этого не видят — они
    смотрят только на то, что распозналось.

    Линейки таблицы отсеиваются: сплошные горизонтальные и вертикальные
    прогоны — это разметка бланка, а не пропущенный текст.
    """
    gray = np.asarray(Image.open(img_path).convert('L'))
    h, w = gray.shape
    ch, cw = h // cell, w // cell
    if ch < 2 or cw < 2:
        return {'result': 'НЕ ПРОВЕРЕНО', 'bands': []}

    px = (gray[:ch * cell, :cw * cell] < DARK)
    med_h = float(np.median([wd['h'] for wd in words])) if words else 16.0
    px = _drop_rules(px, min_len=max(24, int(med_h * 2.5)))
    ink = px.reshape(ch, cell, cw, cell).any(axis=(1, 3))

    cov = np.zeros((ch, cw), dtype=bool)
    for wd in words:
        l = int(max(0, wd['left']) // cell)
        t = int(max(0, wd['top']) // cell)
        r = int(min(w, wd['left'] + wd['w']) // cell) + 1
        b = int(min(h, wd['top'] + wd['h']) // cell) + 1
        cov[t:b, l:r] = True

    un = ink & ~cov

    rows = un.sum(axis=1)
    bands, cur = [], None
    for y in range(ch):
        if rows[y] > 0:
            cur = [y, y] if cur is None else [cur[0], y]
        elif cur is not None:
            bands.append(cur); cur = None
    if cur:
        bands.append(cur)

    out = []
    for t, b in bands:
        n = int(un[t:b + 1].sum())
        if n < min_cells:
            continue
        cols = np.where(un[t:b + 1].any(axis=0))[0]
        out.append({'y_px': [t * cell, (b + 1) * cell],
                    'x_px': [int(cols[0] * cell), int((cols[-1] + 1) * cell)],
                    'cells': n})
    out.sort(key=lambda r: -r['cells'])
    return {'result': 'СОВПАДАЮТ' if not out else 'РАСХОДЯТСЯ',
            'bands': out[:20], 'total_bands': len(out)}


# ------------------------------------------- 5.2 структура таблицы

def rows_by_geometry(words, tol_ratio=0.6):
    """
    Группировка слов в строки по координатам оригинала, независимо от
    разметки tesseract. Служит эталоном для проверки плоского текста.
    """
    ws = [w for w in words if w['text'].strip()]
    if not ws:
        return []
    med_h = float(np.median([w['h'] for w in ws]))
    tol = max(2.0, med_h * tol_ratio)
    rows, cur, last = [], [], None
    for w in sorted(ws, key=lambda w: (w['top'] + w['h'] / 2.0, w['left'])):
        cy = w['top'] + w['h'] / 2.0
        if last is None or abs(cy - last) <= tol:
            cur.append(w)
        else:
            rows.append(sorted(cur, key=lambda x: x['left']))
            cur = [w]
        last = cy if last is None else (last * (len(cur) - 1) + cy) / len(cur)
    if cur:
        rows.append(sorted(cur, key=lambda x: x['left']))
    return rows


def check_row_binding(words, txt_path):
    """
    Ни один токен не должен перепрыгнуть в чужую строку плоского текста.

    Для каждой геометрической строки берём токены, встречающиеся ровно в
    ней и ни в какой другой строке (уникальные метки строки). Если два
    таких токена из разных строк оказались на одной строке .txt — колонки
    схлопнулись и значение привязано к чужой строке (реестр 8.1.1).
    """
    rows = rows_by_geometry(words)
    if not rows or not os.path.exists(txt_path):
        return {'result': 'НЕ ПРОВЕРЕНО', 'rows': len(rows), 'collisions': []}

    from collections import Counter
    seen = Counter()
    per_row = []
    for r in rows:
        toks = {w['text'] for w in r if len(w['text']) > 2}
        per_row.append(toks)
        for t in toks:
            seen[t] += 1
    marks = [{t for t in toks if seen[t] == 1} for toks in per_row]

    lines = [l for l in open(txt_path, encoding='utf-8').read().splitlines()
             if l.strip()]
    collisions = []
    for ln in lines:
        toks = set(ln.split())
        hit = [i for i, m in enumerate(marks) if m & toks]
        if len(hit) > 1:
            collisions.append({'line': ln.strip()[:100],
                               'rows': hit,
                               'tokens': sorted(
                                   {t for i in hit for t in (marks[i] & toks)})})
    return {'result': 'СОВПАДАЮТ' if not collisions else 'РАСХОДЯТСЯ',
            'rows': len(rows), 'collisions': collisions}
