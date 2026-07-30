#!/usr/bin/env python3
"""
make_review.py — форма выбора расшифровок (раздел 6.3).

Одна HTML-страница, открывается двойным щелчком, работает офлайн: вырезка
оригинала крупно, под ней варианты переключателями, выбор мышью. Картинки
вшиты в страницу как data: URI, поэтому файл можно переносить целиком.

Пользователь не набирает текст: поле «ввести своё» есть, но это запасной
путь, а не основной (раздел 6.2).

Кнопка «Скачать выбранное» отдаёт <ВЫХОД>_corrections.json. Положите его
рядом с PDF и запустите ocr2pdf.py снова — выбор применится и будет
применяться при всех дальнейших прогонах (раздел 6.5).

Использование:
    python3 make_review.py ВЫХОД_review.json ВЫХОД_review.html
"""
import argparse, base64, html, json, os, sys

CSS = """
:root{--bg:#fff;--fg:#111;--mut:#666;--line:#d8d8d8;--card:#fafafa;
      --crit:#b00020;--ok:#0a7d33;--warn:#8a5a00}
@media (prefers-color-scheme:dark){
:root{--bg:#151618;--fg:#e8e8e8;--mut:#9a9a9a;--line:#333;--card:#1d1f22;
      --crit:#ff6b81;--ok:#5ed17f;--warn:#e0b050}}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--fg);
     font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;background:var(--bg);padding:12px 0 16px;
       border-bottom:2px solid var(--line);z-index:10;margin-bottom:20px}
h1{font-size:20px;margin:0 0 6px}
.sub{color:var(--mut);font-size:13px}
button{font:inherit;padding:9px 16px;border-radius:7px;border:1px solid var(--line);
       background:var(--card);color:var(--fg);cursor:pointer}
button.primary{background:#1a73e8;color:#fff;border-color:#1a73e8;font-weight:600}
button:hover{filter:brightness(1.08)}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}
.item{border:1px solid var(--line);border-radius:10px;padding:16px;
      margin-bottom:18px;background:var(--card)}
.item.crit{border-left:5px solid var(--crit)}
.item.done{opacity:.55}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;
     border:1px solid var(--line);margin-right:6px;color:var(--mut)}
.tag.crit{color:var(--crit);border-color:var(--crit);font-weight:600}
.shot{max-width:100%;overflow-x:auto;margin:12px 0;background:#fff;
      border:1px solid var(--line);border-radius:6px;padding:6px}
.shot img{display:block;max-width:100%;height:auto;image-rendering:crisp-edges}
.shot.wide img{max-width:none}
.zoom{font-size:12px;color:var(--mut);margin-top:4px}
label.opt{display:block;padding:8px 10px;border-radius:6px;cursor:pointer;
          border:1px solid transparent}
label.opt:hover{background:rgba(127,127,127,.10)}
label.opt input{margin-right:9px}
.txt{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:14px}
.src{color:var(--mut);font-size:12px;margin-left:8px}
.recon{color:var(--warn)}
.why{font-size:12px;color:var(--mut);margin:6px 0 10px}
.own{width:100%;padding:8px;font-family:ui-monospace,monospace;font-size:14px;
     border:1px solid var(--line);border-radius:6px;background:var(--bg);
     color:var(--fg);margin-top:6px}
.count{font-weight:600}
"""

JS = """
const DATA = __DATA__;
function pick(i){
  const el = document.querySelector(`input[name="c${i}"]:checked`);
  if(!el) return null;
  if(el.value === '__own__'){
    const t = document.getElementById('own'+i).value.trim();
    return t ? t : null;
  }
  if(el.value === '__skip__') return null;
  return el.value;
}
function refresh(){
  let n = 0;
  DATA.forEach((d,i)=>{
    const got = pick(i) !== null;
    if(got) n++;
    document.getElementById('it'+i).classList.toggle('done', got);
  });
  document.getElementById('cnt').textContent = n;
}
document.addEventListener('change', refresh);
document.addEventListener('input', refresh);
function save(){
  const out = [];
  DATA.forEach((d,i)=>{
    const t = pick(i);
    if(t) out.push({doc:d.doc, stem:d.stem, page:d.page, box:d.box, text:t,
                    crop:d.crop, ocr:d.ocr,
                    chosen_at:new Date().toISOString()});
  });
  if(!out.length){ alert('Ничего не выбрано.'); return; }
  const blob = new Blob([JSON.stringify(out,null,1)],
                        {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '__OUTNAME__';
  a.click();
}
"""

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{css}</style></head><body>
<header>
  <h1>Выбор расшифровок — {doc}</h1>
  <div class="sub">Мест на проверку: {n}, из них критичных полей бланка: {ncrit}.
    Выбрано: <span class="count" id="cnt">0</span>. Ничего не выбранное в текст
    не попадёт.</div>
  <div class="bar">
    <button class="primary" onclick="save()">Скачать выбранное</button>
    <span class="sub">затем положите файл как <code>{corr}</code> рядом с PDF
      и запустите ocr2pdf.py снова</span>
  </div>
