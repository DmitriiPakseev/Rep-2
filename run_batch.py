#!/usr/bin/env python3
"""
run_batch.py — прогоняет ocr2pdf.py по списку файлов и собирает ОДНУ общую
форму выбора по всем документам сразу (раздел 10.4).

Ранее сделанный выбор подхватывается автоматически: если рядом с
результатом лежит <имя>_corrections.json, он применяется. Поэтому пакет
можно перезапускать сколько угодно — выбор не теряется и накапливается
(раздел 6.5).

Использование:
    python3 run_batch.py СПИСОК.txt ПАПКА_ВЫХОДА [--lang eng] [--psm 6]
                         [--max-candidates 40] [--no-candidates]

СПИСОК.txt: по одному пути на строку; пустые строки и строки,
начинающиеся с #, игнорируются. После пути можно через | указать язык:
    /путь/к/chat.jpg | rus+eng
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
OCR = os.path.join(HERE, 'ocr2pdf.py')
FORM = os.path.join(HERE, 'make_review.py')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('list')
    ap.add_argument('outdir')
    ap.add_argument('--lang', default='eng')
    ap.add_argument('--psm', default='6')
    ap.add_argument('--max-candidates', default='40')
    ap.add_argument('--no-candidates', action='store_true')
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    jobs = []
    for raw in open(a.list, encoding='utf-8'):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '|' in line:
            path, lang = [x.strip() for x in line.split('|', 1)]
        else:
            path, lang = line, a.lang
        jobs.append((path, lang))

    print(f'файлов в очереди: {len(jobs)}\n')
    summary, reviews = [], []

    for i, (path, lang) in enumerate(jobs, 1):
        name = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(a.outdir, name + '.pdf')
        print(f'[{i}/{len(jobs)}] {os.path.basename(path)}  (lang={lang})')
        if not os.path.exists(path):
            print('   НЕТ ФАЙЛА\n')
            summary.append([name, 'нет файла', '', '', ''])
            continue

        cmd = [sys.executable, OCR, path, out, '--lang', lang, '--psm', a.psm]
        cmd += (['--no-candidates'] if a.no_candidates
                else ['--max-candidates', a.max_candidates])
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        dt = time.time() - t0

        if r.returncode != 0:
            print('   ОШИБКА:')
            print('   ' + '\n   '.join((r.stdout + r.stderr).strip()
                                       .splitlines()[-5:]))
            summary.append([name, 'ошибка', '', '', ''])
            print()
            continue

        rep_path = os.path.splitext(out)[0] + '_report.json'
        rev_path = os.path.splitext(out)[0] + '_review.json'
        rep = json.load(open(rep_path, encoding='utf-8'))
        v = rep['проверки']

        res = {x['result'] for x in v['5.1_видимый_слой']}
        bytes_ok = ('СОВПАДАЮТ' if res <= {'СОВПАДАЮТ'} else
                    ('ЧАСТИЧНО' if 'СОВПАДАЮТ' in res else 'РАСХОДЯТСЯ'))
        inv = v['5.1_слой_невидим']['result']
        nblank = sum(len(x['blank']) for x in v['5.2_текст_на_чернилах'])
        nmiss = sum(x.get('total_bands', 0) for x in v['5.2_чернила_без_текста'])
        coll = len(v['5.2_строки_таблицы']['collisions'])
        nrev = rep['неподтверждённых_мест']
        ncrit = rep['из_них_критичных']

        print(f'   готово за {dt:.0f} с   стр.{rep["страниц"]}   '
              f'байты: {bytes_ok}   невидим: {inv}')
        print(f'   на выбор: {nrev} мест (критичных {ncrit})   '
              f'слов не на чернилах: {nblank}   пропущено строк: {nmiss}   '
              f'столкновений строк: {coll}')
        for x in v['5.1_видимый_слой']:
            if x['result'] != 'СОВПАДАЮТ':
                print(f'   стр.{x["page"]}: {x["result"]} — {x["method"]} '
                      f'{x["detail"]}')
        summary.append([name, 'ок', bytes_ok, f'{nrev}/{ncrit}',
                        f'{nblank}/{nmiss}/{coll}'])
        if nrev:
            reviews.append(rev_path)
        print()

    print('=' * 84)
    print(f'{"файл":34} {"итог":8} {"байты":11} {"выбор/крит":11} '
          f'{"чернила/проп/столкн":18}')
    for row in summary:
        print(f'{row[0][:34]:34} {row[1]:8} {row[2]:11} {row[3]:11} {row[4]:18}')

    bad = [s for s in summary if s[1] != 'ок' or s[2] != 'СОВПАДАЮТ']
    print(f'\nтребуют внимания: {len(bad)}')
    for s in bad:
        print(f'  {s[0]}: {s[1]} / байты {s[2] or "—"}')

    if reviews:
        form = os.path.join(a.outdir, 'ВЫБОР.html')
        subprocess.run([sys.executable, FORM] + reviews + [form])
        print(f'\nОБЩАЯ ФОРМА ВЫБОРА: {form}')
        print('Откройте её в браузере, выберите варианты мышью, нажмите')
        print('«Скачать выбранное», затем:')
        print(f'    python3 apply_choices.py choices.json')
        print('    python3 run_batch.py ... (тот же вызов) — выбор применится')
    else:
        print('\nнеподтверждённых мест нет — форма выбора не нужна')


if __name__ == '__main__':
    main()
