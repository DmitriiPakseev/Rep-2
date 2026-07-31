#!/usr/bin/env python3
"""
ocr2pdf.py — скан любого из четырёх форматов -> searchable PDF + отчёт проверок.

ФОРМАТЫ ВХОДА (раздел 1 задания)
  * «PDF», который на самом деле zip с JPEG-страницами (NYSCEF)
  * настоящий PDF со вставленным сканом (одна картинка на страницу)
  * настоящий PDF с чужим негодным текстовым слоем — слой отбрасывается,
    видимый слой пересобирается из вложенного скана
  * одиночный JPEG/PNG

ЧТО ГАРАНТИРУЕТСЯ И ПРОВЕРЯЕТСЯ (раздел 5)
  1. Видимый слой тождествен исходному скану — по хешу, не по рендеру.
  2. Текстовый слой невидим — попиксельным сравнением рендеров.
  3. Слой стоит на чернилах — обратная проверка координат.
  4. Строки таблицы не смешиваются в плоском тексте.

ЧЕГО НЕ ДЕЛАЕТ САМО
  Не подставляет неподтверждённую расшифровку (раздел 6.4). Такие места
  собираются в <ВЫХОД>_review.json с несколькими вариантами на выбор;
  выбор делается в HTML-форме (make_review.py) и накапливается в
  <ВЫХОД>_corrections.json, который применяется при каждом прогоне.

ВЫХОД
  ВЫХОД.pdf                  searchable PDF        <- это читать человеку
  ВЫХОД.txt                  pdftotext -layout     <- для поиска, не для дат
  ВЫХОД_report.json          отчёт проверок раздела 5
  ВЫХОД_review.json          неподтверждённые места + варианты
  ВЫХОД_crops/               вырезки этих мест
  ВЫХОД_corrections.json     подтверждённый выбор, применяется автоматически

Использование:
  python3 ocr2pdf.py ВХОД ВЫХОД.pdf [--lang eng] [--psm 6] [--pages 1-3]
                                    [--max-candidates 40] [--no-candidates]
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, zipfile
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engines, verify
from textlayer import (read_tsv, group_lines, line_box, apply_corrections,
                       build)

PAGE_INCHES = 8.5
CONF_WORD = 60      # слово ниже этого — слабое
CONF_HARD = 25      # одно такое слово уже повод для вырезки
RUN_MIN = 2         # либо столько слабых слов подряд (реестр 8.4.17)
AGREE_MIN = 0.55    # согласие движков ниже этого — не подтверждено (6.1)

# Поля бланка, где вписка от руки несёт правовое последствие.
# Ошибка здесь дороже ошибки в теле текста, поэтому такие места идут
# первыми независимо от уверенности OCR.
CRITICAL = (
    'day of', 'dated', 'sworn', 'affirm', 'notary', 'commission expires',
    'part ', 'room', 'index no', 'docket', 'return', 'adjourn',
    'signature', 'print name', 'printed name', 'attorney for',
    'a.m', 'p.m', ' am,', ' pm,', 'oclock', "o'clock",
)


# ------------------------------------------------------------ разбор входа

def parse_pages(spec, total):
    if not spec:
        return list(range(1, total + 1))
    out = set()
    for part in spec.split(','):
        if '-' in part:
            a, b = part.split('-')
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(p for p in out if 1 <= p <= total)


def from_zip(path, work):
    """«PDF» от NYSCEF: на деле zip с JPEG-страницами."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.lower().endswith(('.jpeg', '.jpg', '.png', '.tif', '.tiff'))]
        names.sort(key=lambda n: [int(t) if t.isdigit() else t
                                  for t in re.split(r'(\d+)', n)])
        out = []
        for i, n in enumerate(names, 1):
            dst = os.path.join(work, f'src{i:03d}{os.path.splitext(n)[1].lower()}')
            with open(dst, 'wb') as f:
                f.write(z.read(n))
            out.append({'n': i, 'img': dst, 'dpi': None, 'note': ''})
        return out


