#!/usr/bin/env python3
"""
Sync research publications from OpenAlex API for all keystone protected areas.

Runs via cron, fetches new publications, and creates notifications for new ones.

Usage:
    python scripts/sync_publications.py --all
    python scripts/sync_publications.py --park CAF_Chinko
    python scripts/sync_publications.py --park CAF_Chinko --notify
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Setup
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# OpenAlex API
OPENALEX_BASE = "https://api.openalex.org/works"
OPENALEX_API_KEY = "o6W24VcVQJHk3OJlNfDjQv"
USER_AGENT = "5mp-conservation-app/1.0 (mailto:research@5mp.org)"

# Country translations for better search
COUNTRY_TRANSLATIONS = {
    "Democratic Republic of the Congo": ["République démocratique du Congo", "DRC", "RDC", "Congo-Kinshasa"],
    "Republic of the Congo": ["République du Congo", "Congo-Brazzaville"],
    "Central African Republic": ["République centrafricaine", "Centrafrique", "RCA"],
    "Ivory Coast": ["Côte d'Ivoire"],
    "Cameroon": ["Cameroun"],
    "Tanzania": ["Tanzanie"],
    "Kenya": ["Kenya"],
    "Uganda": ["Ouganda"],
    "Ethiopia": ["Éthiopie"],
    "South Africa": ["Afrique du Sud"],
}


def load_parks() -> List[Dict]:
    """Load park data from keystones file."""
    with open(KEYSTONES_PATH) as f:
        return json.load(f)


def reconstruct_abstract(inverted_index: Dict) -> str:
    """Reconstruct abstract from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    
    max_pos = 0
    for positions in inverted_index.values():
        for pos in positions:
            if pos > max_pos:
                max_pos = pos
    
    words = [""] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    
    abstract = " ".join(words)
    if len(abstract) > 1000:
        abstract = abstract[:1000] + "..."
    return abstract


def fetch_publications(park_name: str, country: str = "") -> List[Dict]:
    """Fetch publications from OpenAlex for a park."""
    # Search with quoted park name
    search_query = f'"{park_name}"'
    
    params = {
        "search": search_query,
        "filter": "type:article",
        "per_page": "100",
        "sort": "publication_date:desc"
    }
    
    # Add API key to params
    params["api_key"] = OPENALEX_API_KEY
    url = f"{OPENALEX_BASE}?{urllib.parse.urlencode(params)}"
    
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    
    # Retry with exponential backoff on rate limit
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                return data.get("results", [])
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limited
                wait_time = (2 ** attempt) * 5  # 5s, 10s, 20s
                logger.warning(f"Rate limited, waiting {wait_time}s (attempt {attempt + 1}/3)")
                time.sleep(wait_time)
                continue
            logger.error(f"Failed to fetch from OpenAlex: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to fetch from OpenAlex: {e}")
            return []
    
    logger.error("Max retries exceeded for OpenAlex")
    return []


# Conservation/ecology keywords to help filter relevant papers
CONSERVATION_KEYWORDS = [
    "conservation", "wildlife", "biodiversity", "ecology", "species",
    "protected area", "national park", "reserve", "habitat", "ecosystem",
    "mammal", "bird", "reptile", "amphibian", "fish", "plant",
    "forest", "savanna", "wetland", "river", "lake",
    "poaching", "hunting", "deforestation", "land use",
    "population", "survey", "monitoring", "camera trap",
    "elephant", "lion", "leopard", "chimpanzee", "gorilla", "buffalo",
    "antelope", "eland", "giraffe", "hippo", "rhino", "zebra",
    "primate", "carnivore", "ungulate", "herbivore",
    "africa", "african", "tropical", "savannah",
    "ranger", "patrol", "management", "tourism",
]


