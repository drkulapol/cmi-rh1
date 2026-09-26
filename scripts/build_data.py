#!/usr/bin/env python3
"""แปลงข้อมูลดิบจาก fetch_cmi.py -> data.json ของ dashboard cmi-rh1 (รูปแบบเดิมทุก key)

ข้อมูล master ของโรงพยาบาล (ประเภท รพ., ระดับบริการ, เตียง, ปชก.UC, กลุ่ม) ไม่มีบนเว็บ CMI
จึงอ่านจาก data.json เดิม (H[0..7]) แล้วแทนที่เฉพาะตัวเลข CMI

ใช้: python scripts/build_data.py data/cmi_raw_2569.json data.json [--run 2026-09-25]
"""
import argparse, json, re
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
    # เรียง key ตามไฟล์เดิม
    return {k: D[k] for k in old if k in D}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('raw'); ap.add_argument('data_json')
    ap.add_argument('--run', default=datetime.now(timezone(timedelta(hours=7))).isoformat(timespec='seconds'))
    a = ap.parse_args()
    raw = json.load(open(a.raw, encoding='utf-8'))
    old = json.load(open(a.data_json, encoding='utf-8'))
    ok = sum(1 for h in raw['spcl'].values() if h.get('ipd_percentage'))
    new = build(raw, old, a.run)
    # meta.json = วันเวลาที่ดึงข้อมูลสำเร็จล่าสุด (อัปเดตทุกครั้งที่ดึงได้ แม้ตัวเลขไม่เปลี่ยน)
    import os
    mp = os.path.join(os.path.dirname(os.path.abspath(a.data_json)), 'meta.json')
    with open(mp, 'w', encoding='utf-8') as f:
        json.dump({'fy': new['meta']['fy'], 'run': a.run, 'hospitals_ok': ok, 'failed': new['meta']['failed']}, f, ensure_ascii=False, indent=1)
    print('meta.json: ข้อมูล ณ', a.run)
    strip = lambda d: {**d, 'meta': {**d['meta'], 'run': None}}
    if strip(new) == strip(old) and 'T' in str(old['meta'].get('run', '')):
        print('ตัวเลขไม่เปลี่ยนจากเดิม — ไม่แก้ data.json'); raise SystemExit(0)
    with open(a.data_json, 'w', encoding='utf-8') as f:
        json.dump(new, f, ensure_ascii=False, separators=(',', ':'))
    print(f"data.json: ปีงบ {new['meta']['fy']} ข้อมูล ณ {a.run}; ได้ข้อมูล {ok} รพ.; ไม่มีข้อมูล {new['meta']['failed']}")
