#!/usr/bin/env python3
"""
read_handwriting.py — добавляет к вырезкам ещё один источник вариантов:
модель, которая умеет читать изображения.

ЧТО ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ
  Дописывает варианты в <ВЫХОД>_review.json как ещё одного «движка».
  НИЧЕГО не применяет и не пишет в corrections: даже уверенный ответ
  модели остаётся вариантом на выбор (раздел 6.4). Цена выдуманной даты
  возврата или фамилии выше, чем цена лишнего щелчка мышью.

  Ответ модели помечается kind='прочтение (модель)' и в форме выбора
  показывается наравне с прочтениями tesseract, но с указанием источника.
  Неуверенный ответ помечается отдельно и ставится ниже.

НУЖЕН КЛЮЧ
    export ANTHROPIC_API_KEY=sk-ant-...
  Без ключа скрипт не нужен: конвейер и без него собирает варианты
  режимами tesseract. Это добавка, а не обязательная часть.

Использование:
    python3 read_handwriting.py ВЫХОД_review.json [--only-critical]
                                [--model claude-sonnet-5] [--dry-run]
"""
import argparse, base64, json, os, re, sys, time, urllib.error, urllib.request

API = 'https://api.anthropic.com/v1/messages'
MODEL = 'claude-sonnet-5'

PROMPT = """Ты расшифровываешь фрагмент отсканированного судебного документа.

На картинке — одна или несколько строк. Часть может быть напечатана,
часть вписана от руки, возможен оттиск штампа.

Верни СТРОГО JSON без пояснений и без markdown:
{"text": "<точная расшифровка одной строкой>",
 "kind": "printed" | "handwritten" | "stamp" | "mixed",
 "certain": true | false,
 "note": "<что именно неразборчиво, или пустая строка>"}

Правила:
- Расшифровывай ТОЛЬКО то, что видишь. Ничего не додумывай и не
  дополняй по смыслу юридического бланка.
- Если хотя бы один знак неразборчив, поставь "certain": false и
  опиши проблему в note. Если неразборчиво почти всё — верни
  "text": "UNREADABLE".
- Числа, даты, номера частей и комнат, имена переписывай посимвольно.
  Не исправляй и не нормализуй их.
- Сохраняй регистр и пунктуацию оригинала.
"""


def call(img_path, key, model, timeout=120):
    data = base64.b64encode(open(img_path, 'rb').read()).decode()
    media = 'image/png' if img_path.lower().endswith('.png') else 'image/jpeg'
    body = json.dumps({
        'model': model, 'max_tokens': 400,
        'messages': [{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64',
                                         'media_type': media, 'data': data}},
            {'type': 'text', 'text': PROMPT}]}]
    }).encode()
    req = urllib.request.Request(API, data=body, method='POST', headers={
        'content-type': 'application/json',
        'x-api-key': key,
        'anthropic-version': '2023-06-01'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    txt = ''.join(b.get('text', '') for b in out.get('content', []))
    txt = re.sub(r'^```(?:json)?|```$', '', txt.strip(), flags=re.M).strip()
    return json.loads(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('review', help='ВЫХОД_review.json')
    ap.add_argument('--model', default=MODEL)
    ap.add_argument('--only-critical', action='store_true',
                    help='только критичные поля бланка — дешевле и быстрее')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    review = json.load(open(a.review, encoding='utf-8'))
    crops_dir = a.review.replace('_review.json', '_crops')

    todo = [r for r in review
            if (not a.only_critical or r.get('critical'))
            and os.path.exists(os.path.join(crops_dir, r.get('crop', '')))]

    key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not key and not a.dry_run:
        raise SystemExit(
            'нет ANTHROPIC_API_KEY.\n'
            'Это НЕ значит, что рукописное прочитать нечем: варианты уже\n'
            'собраны режимами tesseract и лежат в форме выбора. Модель —\n'
            'дополнительный источник вариантов, а не единственный.\n'
            'Задайте ключ или запустите с --dry-run.')

    print(f'вырезок к отправке: {len(todo)} из {len(review)}'
          + ('   [СУХОЙ ПРОГОН, запросы не отправляются]' if a.dry_run else ''))

    stats = {'ok': 0, 'неуверенно': 0, 'нечитаемо': 0, 'ошибка': 0}
    for r in todo:
        img = os.path.join(crops_dir, r['crop'])
        if a.dry_run:
            print(f'  {r["crop"]}  стр.{r["page"]}  '
                  f'{"КРИТ " if r.get("critical") else ""}OCR: {r["ocr"][:56]}')
            continue
        try:
            res = call(img, key, a.model)
        except (urllib.error.HTTPError, urllib.error.URLError,
                json.JSONDecodeError, ValueError, KeyError) as ex:
            print(f'  {r["crop"]}: ОШИБКА КАНАЛА — {ex}')
            stats['ошибка'] += 1
            time.sleep(1)
            continue

        text = (res.get('text') or '').strip()
        certain = bool(res.get('certain'))
        kind = res.get('kind', 'mixed')
        note = res.get('note', '')

        if text == 'UNREADABLE' or not text:
            stats['нечитаемо'] += 1
            print(f'  ?? {r["crop"]}  модель: неразборчиво  ({note[:50]})')
            continue

        stats['ok' if certain else 'неуверенно'] += 1
        cands = r.setdefault('candidates', [])
        # варианты модели ставим первыми, если она уверена
        rec = {'text': text,
               'source': f'{a.model} [{kind}]'
                         + ('' if certain else f' — не уверена: {note[:60]}'),
               'kind': 'прочтение (модель)',
               'votes': 3 if certain else 1}
        if certain:
            cands.insert(0, rec)
        else:
            cands.append(rec)
        r['model_note'] = note
        print(f'  {"ok" if certain else "??"} {r["crop"]}  «{text[:64]}»')
        time.sleep(0.3)

    if a.dry_run:
        print('\nСухой прогон. Задайте ANTHROPIC_API_KEY и запустите снова.')
        return

    json.dump(review, open(a.review, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f'\n{a.review} обновлён: ' +
          ', '.join(f'{k}={v}' for k, v in stats.items() if v))
    print('Ответы модели добавлены КАК ВАРИАНТЫ. Ничего не применено:')
    print('в текст попадёт только то, что вы выберете в форме.')
    print(f'Пересоберите форму: python3 make_review.py {a.review} '
          f'{a.review.replace("_review.json", "_review.html")}')


if __name__ == '__main__':
    main()
