#!/usr/bin/env python3
"""ดึงข้อมูล CMI เขตสุขภาพที่ 1 จาก cmi.moph.go.th อัตโนมัติ

ผลลัพธ์ (ในโฟลเดอร์ data/):
  cmi_raw_<ปีงบ>.json      ข้อมูลดิบ (โครงสร้างเดียวกับ cmi_region1_2569_raw.json)
  CMI_เขต1_ปีงบ<ปีงบ>.xlsx  Excel สำหรับ dashboard
  meta.json                 วันเวลาที่ดึง + สถานะแต่ละ รพ.

ใช้:  python scripts/fetch_cmi.py            (ปีงบปัจจุบันอัตโนมัติ)
      python scripts/fetch_cmi.py --fy 2568  (ระบุปีงบ พ.ศ.)
      python scripts/fetch_cmi.py --probe    (ทดสอบว่าเข้าเว็บได้ไหม แล้วจบ)
"""
import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

BASE = "https://cmi.moph.go.th"
TH = timezone(timedelta(hours=7))
CODES = """10674 11189 11190 11191 11192 11193 11194 11195 11196 11197 11198 11199 11200 11201 11202 11454 15012 28823
06009 10713 11119 11120 11121 11122 11123 11124 11125 11126 11127 11128 11129 11130 11131 11132 11133 11134 11135 11136
11137 11138 11139 11643 23736 10716 11173 11174 11175 11176 11177 11178 11179 11180 11181 11182 11183 11453 11625 25017
10717 10718 11184 11185 11186 11187 11188 40744 40745 10715 11166 11167 11169 11170 11171 11172 11452 10719 11203 11204
11205 11206 11207 11208 10672 11146 11147 11148 11149 11150 11151 11152 11153 11154 11155 11156 11157 10714 11140 11141
11142 11143 11144 11145 24956""".split()
CHW = ["50", "51", "52", "54", "55", "56", "57", "58"]
SPCL_EPS = ["top10pdx", "drg", "ipd_percentage"]  # menu_id 15, 18, 16
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Client:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.csrf = {}

    def token(self, section):  # section = 'spcl' | 'macro'
        if section not in self.csrf:
            menu = "15" if section == "spcl" else "1"
            r = self.s.get(f"{BASE}/report/{section}/index", params={"menu_id": menu}, timeout=60)
            r.raise_for_status()
            tag = BeautifulSoup(r.text, "lxml").select_one("input[name=_csrf]")
            if not tag:
                raise RuntimeError("ไม่พบ _csrf ในหน้าเว็บ (โครงสร้างเว็บอาจเปลี่ยน)")
            self.csrf[section] = tag["value"]
        return self.csrf[section]

    def post(self, section, ep, params, tries=3):
        tok = self.token(section)
        last = None
        for a in range(tries):
            try:
                r = self.s.post(f"{BASE}/report/{section}/{ep}", data={**params, "_csrf": tok},
                                headers={"X-CSRF-Token": tok, "X-Requested-With": "XMLHttpRequest"},
                                timeout=180)
                if r.status_code == 200:
                    return r.text
                last = f"HTTP {r.status_code}"
                if r.status_code == 500:  # เว็บตอบ 500 ถาวรสำหรับบาง รพ. (เช่น 40744) ไม่ต้องลองซ้ำนาน
                    break
            except requests.RequestException as e:
                last = str(e)
            time.sleep(2 * (a + 1))
        print(f"  ! {section}/{ep} {params.get('hcode','')}: {last}", file=sys.stderr)
        return None


