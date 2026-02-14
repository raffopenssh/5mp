#!/usr/bin/env python3
"""
Compute GADM level 1 and 2 regions that intersect with park boundaries.

This gives us province and district names for each park, which vastly
improves publication and legal document search relevance.

Output: data/park_gadm_regions.json
"""

import json
from pathlib import Path

def bbox_intersects(bbox1, bbox2):
    """Check if two bboxes [minX, minY, maxX, maxY] intersect."""
    if bbox1 is None or bbox2 is None:
        return False
    return not (bbox1[2] < bbox2[0] or bbox1[0] > bbox2[2] or
                bbox1[3] < bbox2[1] or bbox1[1] > bbox2[3])

def compute_bbox_from_coords(geometry):
    """Compute bounding box from GeoJSON geometry."""
    coords = geometry.get("coordinates", [])
    geom_type = geometry.get("type", "")
    
    all_coords = []
    if geom_type == "Polygon":
        if coords and coords[0]:
            all_coords = coords[0]
    elif geom_type == "MultiPolygon":
        for poly in coords:
            if poly and poly[0]:
                all_coords.extend(poly[0])
    
    if not all_coords:
        return None
    
    lons = [c[0] for c in all_coords]
    lats = [c[1] for c in all_coords]
    return [min(lons), min(lats), max(lons), max(lats)]

def load_parks():
    """Load park data with boundaries."""
    path = Path("data/keystones_with_boundaries.json")
    if not path.exists():
        print(f"Error: {path} not found")
        return {}
    
    with open(path) as f:
        parks = json.load(f)
    
    result = {}
    for park in parks:
        park_id = park.get("id")
        if not park_id:
            continue
        
        country = park_id.split("_")[0] if "_" in park_id else ""
        geometry = park.get("geometry")
        bbox = compute_bbox_from_coords(geometry) if geometry else None
        
        if bbox:
            result[park_id] = {
                "name": park.get("name", ""),
                "country": country,
                "bbox": bbox
            }
    
    return result

def load_gadm_regions(level):
    """Load GADM regions at specified level."""
    if level == 1:
        path = Path("data/gadm_africa.json")
    else:
        path = Path(f"data/gadm_africa_level{level}.json")
    
    if not path.exists():
        return []
    
    with open(path) as f:
        data = json.load(f)
    
    return data.get("regions", [])

def main():
    print("Loading park boundaries...")
    parks = load_parks()
    print(f"Loaded {len(parks)} parks with boundaries")
    
    print("Loading GADM level 1 regions...")
    level1_regions = load_gadm_regions(1)
    print(f"Loaded {len(level1_regions)} level 1 regions")
    
    print("Loading GADM level 2 regions...")
    level2_regions = load_gadm_regions(2)
    print(f"Loaded {len(level2_regions)} level 2 regions")
    
    # Index regions by country
    l1_by_country = {}
    for region in level1_regions:
        country = region.get("country_code", "")
        if country not in l1_by_country:
            l1_by_country[country] = []
        l1_by_country[country].append(region)
    
    l2_by_country = {}
    for region in level2_regions:
        country = region.get("country_code", "")
        if country not in l2_by_country:
            l2_by_country[country] = []
        l2_by_country[country].append(region)
    
    print("Computing intersections...")
    result = {}
    
    for park_id, park_data in parks.items():
        country = park_data["country"]
        park_bbox = park_data["bbox"]
        
        if not country or not park_bbox:
            continue
        
        # Level 1 intersections
        l1_names = []
        l1_ids = []
        for region in l1_by_country.get(country, []):
            region_bbox = region.get("bbox")
            if region_bbox and bbox_intersects(park_bbox, region_bbox):
                l1_names.append(region.get("name", ""))
                l1_ids.append(region.get("id", ""))
        
        # Level 2 intersections
        l2_names = []
        l2_ids = []
        l2_varnames = []
        for region in l2_by_country.get(country, []):
            region_bbox = region.get("bbox")
            if region_bbox and bbox_intersects(park_bbox, region_bbox):
                l2_names.append(region.get("name", ""))
                l2_ids.append(region.get("id", ""))
                # Include variant names (important for search!)
                varnames = region.get("varnames", [])
                l2_varnames.extend(varnames)
        
        result[park_id] = {
            "country_iso": country,
            "level1_regions": l1_names,
            "level1_ids": l1_ids,
            "level2_regions": l2_names,
            "level2_ids": l2_ids,
            "level2_varnames": list(set(l2_varnames))  # Unique variant names
        }
    
    # Write output
    output_path = Path("data/park_gadm_regions.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    
    print(f"Wrote {len(result)} park-region mappings to {output_path}")
    
    # Print examples
    print("\nExample mappings (showing level 2 districts):")
    examples = ["TCD_Zakouma", "COD_Virunga", "KEN_Tsavo_East", "TZA_Serengeti"]
    for park_id in examples:
        if park_id in result:
            data = result[park_id]
            l1_str = ', '.join(data['level1_regions'][:3])
            l2_str = ', '.join(data['level2_regions'][:5])
            print(f"\n  {park_id}:")
            print(f"    Level 1 (provinces): {l1_str}")
            print(f"    Level 2 (districts): {l2_str}")
    
    # Stats
    parks_with_l2 = sum(1 for p in result.values() if p['level2_regions'])
    total_l2 = sum(len(p['level2_regions']) for p in result.values())
    print(f"\nStats: {parks_with_l2} parks have level 2 mappings, {total_l2} total park-district pairs")

if __name__ == "__main__":
    main()
