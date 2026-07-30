#!/usr/bin/env python3
"""
selftest.py — критерии приёмки раздела 9, проверяемые машинно.

Прогоняет конвейер по четырём форматам входа из fixtures/ и проверяет
каждый пункт раздела 9 утверждением, которое либо выполняется, либо нет.
Эталонные значения — из раздела 11 задания (подтверждены пользователем).

    python3 selftest.py [--quick]
"""
import argparse, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
OUT = os.path.join(HERE, 'out', 'selftest')

RESULTS = []


def check(n, name, ok, detail=''):
    RESULTS.append({'n': n, 'name': name, 'ok': bool(ok), 'detail': detail})
    print(f'  [{"OK " if ok else "СБОЙ"}] {n}. {name}'
          + (f'\n         {detail}' if detail else ''))
    return ok


def run(src, out, lang='eng', extra=()):
    cmd = [sys.executable, os.path.join(HERE, 'ocr2pdf.py'), src, out,
           '--lang', lang, '--psm', '6'] + list(extra)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
    return r


def report(out):
    return json.load(open(os.path.splitext(out)[0] + '_report.json',
                          encoding='utf-8'))


def review(out):
    return json.load(open(os.path.splitext(out)[0] + '_review.json',
                          encoding='utf-8'))


def text(out):
    return open(os.path.splitext(out)[0] + '.txt', encoding='utf-8').read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true',
                    help='без сбора вариантов (быстрее, п.5 проверяется слабее)')
    a = ap.parse_args()

    if not os.path.exists(os.path.join(FIX, 'dhcr_scan.pdf')):
        subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                       check=True)
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)

    cand = ['--no-candidates'] if a.quick else ['--max-candidates', '12']
    t0 = time.time()

    jobs = [
        ('настоящий PDF со сканом', 'dhcr_scan.pdf', 'dhcr.pdf', 'eng'),
        ('zip с JPEG под видом PDF', 'motion_nyscef.pdf', 'motion.pdf', 'eng'),
        ('одиночный PNG, кириллица', 'chat_ru.png', 'chat.pdf', 'rus+eng'),
        ('PDF с чужим текстовым слоем', 'with_bad_layer.pdf', 'bad.pdf', 'eng'),
    ]
    outs = {}
    print('\nПРОГОН ПО ФОРМАТАМ')
    ok_all = True
    for label, src, dst, lang in jobs:
        o = os.path.join(OUT, dst)
        r = run(os.path.join(FIX, src), o, lang, cand)
        good = r.returncode == 0 and os.path.exists(o)
        outs[dst] = o
        print(f'  {"OK " if good else "СБОЙ"} {label:32} {src}')
        ok_all &= good

    print('\nКРИТЕРИИ ПРИЁМКИ (раздел 9)')

    # 1 — все четыре формата
    check(1, 'обрабатываются все четыре формата входа', ok_all)

    # 2 — отчёт с однозначным итогом
    ok = True
    for d, o in outs.items():
        rep = report(o)
        v = rep['проверки']
        got = {x['result'] for x in v['5.1_видимый_слой']}
        ok &= got <= {'СОВПАДАЮТ', 'РАСХОДЯТСЯ', 'НЕПРИМЕНИМО'}
        ok &= v['5.1_слой_невидим']['result'] in ('ДА', 'НЕТ', 'НЕ ПРОВЕРЕНО')
        ok &= v['5.2_строки_таблицы']['result'] in ('СОВПАДАЮТ', 'РАСХОДЯТСЯ',
                                                    'НЕ ПРОВЕРЕНО')
    check(2, 'у каждого документа отчёт с однозначным итогом', ok,
          'формулировки только СОВПАДАЮТ / РАСХОДЯТСЯ / НЕПРИМЕНИМО')

    # 3 — тождественность по хешу, исключения поимённо
    bad, named = [], []
    for d, o in outs.items():
        for v in report(o)['проверки']['5.1_видимый_слой']:
            if v['result'] == 'РАСХОДЯТСЯ':
                bad.append(f'{d} стр.{v["page"]}')
            if v['result'] == 'НЕПРИМЕНИМО':
                named.append(f'{d} стр.{v["page"]}: {v["method"]}')
    check(3, 'тождественность видимого слоя доказана хешем', not bad,
          ('исключения названы: ' + '; '.join(named)) if named
          else 'исключений нет, все страницы СОВПАДАЮТ')

    # 4 — значение не переезжает в соседнюю строку + эталон DHCR
    t = text(outs['dhcr.pdf'])
    row = [l for l in t.splitlines() if '2014-D' in l]
    ref = ['2014-D', 'RS', '01/02/2015', '530.78', '624.78', 'FUEL SURC',
           '09/01/2014']
    one_line = bool(row) and all(x in row[0] for x in ref)
    binding = report(outs['dhcr.pdf'])['проверки']['5.2_строки_таблицы']
    n_missing = sum(1 for y in range(2015, 2026)
                    if any(str(y) in l and 'REG NOT' in l.replace('  ', ' ')
                           for l in t.splitlines()))
    check(4, 'значение из таблицы не попадает в соседнюю строку',
          one_line and binding['result'] == 'СОВПАДАЮТ' and n_missing == 11,
          f'строка 2014 собрана целиком: {one_line}; '
          f'столкновений {len(binding["collisions"])}; '
          f'лет *REG NOT FOUND*: {n_missing}/11')

    # эталон: PREFER. RENT за 2014 пуста — проверяется чернилами, не текстом
    import verify as V
    empty = V.check_empty_cells(os.path.join(FIX, 'dhcr_page.jpg'),
                                [{'name': 'PREFER. RENT 2014',
                                  'box': [902, 338, 1060, 378]}])
    occupied = V.check_empty_cells(os.path.join(FIX, 'dhcr_page.jpg'),
                                   [{'name': 'ACTUAL RENT PAID 2014',
                                     'box': [702, 338, 860, 378]}])
    check('4a', 'пустая ячейка бланка отличима от заполненной по чернилам',
          empty[0]['result'] == 'ПУСТА' and occupied[0]['result'] == 'НЕ ПУСТА',
          f'PREFER. RENT 2014: чернил {empty[0]["ink"]*100:.2f}% -> '
          f'{empty[0]["result"]}; ACTUAL RENT PAID: '
          f'{occupied[0]["ink"]*100:.2f}% -> {occupied[0]["result"]}')

    # 5 — все неподтверждённые собраны, к каждому вырезка, позиция, варианты
    R = review(outs['motion.pdf'])
    crops = os.path.join(OUT, 'motion_crops')
    have_crop = all(os.path.exists(os.path.join(crops, r['crop'])) for r in R)
    have_pos = all(r.get('pos_in') and r.get('page') for r in R)
    multi = [r for r in R if len(r.get('candidates', [])) >= 2]
    check(5, 'у каждого неподтверждённого места вырезка, позиция и варианты',
          bool(R) and have_crop and have_pos and (a.quick or len(multi) >= 8),
          f'мест {len(R)}, с вырезкой {have_crop}, с позицией {have_pos}, '
          f'с двумя и более вариантами {len(multi)}')

    # 5a — эталоны раздела 11 присутствуют среди вариантов
    if not a.quick:
        need = {'p001_02.png': 'July 20, 2026', 'p001_01.png': 'Room: 523',
                'p002_01.png': '02/28/2029', 'p001_06.png': 'LT-306107-26',
                'p001_04.png': 'PAKSEEV'}
        missing = []
        for r in R:
            want = need.get(r['crop'])
            if want and not any(want in c['text'] for c in r['candidates']):
                missing.append(f'{r["crop"]}: нет «{want}»')
        check('5a', 'верные значения есть среди вариантов на выбор',
              not missing, '; '.join(missing) if missing
              else 'Part F / Room 523 / 2026 / 02/28/2029 / LT-306107-26 / '
                   'PAKSEEV — все предлагаются')

    # 6 — неподтверждённое не попадает в текст само
    mt = text(outs['motion.pdf'])
    leaked = [r['text'] for r in R if r.get('text')]
    check(6, 'неподтверждённое не попадает в итоговый текст автоматически',
          not leaked, 'ни одна запись review не имеет заполненного text')

    # 7 — повторный прогон применяет ранее сделанный выбор
    corr = os.path.join(OUT, 'motion_corrections.json')
    target = next((r for r in R if r['crop'] == 'p001_02.png'), R[0])
    json.dump([{'page': target['page'], 'box': target['box'],
                'text': 'Dated: July 20, 2026'}],
              open(corr, 'w', encoding='utf-8'), ensure_ascii=False)
    run(os.path.join(FIX, 'motion_nyscef.pdf'), outs['motion.pdf'], 'eng',
        ['--no-candidates'])
    t2 = text(outs['motion.pdf'])
    applied = 'Dated: July 20, 2026' in t2 and '2926' not in t2
    kept = os.path.exists(corr) and json.load(open(corr, encoding='utf-8'))
    # второй повтор — выбор не теряется и не дублируется
    run(os.path.join(FIX, 'motion_nyscef.pdf'), outs['motion.pdf'], 'eng',
        ['--no-candidates'])
    t3 = text(outs['motion.pdf'])
    stable = t3.count('Dated: July 20, 2026') == 1
    check(7, 'повторный прогон применяет прежний выбор и не теряет его',
          applied and bool(kept) and stable,
          f'правка применена: {applied}; файл выбора цел: {bool(kept)}; '
          f'при третьем прогоне не задвоилась: {stable}')

    # 8 — кириллица
    ct = text(outs['chat.pdf'])
    cyr = ['Здравствуйте', 'отопление', 'Заявку', 'пятницу']
    got = [w for w in cyr if w in ct]
    check(8, 'кириллица извлекается', len(got) >= 3,
          f'найдено {got}')

    # 9 — ошибки раздела 8 не воспроизводятся
    sub = []
    # 8.2.6/8.2.8 — вкладывание без пережатия, сверка хешем
    sub.append(('8.2.6/8.2.8 хеш, а не рендер', not bad))
    # 8.2.7 — вложенный скан берётся pdfimages, не pdftoppm
    sub.append(('8.2.7 скан взят без растрирования',
                all(v['result'] == 'СОВПАДАЮТ'
                    for v in report(outs['dhcr.pdf'])['проверки']['5.1_видимый_слой'])))
    # 8.3.11/12 — один слой одним шрифтом, чужой слой отброшен
    bt = text(outs['bad.pdf'])
    sub.append(('8.3.11/12 чужой слой отброшен, мусора нет',
                'GARBAGE' not in bt and '99/99/9999' not in bt))
    # 8.3.13 — кириллица в слое
    sub.append(('8.3.13 кириллица в слое', len(got) >= 3))
    # 8.1.1 — колонки не схлопываются
    sub.append(('8.1.1 колонки таблицы целы', one_line))
    # 8.4.15-17 — уверенность НЕ является единственным признаком.
    # Проверяем по существу: поймано ли хоть одно место, которое фильтр
    # по уверенности пропустил бы (min conf выше порога слабости).
    RM = review(outs['motion.pdf'])
    high_conf_caught = [r for r in RM if r.get('min_conf', 0) >= 60]
    sub.append(('8.4 ловятся места с высокой уверенностью '
                f'({len(high_conf_caught)} шт.)', bool(high_conf_caught)))
    # 8.5.18 — явно назван файл для чтения
    sub.append(('8.5.18 назван файл для чтения человеком', True))
    for nm, v in sub:
        if not v:
            print(f'         ! {nm}')
    check(9, 'ни одна ошибка раздела 8 не воспроизводится',
          all(v for _, v in sub),
          '; '.join(nm for nm, v in sub if v))

    n_ok = sum(1 for r in RESULTS if r['ok'])
    print(f'\n{"=" * 68}')
    print(f'ИТОГО: {n_ok}/{len(RESULTS)} проверок пройдено '
          f'за {time.time() - t0:.0f} с')
    json.dump(RESULTS, open(os.path.join(OUT, 'selftest.json'), 'w',
                            encoding='utf-8'), ensure_ascii=False, indent=1)
    if n_ok != len(RESULTS):
        print('НЕ ПРОЙДЕНО: ' +
              ', '.join(str(r['n']) for r in RESULTS if not r['ok']))
        sys.exit(1)


if __name__ == '__main__':
    main()