def filter_relevant_publications(works: List[Dict], park_name: str, country: str = "") -> List[Dict]:
    """Filter to only publications that are conservation-related and mention the park."""
    relevant = []
    park_lower = park_name.lower()
    country_lower = country.lower() if country else ""
    
    # Also try shortened versions
    short_names = [park_lower]
    for suffix in [" national park", " game reserve", " reserve", " wildlife reserve"]:
        if park_lower.endswith(suffix):
            short_names.append(park_lower[:-len(suffix)])
    
    for work in works:
        title = (work.get("title") or "").lower()
        abstract = reconstruct_abstract(work.get("abstract_inverted_index", {})).lower()
        combined = title + " " + abstract
        
        # Check if park name appears in title or abstract
        park_mentioned = False
        for name in short_names:
            # Require word boundary to avoid partial matches (e.g., "pachinko" vs "chinko")
            import re
            if re.search(r'\b' + re.escape(name) + r'\b', combined):
                park_mentioned = True
                break
        
        if not park_mentioned:
            continue
        
        # Must also have conservation/ecology context
        has_conservation_context = False
        for keyword in CONSERVATION_KEYWORDS:
            if keyword in combined:
                has_conservation_context = True
                break
        
        # Or mention the country (if African)
        if country_lower and country_lower in combined:
            has_conservation_context = True
        
        if has_conservation_context:
            relevant.append(work)
    
    return relevant