def pdf_page_images(path):
    """Сколько изображений на странице и их ppi.
    Поля pdfimages -list: page num type width height color comp bpc enc
    interp object ID x-ppi y-ppi size ratio — ширина в поле 3 (реестр 8.3.10).
    """
    out = {}
    r = subprocess.run(['pdfimages', '-list', path], capture_output=True, text=True)
    for line in r.stdout.splitlines()[2:]:
        f = line.split()
        if len(f) < 14 or not f[0].isdigit() or f[2] != 'image':
            continue
        try:
            out.setdefault(int(f[0]), []).append(
                {'w': int(f[3]), 'h': int(f[4]), 'enc': f[8], 'xppi': f[12]})
        except (ValueError, IndexError):
            continue
    return out


def from_pdf(path, work, pages_spec):
    """
    Настоящий PDF. Вложенный скан берём через pdfimages -all: pdftoppm
    растрирует заново и теряет тождественность (реестр 8.2.7).
    Чужой текстовый слой при этом отбрасывается сам — мы пересобираем
    страницу из картинки.
    """
    total = 0
    r = subprocess.run(['pdfinfo', path], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.lower().startswith('pages:'):
            total = int(line.split(':')[1])
    imgs = pdf_page_images(path)
    out = []
    for pg in parse_pages(pages_spec, total):
        info = imgs.get(pg, [])
        entry = {'n': pg, 'img': None, 'dpi': None, 'note': ''}
        if len(info) == 1:
            pref = os.path.join(work, f'ex{pg:03d}')
            subprocess.run(['pdfimages', '-all', '-f', str(pg), '-l', str(pg),
                            path, pref], capture_output=True)
            got = sorted(f for f in os.listdir(work) if f.startswith(f'ex{pg:03d}'))
            if got:
                entry['img'] = os.path.join(work, got[0])
                try:
                    entry['dpi'] = int(round(float(info[0]['xppi'])))
                except ValueError:
                    entry['dpi'] = None
        if entry['img'] is None:
            pref = os.path.join(work, f'rz{pg:03d}')
            subprocess.run(['pdftoppm', '-r', '300', '-png', '-f', str(pg),
                            '-l', str(pg), path, pref], capture_output=True)
            got = sorted(f for f in os.listdir(work) if f.startswith(f'rz{pg:03d}'))
            if not got:
                continue
            entry['img'] = os.path.join(work, got[0])
            entry['dpi'] = 300
            entry['note'] = (f'страница содержит {len(info)} изображений — '
                             'растрирована, побайтовое совпадение НЕ гарантируется')
        out.append(entry)
    return out


def load_pages(path, work, pages_spec):
    kind = subprocess.run(['file', '-b', path], capture_output=True,
                          text=True).stdout.lower()
    if 'zip archive' in kind:
        pages = from_zip(path, work)
        keep = parse_pages(pages_spec, len(pages))
        return [p for p in pages if p['n'] in keep], 'zip с картинками (NYSCEF)'
    if 'pdf document' in kind:
        return from_pdf(path, work, pages_spec), 'настоящий PDF'
    return [{'n': 1, 'img': path, 'dpi': None, 'note': ''}], 'одиночное изображение'


CROP_TARGET_H = 64      # желаемая высота строки в вырезке, пикселей
CROP_MAX_W = 3000       # предел ширины вырезки


def save_crop(im, box, path):
    """
    Вырезка на диск с осмысленным масштабом.

    Увеличение вслепую вчетверо годилось на скане 1700 px, но на снимке
    телефона 2346x3047 давало полосы 9400 px шириной: вырезки распухали,
    а распознавание на них разваливалось — тот же перебор масштаба, из-за
    которого `2026` читалось как `2926`. Масштаб считается от высоты
    строки и ограничен сверху.
    """
    crop = im.crop(box)
    h = max(1, crop.height)
    k = CROP_TARGET_H / h if h < CROP_TARGET_H else 1.0
    k = min(k if k > 1 else 1.0, 4.0)
    if crop.width * k > CROP_MAX_W:
        k = CROP_MAX_W / crop.width
    if abs(k - 1.0) > 0.05:
        crop = crop.resize((max(1, round(crop.width * k)),
                            max(1, round(crop.height * k))), Image.LANCZOS)
    crop.save(path)


# ------------------------------------------------------ поиск слабых мест

def weak_lines(by_line, second=None):
    """
    Строки, достойные вырезки. Три признака, любого достаточно:

    1. КРИТИЧНОЕ ПОЛЕ БЛАНКА — на измерениях это единственный признак,
       который реально сработал. `2926` вместо `2026` и `396107` вместо
       `306107` прошли с уверенностью 90+ И при согласии обоих проходов;
       поймала их только принадлежность строки к полю «Dated» / «Index No».
    2. Слабая уверенность — несколько слабых слов подряд либо одно совсем
       провальное. Одиночное слабое слово на печатном тексте — шум,
       таких сотни (реестр 8.4.17).
    3. Расхождение двух проходов. Признак слабый и здесь ничего не добавил:
       оба прохода — tesseract, ошибается он в обоих режимах одинаково
       (сходство 0,92-1,0 даже там, где оба прочли неверно). Оставлен как
       страховка на шумных сканах, но полагаться на него нельзя — считать
       его работающим механизмом было бы ровно ошибкой 8.5.20.

    Настоящее расхождение появляется уровнем ниже — между вариантами по
    вырезке (engines.candidates_for_crop): там согласие падает до 0,36-0,68
    и верное чтение попадает в список. Поэтому основной механизм защиты —
    не автоматический фильтр, а выбор человеком из вариантов.
    """
    def meaningful(w):
        t = w['text']
        return len(t) > 1 or t.isalnum()

    out = []
    for ws in by_line:
        real = [w for w in ws if meaningful(w)]
        if not real:
            continue
        run = best = 0
        for w in real:
            run = run + 1 if w['conf'] < CONF_WORD else 0
            best = max(best, run)
        lo = min(w['conf'] for w in real)
        text = ' '.join(w['text'] for w in ws)
        low = text.lower()
        cues = [c.strip() for c in CRITICAL if c in low]

        sim, other = (1.0, '')
        if second is not None:
            sim, other = engines.line_disagreement(ws, second)

        reasons = []
        if second is not None and sim < AGREE_MIN:
            reasons.append(f'проходы разошлись ({sim:.0%})')
        if best >= RUN_MIN or lo < CONF_HARD:
            reasons.append(f'слабая уверенность (min {lo:.0f})')
        if cues:
            reasons.append('критичное поле бланка')
        if not reasons:
            continue

        out.append({'lo': lo, 'text': text, 'run': best, 'sim': round(sim, 3),
                    'second': other[:200], 'reasons': reasons,
                    'bad': [(w['text'], w['conf']) for w in real
                            if w['conf'] < CONF_WORD],
                    'critical': bool(cues), 'cues': cues,
                    'box': line_box(ws)})
    out.sort(key=lambda s: (not s['critical'], s['sim'], -s['run'], s['lo']))
    return out


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--lang', default='eng')
    ap.add_argument('--psm', default='6')
    ap.add_argument('--psm2', default='11', help='режим второго прохода')
    ap.add_argument('--no-second', action='store_true',
                    help='без второго прохода (быстрее, но почерк не ловится)')
    ap.add_argument('--pages', default=None, help='напр. 1-3 или 1,4,7')
    ap.add_argument('--max-candidates', type=int, default=40,
                    help='для скольких мест собирать варианты (0 — для всех)')
    ap.add_argument('--no-candidates', action='store_true',
                    help='не собирать варианты (быстрый прогон)')
    a = ap.parse_args()

    for t in ('tesseract', 'qpdf', 'pdfunite', 'pdftotext', 'pdfimages',
              'pdftoppm', 'pdfinfo', 'file'):
        if not shutil.which(t):
            raise SystemExit(f'нет {t}')
    import img2pdf

    work = tempfile.mkdtemp()
    stem = os.path.splitext(a.out)[0]
    crops_dir = stem + '_crops'
    corr_path = stem + '_corrections.json'
    review_path = stem + '_review.json'
    report_path = stem + '_report.json'
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)

    pages, fmt = load_pages(a.src, work, a.pages)
    if not pages:
        raise SystemExit('нечего обрабатывать')

    # накопленный выбор пользователя применяется автоматически (6.5)
    corrections, n_corr = {}, 0
    if os.path.exists(corr_path):
        for c in json.load(open(corr_path, encoding='utf-8')):
            if str(c.get('text', '')).strip() and c.get('box'):
                corrections.setdefault(int(c['page']), []).append(c)
                n_corr += 1

    shutil.rmtree(crops_dir, ignore_errors=True)
    os.makedirs(crops_dir, exist_ok=True)

    print(f'вход: {fmt};  страниц к обработке: {len(pages)}')
    if n_corr:
        print(f'применяю ранее подтверждённых правок: {n_corr}')

    bases, layers, sources, review = [], [], [], []
    ink_reports, applied, page_words, coverage = [], [], [], []

    for p in pages:
        n, img = p['n'], p['img']
        dpi = p['dpi'] or max(72, round(Image.open(img).width / PAGE_INCHES))

        # видимый слой: исходные байты без перекодирования (реестр 8.2.6)
        bp = os.path.join(work, f'b{n:03d}.pdf')
        lay = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
        with open(img, 'rb') as s, open(bp, 'wb') as d:
            img2pdf.convert(s.read(), outputstream=d, layout_fun=lay)

        tsv, w_px, h_px = engines.ocr_page_tsv(img, dpi, work, f't{n:03d}',
                                               a.lang, a.psm)
        words = read_tsv(tsv, 1.0 / engines.UPSCALE)
        by_line = group_lines(words)

        fixes = corrections.get(n, [])
        if fixes:
            words, rep = apply_corrections(words, fixes)
            for text, removed in rep:
                applied.append(f'стр.{n}: «{text[:50]}» вместо {removed}')

        lp = os.path.join(work, f'l{n:03d}.pdf')
        build([{'dpi': dpi, 'w_px': w_px, 'h_px': h_px, 'words': words}], lp)
        bases.append(bp); layers.append(lp)
        sources.append({'page': n, 'img': img, 'note': p['note']})
        page_words.append({'page': n, 'words': words})

        # 5.2 обратная проверка координат: слой должен стоять на чернилах
        ink = verify.check_ink(img, words)
        ink['page'] = n
        ink_reports.append(ink)

        # и обратно: чернила, которым не соответствует ни одного слова
        cov = verify.check_coverage(img, words)
        cov['page'] = n
        cov['dpi'] = dpi
        coverage.append(cov)

        # неподтверждённые места -> вырезки + варианты
        second = engines.second_pass(img, dpi, work, f't{n:03d}', a.lang,
                                     a.psm2) if not a.no_second else None
        weak = weak_lines(by_line, second)
        fixed_boxes = [c['box'] for c in fixes]

        def already_fixed(box):
            cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
            return any(l <= cx <= r and t <= cy <= b
                       for l, t, r, b in fixed_boxes)

        with Image.open(img) as im:
            for k, s in enumerate(weak, 1):
                if already_fixed(s['box']):
                    continue
                l, t, r, b = s['box']
                pad = 8
                box = (max(0, l - pad), max(0, t - pad),
                       min(im.width, r + pad), min(im.height, b + pad))
                if box[2] - box[0] < 5 or box[3] - box[1] < 5:
                    continue
                fn = f'p{n:03d}_{k:02d}.png'
                # изоляция вырезки заметно поднимает качество (реестр 8.3.14)
                save_crop(im, box, os.path.join(crops_dir, fn))
                review.append({
                    'id': f'{n}_{k}', 'page': n, 'box': s['box'],
                    'crop': fn, 'critical': s['critical'], 'cues': s['cues'],
                    'ocr': s['text'][:200], 'min_conf': s['lo'],
                    'reasons': s['reasons'], 'sim': s['sim'],
                    'second_pass': s['second'],
                    'pos_in': [round(l / dpi, 2), round(t / dpi, 2)],
                    'candidates': [], 'agreement': None, 'text': '',
                })
            # пропущенные строки: чернила есть, слов нет. Их тоже нужно
            # дать восстановить, иначе шапка таблицы просто исчезнет
            # из текста и колонки станет не с чем сопоставить.
            for j, bd in enumerate(cov['bands'], 1):
                l, r = bd['x_px']
                t, b = bd['y_px']
                if already_fixed([l, t, r, b]):
                    continue
                box = (max(0, l - 8), max(0, t - 8),
                       min(im.width, r + 8), min(im.height, b + 8))
                if box[2] - box[0] < 5 or box[3] - box[1] < 5:
                    continue
                fn = f'p{n:03d}_miss{j:02d}.png'
                save_crop(im, box, os.path.join(crops_dir, fn))
                review.append({
                    'id': f'{n}_miss{j}', 'page': n, 'box': [l, t, r, b],
                    'crop': fn, 'critical': True,
                    'cues': ['строка пропущена распознаванием'],
                    'ocr': '', 'min_conf': 0.0,
                    'reasons': ['в оригинале есть чернила, в слое нет слов'],
                    'sim': 0.0, 'second_pass': '',
                    'pos_in': [round(l / dpi, 2), round(t / dpi, 2)],
                    'candidates': [], 'agreement': None, 'text': '',
                })

        if p['note']:
            print(f'  стр.{n}: ВНИМАНИЕ — {p["note"]}')

    # Ранее собранные варианты переносим на те же места. Иначе быстрый
    # прогон с --no-candidates стирал бы уже накопленные варианты, и форма
    # выбора после него оказывалась пустой — терялась работа, а не время.
    prev = {}
    if os.path.exists(review_path):
        try:
            for r in json.load(open(review_path, encoding='utf-8')):
                if r.get('candidates'):
                    prev[(int(r['page']), tuple(r['box']))] = r['candidates']
        except (ValueError, KeyError, TypeError):
            prev = {}
    carried = 0
    for r in review:
        k = (int(r['page']), tuple(r['box']))
        if k in prev and not r['candidates']:
            r['candidates'] = prev[k]
            carried += 1
    if carried:
        print(f'перенесено ранее собранных наборов вариантов: {carried}')

    # --- варианты на выбор (6.2). Критичные поля обслуживаются первыми.
    if not a.no_candidates and review:
        review.sort(key=lambda r: (not r['critical'], r['min_conf']))
        limit = len(review) if a.max_candidates == 0 else a.max_candidates
        print(f'собираю варианты для {min(limit, len(review))} из {len(review)} мест...')
        for i, r in enumerate(review):
            if i >= limit:
                if not r['candidates'] and r['ocr']:
                    r['candidates'] = [{'text': r['ocr'],
                                        'source': f'tesseract psm{a.psm}',
                                        'kind': 'прочтение', 'votes': 1}]
                continue
            cp = os.path.join(crops_dir, r['crop'])
            cands = engines.candidates_for_crop(cp, a.lang)
            cands += engines.reconstructions(r['ocr'])
            r['candidates'] = cands[:8]
            r['agreement'] = round(engines.agreement(cands), 3)

    # --- сборка результата
    def unite(parts, dst):
        if len(parts) == 1:
            shutil.copy(parts[0], dst)
        else:
            subprocess.run(['pdfunite'] + parts + [dst], capture_output=True)

    base_all = os.path.join(work, 'base.pdf')
    text_all = os.path.join(work, 'text.pdf')
    unite(bases, base_all); unite(layers, text_all)
    # один слой одним шрифтом, наложение однократное (реестр 8.3.11-12)
    subprocess.run(['qpdf', base_all, '--overlay', text_all, '--', a.out],
                   capture_output=True)
    if not os.path.exists(a.out):
        raise SystemExit('qpdf не собрал результат')

    txt_path = stem + '.txt'
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(subprocess.run(['pdftotext', '-layout', a.out, '-'],
                               capture_output=True, text=True).stdout)

    # --- проверки раздела 5
    vis = verify.check_visible_identity(a.out, sources)
    inv = verify.check_invisible(base_all, a.out, work)
    # структурная проверка по страницам: pdftotext разделяет их \f
    txt_pages = open(txt_path, encoding='utf-8').read().split('\f')
    binding = {'result': 'СОВПАДАЮТ', 'rows': 0, 'collisions': [], 'pages': []}
    for i, pw in enumerate(page_words):
        page_txt = os.path.join(work, f'pt{i:03d}.txt')
        with open(page_txt, 'w', encoding='utf-8') as f:
            f.write(txt_pages[i] if i < len(txt_pages) else '')
        b = verify.check_row_binding(pw['words'], page_txt)
        b['page'] = pw['page']
        binding['pages'].append(b)
        binding['rows'] += b['rows']
        for c in b['collisions']:
            c['page'] = pw['page']
        binding['collisions'] += b['collisions']
    if binding['collisions']:
        binding['result'] = 'РАСХОДЯТСЯ'
    elif not binding['rows']:
        binding['result'] = 'НЕ ПРОВЕРЕНО'

    report = {
        'источник': os.path.abspath(a.src), 'формат': fmt,
        'результат': os.path.abspath(a.out), 'страниц': len(bases),
        'применено_правок': n_corr,
        'проверки': {
            '5.1_видимый_слой': vis,
            '5.1_слой_невидим': inv,
            '5.2_текст_на_чернилах': ink_reports,
            '5.2_чернила_без_текста': coverage,
            '5.2_строки_таблицы': binding,
        },
        'неподтверждённых_мест': len(review),
        'из_них_критичных': sum(1 for r in review if r['critical']),
    }
    json.dump(report, open(report_path, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    json.dump(review, open(review_path, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    # --- вывод
    print(f'\n{a.out}  ({os.path.getsize(a.out)/1e6:.2f} МБ, страниц {len(bases)})')
    print(f'{txt_path}')
    print(f'{report_path}   {review_path}   {crops_dir}/')
    for line in applied:
        print(f'  правка {line}')

    print('\nПРОВЕРКИ (раздел 5)')
    ok_vis = [v for v in vis if v['result'] == 'СОВПАДАЮТ']
    na_vis = [v for v in vis if v['result'] == 'НЕПРИМЕНИМО']
    bad_vis = [v for v in vis if v['result'] == 'РАСХОДЯТСЯ']
    print(f'  5.1 видимый слой тождествен: {len(ok_vis)}/{len(vis)} страниц СОВПАДАЮТ')
    for v in na_vis:
        print(f'      стр.{v["page"]}: НЕПРИМЕНИМО — {v["method"]}. {v["detail"]}')
    for v in bad_vis:
        print(f'      стр.{v["page"]}: РАСХОДЯТСЯ — {v["detail"]}')
    if ok_vis:
        print(f'      способ: {ok_vis[0]["method"]}')
    print(f'  5.1 текстовый слой невидим: {inv["result"]}')
    print(f'      {inv["detail"]}')
    nblank = sum(len(i['blank']) for i in ink_reports)
    ntot = sum(i['total'] for i in ink_reports)
    print(f'  5.2 текст стоит на чернилах: '
          f'{"СОВПАДАЮТ" if nblank == 0 else "РАСХОДЯТСЯ"} '
          f'({ntot - nblank}/{ntot} слов на чернилах)')
    for i in ink_reports:
        for b in i['blank'][:3]:
            print(f'      стр.{i["page"]}: «{b["text"]}» на пустом месте '
                  f'(чернил {b["ink"]*100:.2f}%)')
    nb = sum(c.get('total_bands', 0) for c in coverage)
    print(f'  5.2 чернила без текста: '
          f'{"СОВПАДАЮТ" if nb == 0 else "РАСХОДЯТСЯ"} '
          f'(областей с чернилами, но без слов: {nb})')
    for c in coverage:
        for bd in c['bands'][:3]:
            print(f'      стр.{c["page"]}: полоса y={bd["y_px"]} x={bd["x_px"]} '
                  f'— текст в оригинале есть, в слое нет')
    print(f'  5.2 строки не смешиваются: {binding["result"]} '
          f'(строк {binding["rows"]}, столкновений {len(binding["collisions"])})')
    for c in binding['collisions'][:3]:
        print(f'      строки {c["rows"]}: {c["tokens"]}')

    crit = [r for r in review if r['critical']]
    print(f'\nНеподтверждённых мест: {len(review)}, из них критичных полей: {len(crit)}')
    for r in crit[:10]:
        print(f'  стр.{r["page"]}  {r["crop"]}  [{", ".join(r["cues"])}]  '
              f'OCR: {r["ocr"][:60]}')
    if review:
        print(f'\nВыбрать варианты:  python3 make_review.py {review_path} '
              f'{stem}_review.html')
        print('Ничего из неподтверждённого в текст НЕ попало (раздел 6.4).')
    print(f'\nЧитать человеку: {a.out}. Файл .txt — только для поиска;')
    print('даты, имена, подписи и Affirmation Language читать по PDF.')

    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