</header>
{items}
<script>{js}</script>
</body></html>
"""


def data_uri(path):
    with open(path, 'rb') as f:
        return 'data:image/png;base64,' + base64.b64encode(f.read()).decode()


def render_item(i, r, crops_dir):
    e = html.escape
    crit = ' crit' if r.get('critical') else ''
    tags = ['<span class="tag crit">критичное поле</span>'] if r.get('critical') else []
    for c in r.get('cues', []):
        tags.append(f'<span class="tag">{e(c)}</span>')
    if r.get('doc'):
        tags.append(f'<span class="tag">{e(r["doc"])}</span>')
    tags.append(f'<span class="tag">стр. {r["page"]}</span>')
    if r.get('pos_in'):
        tags.append(f'<span class="tag">от верха {r["pos_in"][1]}″, '
                    f'слева {r["pos_in"][0]}″</span>')

    # Широкую вырезку вписываем в ширину страницы, а не обрезаем: шапка
    # таблицы длиной 1360 px иначе показывалась бы кусочком «YEAR-CODE STAT».
    # Щелчок по картинке переключает на масштаб 1:1 с прокруткой.
    img = os.path.join(crops_dir, r.get('crop', ''))
    shot = (f'<div class="shot" onclick="this.classList.toggle(\'wide\')">'
            f'<img src="{data_uri(img)}" alt="">'
            f'<div class="zoom">щелчок по вырезке — крупный масштаб / '
            f'вписать в ширину</div></div>'
            if os.path.exists(img) else
            '<div class="why">вырезка недоступна</div>')

    why = '; '.join(r.get('reasons', [])) or 'не подтверждено'
    if r.get('second_pass'):
        why += f' · второй проход прочёл: «{e(r["second_pass"][:90])}»'

    opts, seen = [], set()
    for c in r.get('candidates', []):
        t = (c.get('text') or '').strip()
        if not t or t in seen:
            continue
        seen.add(t)
        recon = c.get('kind') == 'реконструкция'
        mark = ' <span class="src recon">реконструкция по шаблону, не прочтение</span>' \
            if recon else f'<span class="src">{e(c.get("source", ""))}' \
                          f'{" · голосов " + str(c["votes"]) if c.get("votes") else ""}</span>'
        opts.append(
            f'<label class="opt"><input type="radio" name="c{i}" '
            f'value="{e(t)}"><span class="txt">{e(t)}</span>{mark}</label>')
    if not opts:
        opts.append('<div class="why">вариантов не собрано — впишите своё</div>')

    opts.append(
        f'<label class="opt"><input type="radio" name="c{i}" value="__own__">'
        f'<span>ввести своё</span></label>'
        f'<input class="own" id="own{i}" placeholder="если ни один вариант не подходит">')
    opts.append(
        f'<label class="opt"><input type="radio" name="c{i}" value="__skip__" '
        f'checked><span>оставить неразобранным</span> '
        f'<span class="src">в текст не попадёт</span></label>')

    return (f'<div class="item{crit}" id="it{i}">{"".join(tags)}{shot}'
            f'<div class="why">почему сюда попало: {e(why)}</div>'
            f'<div class="why">OCR прочёл: <span class="txt">{e(r.get("ocr") or "—")}'
            f'</span></div>{"".join(opts)}</div>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('review', nargs='+',
                    help='один или несколько ВЫХОД_review.json')
    ap.add_argument('out')
    a = ap.parse_args()

    review, crops_of = [], {}
    for path in a.review:
        stem = path.replace('_review.json', '')
        doc = os.path.basename(stem)
        for r in json.load(open(path, encoding='utf-8')):
            r['doc'] = doc
            r['stem'] = stem
            review.append(r)
        crops_of[doc] = stem + '_crops'

    if not review:
        print('неподтверждённых мест нет — форма не нужна')
        return

    multi = len(a.review) > 1
    title = f'{len(a.review)} документов' if multi else review[0]['doc']
    corr = ('choices.json' if multi
            else os.path.basename(review[0]['stem']) + '_corrections.json')

    review.sort(key=lambda r: (not r.get('critical'), r.get('sim', 1),
                               r.get('min_conf', 100)))
    items = '\n'.join(render_item(i, r, crops_of[r['doc']])
                      for i, r in enumerate(review))
    slim = [{'doc': r['doc'], 'stem': r['stem'], 'page': r['page'],
             'box': r['box'], 'crop': r.get('crop', ''),
             'ocr': r.get('ocr', '')} for r in review]

    page = PAGE.format(
        title='Выбор расшифровок', css=CSS, doc=html.escape(title),
        n=len(review), ncrit=sum(1 for r in review if r.get('critical')),
        corr=html.escape(corr), items=items,
        js=JS.replace('__DATA__', json.dumps(slim, ensure_ascii=False))
             .replace('__OUTNAME__', corr))
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(page)
    print(f'{a.out}  ({os.path.getsize(a.out)/1024:.0f} КБ, мест {len(review)})')
    print('Откройте файл в браузере, выберите варианты, нажмите «Скачать выбранное».')
    if len(a.review) > 1:
        print(f'Затем: python3 apply_choices.py {corr} — разложит выбор по '
              f'документам и допишет в их _corrections.json.')
    else:
        print(f'Положите скачанный {corr} рядом с PDF и запустите ocr2pdf.py снова.')


if __name__ == '__main__':
    main()