def parse_tables(html):
    if html is None:
        return None
    soup = BeautifulSoup(html, "lxml")
    out = []
    for t in soup.find_all("table"):
        hdr = [[[c.get_text(strip=True), int(c.get("colspan", 1))] for c in tr.find_all(["th", "td"])]
               for tr in t.select("thead tr")]
        rows = [[c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                for tr in t.select("tbody tr, tfoot tr")]
        out.append({"hdr": hdr, "rows": rows})
    return out


def current_fy_be(now):
    return now.year + 543 + (1 if now.month >= 10 else 0)


def fetch(fy_be, workers=4):
    year = str(fy_be - 543)  # เว็บใช้ปีงบแบบ ค.ศ. เช่น 2026 = 2569
    cl = Client()
    C = {"year": year, "hosp": {}, "codes": CODES, "macro": {}, "spcl": {}, "region": {}, "err": []}

    for chw in CHW:
        h = cl.post("spcl", "hosplist", {"region": "1", "chwcode": chw}) or ""
        for o in BeautifulSoup(f"<select>{h}</select>", "lxml").find_all("option"):
            if o.get("value"):
                C["hosp"][o["value"]] = {"name": o.get_text(strip=True), "chw": chw}
    print(f"hosplist: {len(C['hosp'])} รพ.")

    for t in "123":
        for m in "12":
            C["macro"][f"type{t}_mm{m}"] = parse_tables(cl.post("macro", "summaryall", {
                "year": year, "region": "1", "chwcode": "", "rdotype": t, "rdomm": m}))
    for ep in SPCL_EPS:
        C["region"][ep] = parse_tables(cl.post("spcl", ep, {
            "year": year, "region": "1", "chwcode": "", "hcode": "", "hosptype": "", "servplan": "", "rdorpt": "1"}))

    def one(hc):
        o = {}
        for ep in SPCL_EPS:
            o[ep] = parse_tables(cl.post("spcl", ep, {
                "year": year, "region": "", "chwcode": "", "hcode": hc, "hosptype": "", "servplan": "", "rdorpt": "1"}))
            if o[ep] is None:
                C["err"].append(f"{hc}:{ep}")
        return hc, o

    with ThreadPoolExecutor(workers) as ex:
        for i, (hc, o) in enumerate(ex.map(one, CODES), 1):
            C["spcl"][hc] = o
            if i % 20 == 0:
                print(f"  {i}/{len(CODES)}")
    return C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fy", type=int, help="ปีงบประมาณ พ.ศ. (ค่าเริ่มต้น = ปีงบปัจจุบัน)")
    ap.add_argument("--out", default="data")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--no-excel", action="store_true", help="ไม่สร้างไฟล์ Excel")
    ap.add_argument("--min-ok", type=int, default=95, help="จำนวน รพ. ขั้นต่ำที่ต้องได้ข้อมูล มิฉะนั้นไม่เขียนไฟล์")
    a = ap.parse_args()
    now = datetime.now(TH)

    if a.probe:
        cl = Client()
        tok = cl.token("spcl")
        h = cl.post("spcl", "hosplist", {"region": "1", "chwcode": "55"})
        n = len(re.findall(r"<option value='\d", h or ""))
        print(f"PROBE: csrf={'ok' if tok else 'missing'} hosplist(น่าน)={n} รพ.")
        sys.exit(0 if n > 0 else 1)

    fy = a.fy or current_fy_be(now)
    t0 = time.time()
    C = fetch(fy)
    ok = [h for h, v in C["spcl"].items() if v.get("ipd_percentage")]
    print(f"ปีงบ {fy}: ได้ข้อมูล {len(ok)}/{len(CODES)} รพ. ใน {time.time()-t0:.0f} วินาที; error: {C['err']}")
    if len(ok) < a.min_ok:
        print("ข้อมูลไม่ครบตามเกณฑ์ — ไม่เขียนทับไฟล์เดิม", file=sys.stderr)
        sys.exit(2)

    os.makedirs(a.out, exist_ok=True)
    C["fetched_at"] = now.isoformat(timespec="seconds")
    raw = os.path.join(a.out, f"cmi_raw_{fy}.json")
    with open(raw, "w", encoding="utf-8") as f:
        json.dump(C, f, ensure_ascii=False, separators=(",", ":"))

    xlsx = None
    if not a.no_excel:
        from build_excel import build
        xlsx = os.path.join(a.out, f"CMI_เขต1_ปีงบ{fy}.xlsx")
        build(C, xlsx, fy, now)

    meta = {"fy": fy, "fetched_at": C["fetched_at"], "hospitals_ok": len(ok),
            "hospitals_total": len(CODES), "errors": C["err"],
            "files": {"raw": os.path.basename(raw), "xlsx": os.path.basename(xlsx) if xlsx else None}}
    # meta.json เก็บทุกปีงบ: {"latest_fy": ..., "years": {"2569": {...}}}
    mp = os.path.join(a.out, "meta.json")
    try:
        allm = json.load(open(mp, encoding="utf-8"))
    except Exception:
        allm = {"years": {}}
    allm.setdefault("years", {})[str(fy)] = meta
    allm["latest_fy"] = max(int(y) for y in allm["years"])
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(allm, f, ensure_ascii=False, indent=1)
    print("เขียนไฟล์:", raw, xlsx or "")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
