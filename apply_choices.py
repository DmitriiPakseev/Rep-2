#!/usr/bin/env python3
"""
apply_choices.py — раскладывает выбор из общей формы по документам.

Общая форма (make_review.py по нескольким документам) отдаёт один
choices.json. Этот скрипт дописывает каждую запись в
<документ>_corrections.json.

ДОПИСЫВАЕТ, А НЕ ПЕРЕЗАПИСЫВАЕТ (раздел 6.5): файл выбора — актив
документа, накопленный за все прогоны, а не временный артефакт. Запись с
той же рамкой на той же странице считается обновлением и заменяет
прежнюю; всё остальное сохраняется. Перед изменением делается .bak.

Использование:
    python3 apply_choices.py choices.json [--dry-run]
"""
import argparse, json, os, shutil


def key(rec):
    return (int(rec['page']), tuple(int(v) for v in rec['box']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('choices')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    picks = json.load(open(a.choices, encoding='utf-8'))
    by_doc = {}
    for r in picks:
        if not str(r.get('text', '')).strip():
            continue
        stem = r.get('stem') or r.get('doc')
        if not stem:
            raise SystemExit('в записи нет ни stem, ни doc — какой это документ?')
        by_doc.setdefault(stem, []).append(r)

    if not by_doc:
        print('в файле нет ни одного заполненного выбора')
        return

    for stem, recs in sorted(by_doc.items()):
        path = stem + '_corrections.json'
        old = []
        if os.path.exists(path):
            old = json.load(open(path, encoding='utf-8'))
        merged = {key(r): r for r in old if str(r.get('text', '')).strip()}
        added = updated = 0
        for r in recs:
            k = key(r)
            if k in merged:
                if merged[k].get('text') != r['text']:
                    updated += 1
            else:
                added += 1
            merged[k] = {'page': int(r['page']),
                         'box': [int(v) for v in r['box']],
                         'text': r['text'], 'crop': r.get('crop', ''),
                         'ocr': r.get('ocr', ''),
                         'chosen_at': r.get('chosen_at', '')}
        out = [merged[k] for k in sorted(merged)]
        print(f'{path}: было {len(old)}, добавлено {added}, '
              f'обновлено {updated}, стало {len(out)}')
        if a.dry_run:
            continue
        if os.path.exists(path):
            shutil.copy(path, path + '.bak')
        json.dump(out, open(path, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)

    if a.dry_run:
        print('\nсухой прогон: ничего не записано')
    else:
        print('\nТеперь запустите ocr2pdf.py по этим документам заново — '
              'выбор применится.')


if __name__ == '__main__':
    main()
