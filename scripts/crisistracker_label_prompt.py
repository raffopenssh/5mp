#!/usr/bin/env python3
"""Build the LLM extraction prompt for Crisis Tracker mine-related incidents.

WHY AN LLM AT ALL
-----------------
The commodity is only ever in the prose. "the Kpangou gold and diamond mine",
"they took ... some diamonds and gold", "a mining site in M'bres" -- a keyword
match cannot tell the first (a gold+diamond WORKING, useful as a geology truth
point) from the second (loot carried from somewhere else, useless) from the
third (a mine with no commodity named). That distinction is the whole value of
this source for eval_affinity, so it is worth a language model and worth
committing the labels as data.

The model is asked for extraction, never for judgement: what does this sentence
SAY, with a verbatim span for each claim. Anything it cannot ground in the text
is "unknown", which is a different answer from "none".

Input:  data/crisistracker/details/*.json  (scripts/crisistracker_fetch.py)
Output: data/crisistracker/label_prompt.txt

Then, once (results are committed, so this is not re-run casually):
    llm_one_shot --model gpt-5.6-sol \
        data/crisistracker/label_prompt.txt \
        -> data/eval/crisistracker/note_labels.json
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DET = ROOT / "data" / "crisistracker" / "details"
OUT = ROOT / "data" / "crisistracker" / "label_prompt.txt"

INSTRUCTIONS = """\
You are extracting structured facts from incident reports in the Crisis Tracker
database (Invisible Children), which records armed-group activity in the border
region of the Central African Republic, DR Congo, South Sudan and Sudan.

We are building a reference list of ARTISANAL AND SMALL-SCALE MINING SITES to
test a geological model. We need to know, for each report, whether it places a
mine at a location, and which raw material that mine produces.

For EVERY record below, output one JSON object. Output a JSON array, nothing
else -- no prose, no markdown fence.

Fields:

  "id":        the record id, copied exactly.

  "mine_site": one of
      "at_mine"    -- the incident happened AT a mine, mining site, mining
                      camp, quarry, dredging site, orpaillage/chantier site.
      "mine_named" -- a specific mine or mining site is named or located in the
                      text, but the incident happened elsewhere (e.g. "near the
                      Mayeka mine", "on the road from the X mine").
      "miners"     -- the victims are miners or a mining settlement, but no
                      mine location is given.
      "no"         -- no mine is placed by this text. Loot that happens to
                      include gold or diamonds, mineral trade, or a market is
                      NOT a mine. A place name that merely contains a word like
                      "Mine" is not enough on its own.

  "commodities": array from this closed list, ONLY when the text says this mine
      or these miners produce/extract/work it:
        "gold", "diamond", "coltan", "cassiterite", "tin", "tungsten",
        "copper", "iron", "cobalt", "manganese", "chromite", "salt",
        "sand_gravel", "stone", "other"
      Use [] when no material is named. Do NOT infer a commodity from the
      region, the country, or from what was looted. "or" is French for gold and
      "orpaillage" is artisanal gold panning; "diamant" is diamond. If the text
      names a material we do not list, use "other" and put the word in
      "commodity_note".

  "commodity_evidence": the verbatim substring of the note that names the
      commodity, or null. It must appear character-for-character in the note.

  "commodity_source": one of
      "extracted"  -- the text says the mine yields it (a "gold mine", people
                      "digging for diamonds", "orpaillage").
      "looted"     -- gold/diamonds appear only as goods taken, carried, sold
                      or traded. Still record the commodity, but say "looted":
                      it is evidence about a supply chain, not about the rock
                      under that pin.
      "none"       -- no commodity named.

  "site_name": the name of the mine or mining site as written, or null. Not the
      nearest town, unless the mine is identified only by that town's name
      ("the mining site in M'bres" -> "M'bres").

  "location_is_mine": true only if the text indicates the incident's own
      location IS the mine (so the coordinates, if any, point at the mine).
      false if the report locates the event relative to a town, a road, or an
      unspecified distance from the mine.

  "confidence": 0.0-1.0, your confidence in "mine_site" and "commodities".

Read only what is written. Never use knowledge about the region to fill a
field. An empty answer is correct when the text is silent.

RECORDS
"""


def main():
    recs = []
    for p in sorted(DET.glob("*.json"), key=lambda p: int(p.stem)):
        d = json.loads(p.read_text())
        recs.append({
            "id": d["id"],
            "note": d.get("public_display_note") or "",
            # structured context the coder assigned, which the model may use as
            # corroboration but not as a substitute for the text
            "coded_location": d.get("location_specifics"),
            "coded_livelihood": d.get("livelihood_activity_at_time_of_incident"),
            "other_looting_types": d.get("other_looting_types"),
            "goods_looted": d.get("goods_looted_property_destroyed"),
            "nearest_community_country": d.get("community_country"),
        })
    body = "\n".join(json.dumps({k: v for k, v in r.items() if v}, ensure_ascii=False)
                     for r in recs)
    OUT.write_text(INSTRUCTIONS + body + "\n")
    print(f"{len(recs)} records -> {OUT}")


if __name__ == "__main__":
    main()
