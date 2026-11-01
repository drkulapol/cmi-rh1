#!/usr/bin/env python3
"""แปลงข้อมูลดิบจาก fetch_cmi.py -> data-<ปีงบ>.json ของ dashboard cmi-rh1 (รูปแบบเดิมทุก key)

ข้อมูล master ของโรงพยาบาล (ประเภท รพ., ระดับบริการ, เตียง, ปชก.UC, กลุ่ม) ไม่มีบนเว็บ CMI
จึงอ่านจากไฟล์ data เดิม (H[0..7]) แล้วแทนที่เฉพาะตัวเลข CMI
ถ้ายังไม่มีไฟล์ของปีนั้น (เช่น ปีงบใหม่) ให้ระบุ --base เป็นไฟล์ data ของปีอื่นเพื่อใช้ master

ใช้: python scripts/build_data.py /tmp/cmi/cmi_raw_2570.json data-2570.json --base data-2569.json
"""
import argparse, json, os, re
from datetime import datetime, timezone, timedelta

TM = {'ต.ค.': 0, 'พ.ย.': 1, 'ธ.ค.': 2, 'ม.ค.': 3, 'ก.พ.': 4, 'มี.ค.': 5,
      'เม.ย.': 6, 'พ.ค.': 7, 'มิ.ย.': 8, 'ก.ค.': 9, 'ส.ค.': 10, 'ก.ย.': 11}


def num(s):
    s = (s or '').replace(',', '').strip()
    if s in ('', '-'):
        return None
    try:
        f = float(s)
    except ValueError:
        return s
    return int(f) if f.is_integer() else f


def cnt(s):  # "49(22.37)" -> (49, 22.37)
    m = re.match(r'([\d,\.]+)\(([\d\.]+)\)', s.replace(' ', ''))
    return (num(m.group(1)), num(m.group(2))) if m else (num(s), None)


def first_table(x):
    return x[0] if x else None


def build(raw, old, run):
    D = {k: old[k] for k in ('meta',)}
    D['meta'] = dict(old['meta'])
    master = old['H']
    spcl = raw['spcl']

    H, BAND, LOW, MON, PDX, DRG = [], [], [], [], [], []
    # คงลำดับรหัสเดิม (index ใน dashboard ไม่เลื่อน) แล้วต่อท้ายรหัสใหม่
    pdxc, drgc = [list(x) for x in old.get('PDXC', [])], [list(x) for x in old.get('DRGC', [])]
    pdxi = {c[0]: i for i, c in enumerate(pdxc)}; drgi = {c[0]: i for i, c in enumerate(drgc)}
    failed = []
    for i, h in enumerate(master):
        hc = h[0]
        s = spcl.get(hc) or spcl.get(hc.lstrip('0')) or {}
        ip = first_table(s.get('ipd_percentage'))
        tot = next((r for r in (ip['rows'] if ip else []) if r[0] == ''), None)
        if tot:
            H.append(h[:8] + [num(tot[1]), num(tot[2]), num(tot[3])])
            b = [cnt(x)[0] for x in tot[4:12]]
            BAND.append(b)
            LOW.append(list(cnt(tot[4])))
            mon = [[0, 0] for _ in range(12)]
            for r in ip['rows']:
                if r[0]:
                    mon[TM[r[0].split()[0]]] = [num(r[2]) or 0, num(r[3]) or 0]
            MON.append(mon)
        else:
            failed.append(f"{h[1]} ({hc})")
            H.append(h[:8] + [0, 0, 0]); BAND.append(None); LOW.append(None)
            MON.append([[0, 0] for _ in range(12)])
    # PDx / DRG: เรียงตามลำดับ รพ. ใน master
    for key, out, codes, index in (('top10pdx', PDX, pdxc, pdxi), ('drg', DRG, drgc, drgi)):
        for i, h in enumerate(master):
            s = spcl.get(h[0]) or {}
            t = first_table(s.get(key))
            for r in (t['rows'] if t else []):
                if not r[0]:
                    continue
                if r[0] not in index:
                    index[r[0]] = len(codes); codes.append([r[0], r[1]])
                out.append([i, index[r[0]], num(r[2]), num(r[3]), num(r[5]), num(r[6]), num(r[7])])

    def macro(key, split_code):
        rows = []
        for mm, per in (('mm1', 'ครั้งที่ 1'), ('mm2', 'ครั้งที่ 2')):
            t = first_table(raw['macro'].get(f'{key}_{mm}'))
            for r in (t['rows'] if t else []):
                if not r[0]:
                    continue
                lead = [x.strip() for x in r[0].split(':')] if split_code else [r[0]]
                rows.append([per] + lead + [num(x) for x in r[1:]])
        return rows

    fy = int(raw['year']) + 543
    yy1, yy2 = str(fy - 1)[-2:], str(fy)[-2:]
    lab = {'ครั้งที่ 1': f'ครั้งที่ 1 (ต.ค.{yy1}-มี.ค.{yy2})', 'ครั้งที่ 2': f'ครั้งที่ 2 (ต.ค.{yy1}-ก.ย.{yy2})'}
    fix = lambda rows: [[lab[r[0]]] + r[1:] for r in rows]
    R1B = []
    t = first_table(raw['region'].get('ipd_percentage'))
    for r in (t['rows'] if t else []):
        a, b = ([x.strip() for x in r[0].split(':')] if r[0] else ['รวม', 'เขตสุขภาพที่ 1'])
        v = [num(r[1]), num(r[2]), num(r[3])] + [x for c in r[4:12] for x in cnt(c)]
        R1B.append([a, b] + v + [round(v[2] / v[1], 4) if v[1] else None])

    D.update(H=H, BAND=BAND, LOW=LOW, MON=MON, PDX=PDX, PDXC=pdxc, DRG=DRG, DRGC=drgc,
             R1P=fix(macro('type3', True)), R1S=fix(macro('type1', False)), R1T=fix(macro('type2', False)), R1B=R1B)
    D['meta'].update(fy=fy, run=run, failed=failed)
    D['meta'].pop('empty', None)
    # เรียง key ตามไฟล์เดิม
    return {k: D[k] for k in old if k in D}