def store_publication(conn: sqlite3.Connection, pa_id: str, work: Dict) -> Tuple[bool, Optional[int]]:
    """Store a publication in the database. Returns (is_new, pub_id)."""
    # Extract OpenAlex ID
    openalex_id = work.get("id", "")
    if "/" in openalex_id:
        openalex_id = openalex_id.split("/")[-1]
    
    # Check if already exists
    cursor = conn.execute(
        "SELECT id FROM pa_publications WHERE pa_id = ? AND openalex_id = ?",
        (pa_id, openalex_id)
    )
    existing = cursor.fetchone()
    if existing:
        return False, existing[0]
    
    # Extract authors
    authors = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author", {})
        name = author.get("display_name")
        if name:
            authors.append(name)
    
    # Get URL
    url = ""
    primary_loc = work.get("primary_location", {})
    if primary_loc:
        url = primary_loc.get("landing_page_url", "")
    if not url:
        url = work.get("doi", "")
    
    # Get abstract
    abstract = reconstruct_abstract(work.get("abstract_inverted_index", {}))
    
    # Insert
    cursor = conn.execute("""
        INSERT INTO pa_publications 
        (pa_id, openalex_id, title, authors, year, doi, url, abstract, cited_by_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pa_id,
        openalex_id,
        work.get("title", ""),
        json.dumps(authors),
        work.get("publication_year"),
        work.get("doi"),
        url,
        abstract,
        work.get("cited_by_count", 0)
    ))
    
    return True, cursor.lastrowid


def create_notification(conn: sqlite3.Connection, park_id: str, park_name: str, 
                       pub_id: int, title: str, year: int, authors: List[str]):
    """Create a notification for a new publication."""
    # Check if notifications table exists
    cursor = conn.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='notifications'
    """)
    if not cursor.fetchone():
        # Create notifications table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT,
                reference_id TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_park ON notifications(park_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(is_read) WHERE is_read = 0")
        conn.commit()
    
    # Create notification
    author_str = authors[0] if authors else "Unknown"
    if len(authors) > 1:
        author_str += f" et al."
    
    message = f"New publication: \"{title}\" ({year}) by {author_str}"
    
    conn.execute("""
        INSERT INTO notifications (park_id, notification_type, title, message, reference_id)
        VALUES (?, 'new_publication', ?, ?, ?)
    """, (park_id, f"New Research: {park_name}", message, str(pub_id)))


def sync_park(conn: sqlite3.Connection, park: Dict, notify: bool = False) -> int:
    """Sync publications for a single park. Returns count of new publications."""
    park_id = park.get("id", "")
    wdpa_id = park.get("wdpa_id", "")
    name = park.get("name", "")
    country = park.get("country", "")
    
    # Use WDPA ID if available, otherwise park ID
    pa_id = str(wdpa_id) if wdpa_id else park_id
    
    logger.info(f"Syncing {park_id} ({name})...")
    
    # Fetch publications
    works = fetch_publications(name, country)
    logger.info(f"  Found {len(works)} results from OpenAlex")
    
    # Filter to relevant ones
    relevant = filter_relevant_publications(works, name, country)
    logger.info(f"  {len(relevant)} are conservation-related and mention '{name}'")
    
    new_count = 0
    seen_ids = set()  # Track OpenAlex IDs to avoid duplicates in same batch
    for work in relevant:
        openalex_id = work.get("id", "")
        if openalex_id in seen_ids:
            continue
        seen_ids.add(openalex_id)
        is_new, pub_id = store_publication(conn, pa_id, work)
        if is_new:
            new_count += 1
            logger.info(f"  NEW: {work.get('title', '')[:60]}...")
            
            # Create notification if enabled and publication is NEW to our database
            # Only notify for publications from 2025 onwards to avoid spam for old papers
            if notify and pub_id:
                pub_year = work.get("publication_year", 0)
                # Get publication date if available
                pub_date = work.get("publication_date", "")
                
                # Only notify for recent publications (2025+)
                # This prevents notification spam when first syncing a park
                if pub_year and pub_year >= 2025:
                    authors = [a.get("author", {}).get("display_name", "") 
                              for a in work.get("authorships", [])]
                    create_notification(conn, park_id, name, pub_id, 
                                       work.get("title", ""), pub_year, authors)
                    logger.info(f"  Created notification for new publication")
    
    # Update sync status
    conn.execute("""
        INSERT INTO pa_publication_sync (pa_id, last_sync, result_count)
        VALUES (?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(pa_id) DO UPDATE SET 
            last_sync = CURRENT_TIMESTAMP, 
            result_count = excluded.result_count
    """, (pa_id, len(relevant)))
    
    conn.commit()
    
    logger.info(f"  {new_count} new publications stored")
    return new_count


def main():
    parser = argparse.ArgumentParser(description="Sync research publications from OpenAlex")
    parser.add_argument("--park", help="Sync specific park by ID (e.g., CAF_Chinko)")
    parser.add_argument("--all", action="store_true", help="Sync all parks")
    parser.add_argument("--stale", action="store_true", help="Only sync parks not synced in 7 days")
    parser.add_argument("--notify", action="store_true", help="Create notifications for new publications")
    parser.add_argument("--limit", type=int, default=10, help="Max parks to process (default: 10)")
    
    args = parser.parse_args()
    
    if not args.park and not args.all and not args.stale:
        parser.error("Specify --park, --all, or --stale")
    
    # Load parks
    parks = load_parks()
    parks_by_id = {p["id"]: p for p in parks}
    
    conn = sqlite3.connect(DB_PATH)
    
    total_new = 0
    
    if args.park:
        # Single park
        if args.park not in parks_by_id:
            logger.error(f"Park not found: {args.park}")
            return 1
        total_new = sync_park(conn, parks_by_id[args.park], notify=args.notify)
    
    elif args.stale:
        # Get parks that were synced more than 7 days ago
        cursor = conn.execute("""
            SELECT pa_id FROM pa_publication_sync 
            WHERE last_sync < datetime('now', '-7 days')
        """)
        stale_ids = set(row[0] for row in cursor)
        
        # Get parks that have NEVER been synced
        cursor = conn.execute("SELECT pa_id FROM pa_publication_sync")
        synced_ids = set(row[0] for row in cursor)
        
        count = 0
        for park in parks:
            if count >= args.limit:
                break
            wdpa_id = str(park.get("wdpa_id", ""))
            park_id = park["id"]
            
            # Sync if: never synced OR stale
            never_synced = wdpa_id not in synced_ids and park_id not in synced_ids
            is_stale = wdpa_id in stale_ids or park_id in stale_ids
            
            if never_synced or is_stale:
                total_new += sync_park(conn, park, notify=args.notify)
                count += 1
                time.sleep(1)  # Rate limit
    
    else:
        # All parks
        count = 0
        for park in parks:
            if count >= args.limit:
                break
            total_new += sync_park(conn, park, notify=args.notify)
            count += 1
            time.sleep(1)  # Rate limit
    
    conn.close()
    
    logger.info(f"Done! Total new publications: {total_new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
