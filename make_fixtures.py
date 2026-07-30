#!/usr/bin/env python3
"""
make_fixtures.py — синтетические образцы всех четырёх форматов входа.

Нужны, чтобы критерии приёмки проверялись машинно, без файлов дела.
Значения взяты из раздела 11 задания (подтверждённые пользователем эталоны),
поэтому selftest.py может сверять результат с известным ответом.

Создаёт в fixtures/:
  dhcr_scan.pdf        настоящий PDF, одна картинка на страницу (таблица DHCR)
  motion_nyscef.pdf    zip-архив с JPEG под видом PDF (бланк Motion)
  chat_ru.png          одиночный PNG, кириллица
  with_bad_layer.pdf   PDF с чужим негодным текстовым слоем
"""
import io, os, zipfile
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
DJV = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
MONO = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
SERIF = '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'
OBLIQ = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf'

DPI = 200
W, H = int(8.5 * DPI), int(11 * DPI)

# ---- эталонные значения (раздел 11 задания) -------------------------------

DHCR_2014 = ['2014-D', 'RS', '01/02/2015', '530.78', '624.78',
             'FUEL SURC', '09/01/2014']
TENANTS = 'WANG CHANGYI / ZHAO YUQI / LI ZHIPENG'
LEASE = '09/01/2014 - 08/31/2015'
NOT_FOUND = '*REG NOT FOUND FOR SUBJECT PREMISES*'
MISSING_YEARS = list(range(2015, 2026))          # 11 лет

MOTION_PART, MOTION_ROOM = 'Part F', 'Room 523'
MOTION_RETURN = '19 August 2026, 9:30 am'
HAND_LINES = [
    'Motion for Leave to file a late verified answer,',
    'Notice of Motion / Affidavit, Verified Answer,',
    'Exhibit List + Exhibits A-M / Affidavit of Service',
]


def f(path, size):
    return ImageFont.truetype(path, size)


def page():
    return Image.new('RGB', (W, H), 'white')


# ---- 1. DHCR: таблица с колонками -----------------------------------------

def dhcr():
    im = page()
    d = ImageDraw.Draw(im)
    head, mono, small = f(DJV, 30), f(MONO, 20), f(DJV, 17)

    d.text((60, 60), 'NEW YORK STATE DIVISION OF HOUSING', font=head, fill='black')
    d.text((60, 100), 'AND COMMUNITY RENEWAL', font=head, fill='black')
    d.text((60, 150), 'Rent Registration History', font=f(DJV, 24), fill='black')
    d.text((60, 200), f'TENANTS: {TENANTS}', font=small, fill='black')
    d.text((60, 228), f'LEASE TERM: {LEASE}', font=small, fill='black')

    cols = [('YEAR-CODE', 60), ('STAT', 240), ('STATDATE', 330),
            ('LEGAL REG RENT', 520), ('ACTUAL RENT PAID', 700),
            ('PREFER. RENT', 900), ('SERVICE', 1080), ('FILE DATE', 1330)]
    y = 300
    for name, x in cols:
        d.text((x, y), name, font=f(MONO, 16), fill='black')
    d.line([(50, y + 26), (W - 50, y + 26)], fill='black', width=2)

    # строка 2014 — заполненная. PREFER. RENT намеренно пуста.
    y = 345
    vals = {60: DHCR_2014[0], 240: DHCR_2014[1], 330: DHCR_2014[2],
            520: DHCR_2014[3], 700: DHCR_2014[4], 900: '',
            1080: DHCR_2014[5], 1330: DHCR_2014[6]}
    for name, x in cols:
        if vals[x]:
            d.text((x, y), vals[x], font=mono, fill='black')

    # 2015-2025 — регистрации отсутствуют
    y += 46
    for yr in MISSING_YEARS:
        d.text((60, y), f'{yr}', font=mono, fill='black')
        d.text((240, y), NOT_FOUND, font=f(MONO, 18), fill='black')
        y += 40

    d.rectangle([50, 290, W - 50, y], outline='black', width=2)
    for _, x in cols[1:]:
        d.line([(x - 14, 290), (x - 14, y)], fill='black', width=1)

    p = os.path.join(FIX, 'dhcr_page.jpg')
    im.save(p, 'JPEG', quality=88)
    return p


