#!/usr/bin/env python3
"""Build the LLM extraction prompt for UCDP GED mine-related events.

Same instrument as scripts/crisistracker_label_prompt.py, same reasons
(docs/agents/acled.md "The commodity is only ever in the prose"): a keyword
match cannot tell "the Ndassima gold mine" (a working -- geology evidence)
from "fled with gold" (loot -- a supply chain) from "a mining site near
Bria" (a working, no commodity). The model is asked only for extraction --
what the sentence SAYS, with a verbatim span for every commodity claim --
and scripts/ucdp_mines.py verifies each span character-for-character.

GED's prose differs from Crisis Tracker's in one way that matters: the
mine reference is usually in `where_description` (UCDP's own gazetteer
note, e.g. "Bambu Locality (mining town of Bambou ...)"), while
`source_article` is often just a citation string. Both are given to the
model; the span check searches both.

Input:  data/ucdp/ged_mining_candidates.json  (scripts/ucdp_fetch.py)
Output: data/ucdp/label_prompt.txt

Then, once (results are committed, so this is not re-run casually):
    llm_one_shot --model gpt-5.6-sol \
        data/ucdp/label_prompt.txt \
        -> data/eval/ucdp/event_labels.json
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN = ROOT / "data" / "ucdp" / "ged_mining_candidates.json"
OUT = ROOT / "data" / "ucdp" / "label_prompt.txt"

INSTRUCTIONS = """\
You are extracting structured facts from event records in the UCDP
Georeferenced Event Dataset (GED), which records organised-violence events in
the Central African Republic, DR Congo, Sudan, South Sudan and Tanzania. Each
record has UCDP's location description (where_description), a source citation
or headline, and coordinates for the event.

We are building a reference list of MINING SITES to test a geological model.
We need to know, for each record, whether its text places a mine at a
location, and which raw material that mine produces.

For EVERY record below, output one JSON object. Output a JSON array, nothing
else -- no prose, no markdown fence.

Fields:

  "id":        the record id, copied exactly.

  "mine_site": one of
      "at_mine"    -- the event happened AT a mine, mining site, mining camp,
                      quarry, dredging site, gold field, orpaillage/chantier
                      site, or in a settlement the text itself calls a mining
                      town/mining area.
      "mine_named" -- a specific mine, mining site or gold field is named or
                      located in the text, but the event happened elsewhere
                      ("near the Ndassima mine", "on the road to the X mine").
      "miners"     -- the victims or actors are miners, but no mine location
                      is given.
      "no"         -- no mine is placed by this text. Loot that happens to
                      include gold or diamonds, mineral trade or taxation, a
                      market, or a place name that merely contains a word like
                      "Mine"/"Diamond" is NOT a mine.

  "commodities": array from this closed list, ONLY when the text says this
      mine or these miners produce/extract/work it:
        "gold", "diamond", "coltan", "cassiterite", "tin", "tungsten",
        "copper", "iron", "cobalt", "manganese", "chromite", "salt",
        "sand_gravel", "stone", "other"
      Use [] when no material is named. Do NOT infer a commodity from the
      region, the country, the conflict's name, or from what was looted.
      "or" is French for gold and "orpaillage" is artisanal gold panning;
      "diamant" is diamond. If the text names a material we do not list, use
      "other" and put the word in "commodity_note".

  "commodity_evidence": the verbatim substring of the record's text that names
      the commodity, or null. It must appear character-for-character in
      where_description, source_headline or source_article.

  "commodity_source": one of
      "extracted"  -- the text says the mine yields it (a "gold mine", people
                      "digging for diamonds", a "gold field").
      "looted"     -- gold/diamonds appear only as goods taken, carried, sold,
                      taxed or traded. Still record the commodity, but say
                      "looted": it is evidence about a supply chain, not about
                      the rock under that pin.
      "none"       -- no commodity named.

  "site_name": the name of the mine or mining site as written, or null. Not
      the nearest town, unless the mine is identified only by that town's name
      ("the mining town of Bambou" -> "Bambou").

  "location_is_mine": true only if the text indicates the event's own
      location IS the mine or the mining settlement (so the record's
      coordinates point at it). false if the event is located relative to a
      town, a road, or an unspecified distance from the mine.

  "confidence": 0.0-1.0, your confidence in "mine_site" and "commodities".

Read only what is written. Never use knowledge about the region to fill a
field. An empty answer is correct when the text is silent.

RECORDS
"""


def main():
    doc = json.loads(IN.read_text())
    recs = []
    for r in doc["rows"]:
        recs.append({
            "id": r["id"],
            "where_description": r.get("where_description") or None,
            "source_headline": r.get("source_headline") or None,
            "source_article": r.get("source_article") or None,
        })
    body = "\n".join(
        json.dumps({k: v for k, v in r.items() if v}, ensure_ascii=False)
        for r in recs)
    OUT.write_text(INSTRUCTIONS + body + "\n")
    print(f"{len(recs)} records -> {OUT}")


if __name__ == "__main__":
    main()
