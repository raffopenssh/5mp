# Bug Fix: KML Export XML Escaping

## Issue

KML exports were generating invalid XML when place names contained special characters like `&`.

**Error message:**
```
Parse error at line 4183, column 28: not well-formed (invalid token)
```

**Example problematic names:**
- `Mormoto 1 & 2 (village)`
- `Eliba I & II`
- `Kainama II & III`

## Root Cause

The KML export functions `writeGeoJSONToKML()` and `writeGeoJSONToKMLWithDesc()` in `srv/api.go` were writing placemark names directly to the XML output without escaping special characters.

XML requires these characters to be escaped:
- `&` → `&amp;`
- `<` → `&lt;`
- `>` → `&gt;`
- `"` → `&quot;`
- `'` → `&apos;`

## Fix

Added `xmlEscape()` helper function and applied it to all placemark names:

```go
func xmlEscape(s string) string {
    s = strings.ReplaceAll(s, "&", "&amp;")
    s = strings.ReplaceAll(s, "<", "&lt;")
    s = strings.ReplaceAll(s, ">", "&gt;")
    s = strings.ReplaceAll(s, "\"", "&quot;")
    s = strings.ReplaceAll(s, "'", "&apos;")
    return s
}
```

### Changes Made

1. **`writeGeoJSONToKML()`** - Escape placemark names
2. **`writeGeoJSONToKMLWithDesc()`** - Escape placemark names  
3. **`HandleAPIParkKML()`** - Escape park names in document/folder titles
4. **`HandleAPIMergedKML()`** - Escape park names in merged exports

## Verification

**Before fix:**
```xml
<Placemark><name>Mormoto 1 & 2 (village)</name>...
```
❌ Parse error: "xmlParseEntityRef: no name"

**After fix:**
```xml
<Placemark><name>Mormoto 1 &amp; 2 (village)</name>...
```
✅ Valid XML

## Testing

```bash
# Test single park KML
curl -s "http://localhost:8000/api/parks/TCD_Aouk/export.kml?pwd=test2026" > test.kml
xmllint --noout test.kml  # Should pass with no errors

# Test merged multi-park KML
curl -s "http://localhost:8000/api/export/merged.kml?parks=TCD_Aouk,COD_Kahuzi-Biega&pwd=test2026" > merged.kml
xmllint --noout merged.kml  # Should pass with no errors
```

## Impact

- **Affected endpoints:**
  - `GET /api/parks/{id}/export.kml`
  - `GET /api/export/merged.kml`
  - Star report KML exports (frontend calls these endpoints)

- **Data sources with special characters:**
  - OSM place names (villages, towns, cities)
  - Park names
  - Settlement names
  - Road/river names
  - Any user-provided names

## Commit

```
commit a8a9be9c
Fix: Escape XML special characters in KML export names
```