# ---- 2. Motion: бланк с рукописной вписью ---------------------------------

def handwrite(d, xy, text, size=26, fill=(20, 20, 90)):
    """Имитация почерка: наклонный шрифт, дрожание базовой линии."""
    import random
    random.seed(len(text))
    fnt = f(OBLIQ, size)
    x, y = xy
    for ch in text:
        d.text((x, y + random.randint(-3, 3)), ch, font=fnt, fill=fill)
        x += fnt.getlength(ch) * random.uniform(0.92, 1.12)
    return x


def motion(npages=2):
    out = []
    for pg in range(1, npages + 1):
        im = page()
        d = ImageDraw.Draw(im)
        big, txt, small = f(SERIF, 26), f(SERIF, 21), f(SERIF, 17)

        d.text((60, 60), 'CIVIL COURT OF THE CITY OF NEW YORK', font=big, fill='black')
        d.text((60, 96), 'COUNTY OF NEW YORK - HOUSING PART', font=txt, fill='black')
        d.line([(60, 130), (W - 60, 130)], fill='black', width=1)

        if pg == 1:
            d.text((60, 160), 'NOTICE OF MOTION', font=big, fill='black')
            d.text((60, 210), 'Index No.:', font=txt, fill='black')
            handwrite(d, (200, 206), 'LT-306107-26/NY', 24)

            d.text((60, 260), 'PLEASE TAKE NOTICE that upon the annexed affidavit,',
                   font=txt, fill='black')
            d.text((60, 290), 'the undersigned will move this Court at', font=txt, fill='black')

            # критичные поля бланка — вписаны от руки
            d.text((60, 340), 'Part:', font=txt, fill='black')
            handwrite(d, (140, 336), MOTION_PART.split()[1], 28)
            d.text((300, 340), 'Room:', font=txt, fill='black')
            handwrite(d, (400, 336), MOTION_ROOM.split()[1], 28)

            d.text((60, 400), 'on the', font=txt, fill='black')
            handwrite(d, (140, 396), '19th', 26)
            d.text((230, 400), 'day of', font=txt, fill='black')
            handwrite(d, (320, 396), 'August', 26)
            d.text((460, 400), ', 20', font=txt, fill='black')
            handwrite(d, (510, 396), '26', 26)
            d.text((570, 400), 'at', font=txt, fill='black')
            handwrite(d, (610, 396), '9:30', 26)
            d.text((700, 400), "o'clock a.m., for an order:", font=txt, fill='black')

            y = 470
            for ln in HAND_LINES:
                handwrite(d, (100, y), ln, 25)
                y += 46

            d.text((60, y + 40), 'Dated:', font=txt, fill='black')
            handwrite(d, (150, y + 36), 'July 20, 2026', 25)
            d.text((60, y + 100), 'Signature:', font=txt, fill='black')
            handwrite(d, (200, y + 92), 'D. Pakseev', 34)
            d.text((60, y + 160), 'Print Name:', font=txt, fill='black')
            d.text((220, y + 160), 'DMITRII PAKSEEV, pro se', font=txt, fill='black')
        else:
            d.text((60, 170), 'AFFIRMATION IN SUPPORT', font=big, fill='black')
            y = 230
            for ln in ['1. I am the respondent pro se in this proceeding and am',
                       '   fully familiar with the facts set forth herein.',
                       '2. This motion seeks leave to file a late verified answer.',
                       '3. No prior application for this relief has been made.']:
                d.text((60, y), ln, font=txt, fill='black')
                y += 36
            d.text((60, y + 60), 'Sworn to before me this', font=txt, fill='black')
            handwrite(d, (380, y + 56), '20th', 26)
            d.text((470, y + 60), 'day of', font=txt, fill='black')
            handwrite(d, (560, y + 56), 'July', 26)
            d.text((660, y + 60), ', 2026', font=txt, fill='black')
            d.text((60, y + 130), 'Notary Public', font=txt, fill='black')
            d.text((60, y + 180), 'My commission expires:', font=txt, fill='black')
            handwrite(d, (400, y + 176), '02/28/2029', 26)
            # оттиск штампа поверх текста
            d.rectangle([700, y + 90, 1250, y + 230], outline=(150, 30, 30), width=3)
            d.text((730, y + 120), 'NOTARY PUBLIC', font=f(DJV, 26), fill=(150, 30, 30))
            d.text((730, y + 160), 'STATE OF NEW YORK', font=f(DJV, 22), fill=(150, 30, 30))

        p = os.path.join(FIX, f'motion_p{pg}.jpg')
        im.save(p, 'JPEG', quality=90)
        out.append(p)
    return out


