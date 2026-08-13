package srv

import "fmt"

// PROVENANCE, part two: a settlement's AREA and its POPULATION each mean two
// different things depending on which pipeline wrote the row, and until
// 2026-08-13 nothing said which.
//
// GHS_BUILT_S is a *fractional* surface raster: a 100 m pixel holding 60 m² of
// building is one whole pixel of the binary mask. scripts/ghsl_tiles.py
// vectorised that mask and stored the polygon's area as `area_m2`, so the
// column held the ground a settlement's mask covers rather than the surface
// built on it — 6,798 km² against 181 km² over XSA_Study_Area. `population_est`
// was then `area_m2/1e4 * 200`, i.e. a density constant applied to a 24×
// overstatement: 85,360,922 people in one AOI and a single "town" of 61.7
// million (docs/AOI_STRUCTURAL_FIXES.md F1/F2).
//
// The fix is a pipeline change, and a pipeline change converts rows one park at
// a time. That is the whole reason these are columns and not a cutover date:
// while the backfill runs, `area_m2` means SURFACE in the parks it has reached
// and EXTENT in the parks it has not, and only the row can say which.
//
//	area_source        'ghsl_built_s_surface' | 'ghsl_mask_extent' | …
//	population_source  a GHS_POP product id   | 'legacy_density_200_per_ha' | …
//
// The rule these helpers enforce is AGENTS.md invariant 12 in a second table:
// **an unmeasured quantity must read as unmeasured, not as a number.** A
// legacy population is not a worse measurement, it is not a measurement, so it
// is served as NULL/absent rather than as a figure a reader would cite. The
// extent survives under its own name because it was always honest — it was only
// ever labelled wrong.
//
// Deliberately NOT a narrative prefix (invariant 5): the nightly reclassify
// rewrites `narrative`, and a flag some other job rewrites is not a flag.

// settlementPopulationLegacy is the marker migration 055 stamps on every row
// whose population came from the 200 people/ha constant.
const settlementPopulationLegacy = "legacy_density_200_per_ha"

// settlementAreaLegacy marks a row whose area_m2 is mask extent, not surface.
const settlementAreaLegacy = "ghsl_mask_extent"

// settlementPopulationSQL is a SELECT expression yielding the population when it
// was measured and NULL when it was not. `alias` is "" or a table alias with no
// trailing dot ("s").
//
// Use it EVERYWHERE population_est is read, including inside SUM(): a total
// that silently drops the unmeasured rows is a smaller lie in the same shape,
// so a caller wanting a total must also ask how many rows had no number
// (settlementPopulationMeasuredSQL).
func settlementPopulationSQL(alias string) string {
	p := colPrefix(alias)
	return fmt.Sprintf("CASE WHEN COALESCE(%[1]spopulation_source,'') NOT IN ('', %[2]s) "+
		"THEN %[1]spopulation_est END", p, sqlQuote(settlementPopulationLegacy))
}

// settlementPopulationMeasuredSQL counts the rows whose population is real, so
// a caller can say "1,140 of 1,552 settlements" instead of presenting a partial
// sum as a total.
func settlementPopulationMeasuredSQL(alias string) string {
	p := colPrefix(alias)
	return fmt.Sprintf("SUM(CASE WHEN COALESCE(%[1]spopulation_source,'') NOT IN ('', %[2]s) "+
		"THEN 1 ELSE 0 END)", p, sqlQuote(settlementPopulationLegacy))
}

// settlementSurfaceSQL is the built-up SURFACE in m², or NULL where the row
// still holds mask extent.
func settlementSurfaceSQL(alias string) string {
	p := colPrefix(alias)
	return fmt.Sprintf("CASE WHEN COALESCE(%[1]sarea_source,'') NOT IN ('', %[2]s) "+
		"THEN %[1]sarea_m2 END", p, sqlQuote(settlementAreaLegacy))
}

// settlementExtentSQL is the mask footprint in m², which every row has under
// one column name or the other.
func settlementExtentSQL(alias string) string {
	p := colPrefix(alias)
	return fmt.Sprintf("COALESCE(%[1]sextent_m2, %[1]sarea_m2)", p)
}

func colPrefix(alias string) string {
	if alias == "" {
		return ""
	}
	return alias + "."
}

// sqlQuote is for the two package constants above only — never for user input.
func sqlQuote(s string) string {
	return "'" + s + "'"
}
