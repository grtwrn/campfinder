"""Sweep every state-park campground in reach and report real openings."""

import datetime as dt
import json
import sys
import time

import reserveamerica as ra

RANGES = {
    "fri-sun (2n)": (dt.date(2026, 9, 4), dt.date(2026, 9, 6)),
    "fri-mon (3n)": (dt.date(2026, 9, 4), dt.date(2026, 9, 7)),
    "sat-sun (1n)": (dt.date(2026, 9, 5), dt.date(2026, 9, 6)),
}
GRID_START = dt.date(2026, 9, 4)


def nights(ci, co):
    out, d = [], ci
    while d < co:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def main(states, out_path):
    results = []
    for st in states:
        c = ra.client(st)
        try:
            cgs = c.campgrounds()
        except Exception as exc:
            print(f"{st}: directory failed: {exc}", flush=True)
            continue
        print(f"\n########## {st}: {len(cgs)} facilities", flush=True)
        for i, cg in enumerate(cgs, 1):
            name = cg["name"]
            try:
                grid = c.matrix(cg["park_id"], GRID_START)
            except Exception as exc:
                print(f"  [{i}/{len(cgs)}] {name}: ERROR {exc}", flush=True)
                results.append({**cg, "error": str(exc)})
                continue
            rec = {**cg, "parsed_sites": len(grid["sites"]),
                   "sold_out": grid.get("sold_out", False), "open": {}}
            for label, (ci, co) in RANGES.items():
                ns = nights(ci, co)
                hits = {}
                for site, s in grid["sites"].items():
                    if all(s["nights"].get(n) == "available" for n in ns):
                        hits[site] = {"loop": s["loop"], "site_id": s["site_id"]}
                rec["open"][label] = hits
            results.append(rec)
            tag = " ".join(
                f"{lab.split()[0]}={len(v)}" for lab, v in rec["open"].items()
            )
            hot = any(rec["open"].values())
            print(f"  [{i}/{len(cgs)}] {'*' if hot else ' '} {name[:46]:46} "
                  f"sites={rec['parsed_sites']:>3} {tag}", flush=True)
            time.sleep(0.4)
        json.dump(results, open(out_path, "w"), indent=1)
    json.dump(results, open(out_path, "w"), indent=1)
    print(f"\nwrote {out_path} ({len(results)} facilities)", flush=True)


if __name__ == "__main__":
    main(sys.argv[1].split(","), sys.argv[2])