# ---- 3. Скриншот переписки, кириллица --------------------------------------

def chat_ru():
    im = Image.new('RGB', (900, 620), (235, 238, 242))
    d = ImageDraw.Draw(im)
    fnt, tiny = f(DJV, 22), f(DJV, 15)
    msgs = [('Здравствуйте, отопление не работает третий день', 0),
            ('Заявку приняли, мастер придёт в пятницу', 1),
            ('Пятница прошла, никто не пришёл', 0),
            ('The super will come on Monday, sorry', 1)]
    y = 30
    for text, mine in msgs:
        w = int(fnt.getlength(text)) + 36
        x = 900 - w - 30 if mine else 30
        d.rounded_rectangle([x, y, x + w, y + 58], 14,
                            fill=(200, 240, 200) if mine else 'white')
        d.text((x + 18, y + 14), text, font=fnt, fill=(20, 20, 20))
        d.text((x + w - 60, y + 40), '12:0%d' % (y % 9), font=tiny, fill=(120, 120, 120))
        y += 78
    p = os.path.join(FIX, 'chat_ru.png')
    im.save(p, 'PNG')
    return p


# ---- сборка контейнеров ----------------------------------------------------

def build():
    os.makedirs(FIX, exist_ok=True)
    import img2pdf

    # (a) настоящий PDF со вставленным сканом
    dp = dhcr()
    lay = img2pdf.get_fixed_dpi_layout_fun((DPI, DPI))
    with open(dp, 'rb') as s, open(os.path.join(FIX, 'dhcr_scan.pdf'), 'wb') as o:
        img2pdf.convert(s.read(), outputstream=o, layout_fun=lay)

    # (b) «PDF», который на самом деле zip с JPEG (так отдаёт NYSCEF)
    mp = motion(2)
    with zipfile.ZipFile(os.path.join(FIX, 'motion_nyscef.pdf'), 'w') as z:
        for i, p in enumerate(mp, 1):
            z.write(p, f'page_{i}.jpeg')

    # (c) одиночный PNG
    chat_ru()

    # (d) PDF с чужим негодным текстовым слоем
    import fitz
    doc = fitz.open(os.path.join(FIX, 'dhcr_scan.pdf'))
    pg = doc[0]
    pg.insert_text((72, 72), 'GARBAGE LAYER zzz qqq 000', fontsize=9,
                   color=(1, 1, 1), render_mode=3)
    pg.insert_text((72, 90), '2014-D RS 99/99/9999 000.00', fontsize=9,
                   color=(1, 1, 1), render_mode=3)
    doc.save(os.path.join(FIX, 'with_bad_layer.pdf'))
    doc.close()

    for n in ('dhcr_scan.pdf', 'motion_nyscef.pdf', 'chat_ru.png', 'with_bad_layer.pdf'):
        p = os.path.join(FIX, n)
        print(f'  {n:22} {os.path.getsize(p)/1024:8.1f} КБ')


if __name__ == '__main__':
    print('создаю образцы в fixtures/')
    build()
