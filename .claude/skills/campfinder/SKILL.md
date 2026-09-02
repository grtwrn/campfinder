---
name: campfinder
description: Check real campsite availability across Connecticut, Massachusetts, Rhode Island, New York and Vermont state parks, Campspot/Newbook private campgrounds, and recreation.gov - filtered to sites that actually allow dogs and actually sit on the water, with live cyanobacteria status. Use when asked to find an open campsite, check whether a campground is booked, watch for cancellations, or answer "is anything available on <dates>".
---

# campfinder

Finds campsites that are genuinely bookable, by reading the reservation
systems rather than marketing pages. Everything it reports was seen in a live
booking calendar.

## Running it

```bash
python3 cli.py check --checkin 2026-09-04 --nights 3 --dogs --private
```

`--nights` counts nights: Friday→Monday is `--nights 3`.

| Flag | Effect |
|------|--------|
| `--dogs` | drop sites where pets are banned (enforces agency rules, not just an explicit "N") |
| `--private` | add Campspot + Newbook private campgrounds (`--radius N` miles) |
| `--lakes-only` | only lake campgrounds from `registry.json` (~16 facilities, minutes not half an hour) |
| `--waterfront-only` | only sites flagged waterfront in the booking record, or known-waterfront in the registry |
| `--federal` | add recreation.gov |
| `--no-state` | skip state parks |
| `--states CT,MA,RI,NY,VT` | limit the systems scanned |
| `--json out.json` | machine-readable output |

Cancellation watching — prints only newly-opened sites:

```bash
python3 cli.py watch --checkin 2026-09-04 --nights 3 --dogs --private \
    --lakes-only --every 15m --notify 'notify-send "campsite open"'
```

A full five-state pass takes longer than a 15-minute interval, so use
`--lakes-only` or `--no-state` for watch loops.

## Things that will produce wrong answers if you forget them

1. **Holiday minimums.** Most private parks enforce a 3-night minimum on
   Labor Day / Memorial Day / July 4. A 2-night Fri→Sun query returns nothing
   while Fri→Mon returns dozens of sites at the same campground. **Always try
   both `--nights 2` and `--nights 3`.**

2. **Availability counts lie.** Campgrounds list day-use pavilions, group
   areas, and equestrian sites alongside real campsites. On Labor Day 2026,
   Myles Standish showed 12 "available" sites that were all
   `HORSE REQUIRED TO CAMP`, and every Mount Greylock opening was a group
   area. `is_real_campsite()` filters these; don't bypass it.

3. **A blank pet field is not permission.** Connecticut bans pets at *every*
   state park campground (only four state *forest* campgrounds allow them),
   and Rhode Island bans them at East Beach and Charlestown Breachway - all of
   which leave `Pets Allowed` empty rather than setting it to "N". The rules
   live in `registry.json` under `pet_policy` and `pets_banned_park_ids`.

4. **"Not monitored" is not "clean."** Only Vermont publishes a live
   cyanobacteria feed. A lake with no water-quality line simply isn't sampled.

5. **Openings evaporate.** A verified opening was booked by someone else
   within three hours on a holiday weekend. Re-check before telling anyone to
   drive somewhere.

## Extending it

Add campgrounds, lakes, waterfront ratings, drive times and pet rules to
`registry.json` rather than editing code. `README.md` documents every endpoint
and the three parsing traps (session bleed, pagination, fake openings) in case
a site changes.

## Scope

Built around New Haven, CT (set `home` in `registry.json`). Coverage is
strongest in southern New England; recreation.gov has almost no inventory
there, so state parks and private campgrounds carry the search.

Be considerate: this hits real booking systems. Keep scans occasional, keep
watch intervals sane, and read the note at the top of `README.md` about each
site's terms before pointing it at anything at volume.