def has_data(d):
    return sum(1 for h in d.get('H', []) if h and (h[8] or 0) > 0)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('raw'); ap.add_argument('data_json')
    ap.add_argument('--base', help='ไฟล์ data ที่ใช้เป็น master เมื่อยังไม่มี data_json ของปีนี้ (หรือมีแต่ว่าง)')
    ap.add_argument('--run', default=datetime.now(timezone(timedelta(hours=7))).isoformat(timespec='seconds'))
    a = ap.parse_args()
    raw = json.load(open(a.raw, encoding='utf-8'))
    exists = os.path.exists(a.data_json)
    old = json.load(open(a.data_json, encoding='utf-8')) if exists else None
    if not old or not old.get('H'):
        if not a.base:
            raise SystemExit(f'ไม่มี {a.data_json} และไม่ได้ระบุ --base')
        old = json.load(open(a.base, encoding='utf-8'))
        exists = False
    ok = sum(1 for h in raw['spcl'].values() if h.get('ipd_percentage'))
    new = build(raw, old, a.run)
    fy = new['meta']['fy']

    # กันข้อมูลถอยหลัง: ถ้ารอบนี้ได้ รพ. น้อยกว่าเดิมเกิน 5 แห่ง (เช่นเว็บล่มบางส่วน) ไม่เขียนทับ
    if exists and has_data(new) < has_data(old) - 5:
        print(f'ปีงบ {fy}: ได้ข้อมูล {has_data(new)} รพ. น้อยกว่าเดิม {has_data(old)} — ไม่เขียนทับ')
        raise SystemExit(0)

    # meta.json = วันเวลาที่ดึงข้อมูลสำเร็จล่าสุด แยกรายปีงบ (อัปเดตทุกครั้งที่ดึงได้ แม้ตัวเลขไม่เปลี่ยน)
    mp = os.path.join(os.path.dirname(os.path.abspath(a.data_json)), 'meta.json')
    try:
        allm = json.load(open(mp, encoding='utf-8'))
    except Exception:
        allm = {}
    years = allm.get('years') or {}
    years[str(fy)] = {'run': a.run, 'hospitals_ok': ok, 'failed': new['meta']['failed']}
    latest = max(years, key=int)
    allm.update(years=years, fy=int(latest), run=years[latest]['run'],
                hospitals_ok=years[latest]['hospitals_ok'], failed=years[latest]['failed'])
    with open(mp, 'w', encoding='utf-8') as f:
        json.dump(allm, f, ensure_ascii=False, indent=1)
    print(f'meta.json: ปีงบ {fy} ข้อมูล ณ', a.run)

    strip = lambda d: {**d, 'meta': {**d['meta'], 'run': None}}
    if exists and strip(new) == strip(old) and 'T' in str(old['meta'].get('run', '')):
        print(f'ปีงบ {fy}: ตัวเลขไม่เปลี่ยนจากเดิม — ไม่แก้ {a.data_json}'); raise SystemExit(0)
    with open(a.data_json, 'w', encoding='utf-8') as f:
        json.dump(new, f, ensure_ascii=False, separators=(',', ':'))
    print(f"{a.data_json}: ปีงบ {fy} ข้อมูล ณ {a.run}; ได้ข้อมูล {ok} รพ.; ไม่มีข้อมูล {new['meta']['failed']}")
