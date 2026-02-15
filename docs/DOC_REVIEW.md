# 5MP Conservation Monitoring App - Documentation Review

**Review Date:** 2026-01-30  
**Reviewer:** Documentation Audit Subagent

---

## Executive Summary

The 5MP (Five Minute Parks) application is a sophisticated conservation monitoring platform that provides real-time visualization and analysis of threats to 162 keystone protected areas across Africa. The current documentation is minimal and developer-focused, lacking user guides for the primary stakeholders: ministry staff, NGOs, and park managers.

---

## 1. What the App Does

### Core Purpose
A web-based 3D globe interface for monitoring conservation threats across Africa's most important protected areas, providing:

1. **Fire Detection & Analysis**
   - 4.6+ million VIIRS satellite fire detections (NASA FIRMS data)
   - Fire group trajectory tracking with transhumance pattern detection
   - Infraction reporting (fires inside protected area boundaries)
   - AI-generated narratives describing fire movements near settlements and rivers

2. **Deforestation Monitoring**
   - Hansen Global Forest Change data integration
   - 3,200+ deforestation events tracked
   - 5,600+ deforestation clusters identified
   - Spatial clustering analysis for pattern detection

3. **Settlement Analysis**
   - 15,066 settlements mapped via GHSL (Global Human Settlement Layer)
   - Settlement density analysis per park
   - 7 parks identified as "pristine wilderness" (no settlements)

4. **Legal Framework Database**
   - 19 African countries with conservation legislation
   - Park-specific regulations for major protected areas
   - International designations (UNESCO, Ramsar, etc.)

5. **Patrol Effort Tracking**
   - GPX upload and analysis for ranger patrol routes
   - Grid-based coverage visualization
   - Movement type classification (foot, vehicle, aircraft)

6. **Research Publications**
   - OpenAlex API integration for academic literature
   - Per-park publication counts and metadata

---

## 2. How It's Organized

### Codebase Structure

```
5mp/
├── cmd/
│   └── srv/              # Main server binary entry point
├── srv/                  # HTTP server logic
│   ├── api.go           # GeoJSON grid & area APIs
│   ├── narrative_handlers.go  # AI narrative generation
│   ├── fire_handlers.go # Fire analysis endpoints
│   ├── park_stats_handlers.go # Park statistics
│   ├── legal.go         # Legal framework lookups
│   ├── templates/       # HTML templates (globe.html, admin.html)
│   ├── areas/           # WDPA area indexing
│   ├── gpx/             # GPX file processing
│   └── auth/            # Authentication middleware
├── db/
│   ├── migrations/      # 9 SQL migration files
│   └── queries/         # SQLC query definitions
├── data/
│   ├── keystones_with_boundaries.json  # 162 park boundaries
│   ├── legal_frameworks.json           # Country legislation
│   ├── fire/            # Raw fire CSV data
│   ├── ghsl/            # Settlement raster data
│   └── hansen/          # Deforestation raster data
├── scripts/             # Python analysis scripts
│   ├── fire_*.py        # Fire detection & analysis
│   ├── ghsl_*.py        # Settlement processing
│   ├── deforestation_*.py  # Forest loss analysis
│   └── osm_*.py         # OpenStreetMap data extraction
└── static/
    └── downloads/       # Data exports (SQLite DB)
```

### Database Schema (Key Tables)

| Table | Records | Purpose |
|-------|---------|--------|
| fire_detections | 4,621,211 | Individual VIIRS fire points |
| park_settlements | 15,066 | GHSL settlement locations per park |
| deforestation_events | 3,218 | Hansen forest loss events |
| osm_places | 10,600 | Named places (towns, rivers, villages) |
| park_group_infractions | 801 | Fire groups entering protected areas |
| deforestation_clusters | 5,616 | Clustered deforestation events |
| gpx_uploads | 59 | Ranger patrol tracks |
| users | 2 | Registered users |

### API Endpoints

**Public APIs:**
- `GET /api/grid` - Patrol effort grid cells (GeoJSON)
- `GET /api/areas` - Protected area boundaries
- `GET /api/parks/{id}/fire-narrative` - AI fire narrative
- `GET /api/parks/{id}/deforestation-narrative` - AI deforestation narrative
- `GET /api/parks/{id}/settlement-narrative` - Settlement analysis
- `GET /api/legal/pa/{id}` - Legal framework by park
- `GET /api/park/{id}/boundary` - Park boundary GeoJSON

**Admin APIs:**
- `POST /admin/upload/fire` - Upload fire CSV data
- `POST /admin/upload/ghsl` - Upload GHSL tiles
- `GET /admin/status` - Background processing status

---

## 3. What Data It Manages

### Geographic Coverage
- **162 keystone protected areas** across Africa
- Countries represented: Angola, Benin, Botswana, Cameroon, CAR, Chad, DRC, Ethiopia, Gabon, Kenya, Mozambique, Namibia, Rwanda, Senegal, South Africa, South Sudan, Tanzania, Uganda, Zambia, Zimbabwe

### Data Sources
| Source | Data Type | Update Frequency |
|--------|-----------|------------------|
| NASA FIRMS | Fire detections (VIIRS) | Near-real-time |
| Hansen/GFC | Deforestation | Annual |
| GHSL | Settlements | Static (2020) |
| OSM | Place names, rivers, roads | Periodic |
| OpenAlex | Research publications | On-demand |
| WDPA | Protected area boundaries | Static |

### Data Quality Notes
- Fire data: 2022-2024, high confidence VIIRS detections
- Chinko (CAR) has most detailed fire trajectory analysis
- 7 parks confirmed as pristine (no settlements within boundaries)

---

## 4. Current Documentation State

### Existing Documentation

| File | Content | Target Audience | Quality |
|------|---------|-----------------|--------|
| `AGENTS.md` | Generic template reference | Developers | ❌ Minimal |
| `README.md` | Build/run instructions | Developers | ❌ Generic template |
| `CONTINUATION_INSTRUCTIONS.md` | Session state for AI agents | AI/Developers | ✅ Detailed |
| `docs/FIRE_ANALYSIS_CHINKO.md` | Fire analysis methodology | Technical users | ✅ Good |

### Missing Documentation

1. **User Guides** (Critical)
   - Ministry staff quick-start guide
   - NGO data access guide  
   - Park manager dashboard guide
   - Mobile usage instructions

2. **Technical Documentation** (Important)
   - API reference documentation
   - Database schema documentation
   - Data pipeline architecture
   - Deployment guide

3. **Data Documentation** (Important)
   - Data dictionary for all fields
   - Data provenance and licensing
   - Update schedules and procedures
   - Data quality assessment

4. **Training Materials** (Desirable)
   - Video tutorials
   - Use case examples
   - Interpretation guides for narratives

---

## 5. Recommendations for Documentation Updates

### Priority 1: User Guides for Primary Stakeholders

#### A. Ministry Staff Guide (`docs/USER_GUIDE_MINISTRY.md`)
Recommended sections:
- Overview of conservation monitoring capabilities
- Navigating the globe interface
- Interpreting fire threat narratives
- Accessing legal framework information
- Generating reports for policy decisions
- Data download and export options

#### B. NGO/Research Guide (`docs/USER_GUIDE_NGO.md`)
Recommended sections:
- Accessing research publication data
- Downloading raw data (SQLite database)
- API access for programmatic queries
- Citation and data attribution requirements
- Integrating with GIS workflows

#### C. Park Manager Guide (`docs/USER_GUIDE_PARK_MANAGER.md`)
Recommended sections:
- Uploading patrol GPX tracks
- Monitoring fire threats in your park
- Understanding deforestation alerts
- Settlement encroachment analysis
- Generating patrol coverage reports

### Priority 2: Update Core Documentation

#### A. Rewrite `README.md`
Replace generic template with:
- Project mission statement
- Key features overview
- Screenshot of globe interface
- Quick start for users (not just developers)
- Links to user guides
- Contact information

#### B. Expand `AGENTS.md`
Add:
- Project-specific context for AI agents
- Key architectural decisions
- Common development tasks
- Data processing workflows

### Priority 3: Technical Documentation

#### A. Create `docs/API_REFERENCE.md`
- Document all endpoints
- Request/response examples
- Authentication requirements
- Rate limits

#### B. Create `docs/DATABASE_SCHEMA.md`
- Full table documentation
- Relationship diagrams
- Index explanations
- Query optimization notes

#### C. Create `docs/DATA_SOURCES.md`
- Source attribution (NASA, Hansen, GHSL, etc.)
- Data licensing terms
- Update procedures
- Quality assurance processes

### Priority 4: Training Materials

#### A. Create `docs/TUTORIALS/` directory
- `01_FIRST_LOOK.md` - Basic navigation
- `02_FIRE_ANALYSIS.md` - Understanding fire narratives
- `03_DEFORESTATION.md` - Forest loss interpretation
- `04_PATROL_UPLOAD.md` - GPX upload workflow
- `05_DATA_DOWNLOAD.md` - Exporting data

---

## 6. Documentation Gaps Analysis

### Critical Gaps (Blocking User Adoption)

1. **No user-facing documentation exists** - All current docs are developer-focused
2. **Password page lacks instructions** - Users arrive at `/?pwd=test2026` with no context
3. **Narrative interpretation undefined** - AI-generated fire/deforestation stories need explanation
4. **Legal framework context missing** - No explanation of how to use legislation data

### Important Gaps (Reduces Effectiveness)

1. **Data freshness unclear** - Users don't know when data was last updated
2. **Coverage limitations undocumented** - Which parks have full data vs partial
3. **Mobile experience undocumented** - Touch gestures, responsive behavior
4. **Offline capabilities undefined** - What works without internet

### Minor Gaps (Polish)

1. **Attribution requirements unclear** - How should users cite the platform
2. **Feedback mechanism missing** - No documented way to report issues
3. **Changelog absent** - No history of updates and improvements

---

## 7. Suggested Documentation Roadmap

### Phase 1 (Week 1-2): Essential User Docs
- [ ] Create `docs/USER_GUIDE_PARK_MANAGER.md`
- [ ] Create `docs/USER_GUIDE_MINISTRY.md`
- [ ] Rewrite `README.md` with project overview
- [ ] Add in-app help tooltips

### Phase 2 (Week 3-4): Technical Reference
- [ ] Create `docs/API_REFERENCE.md`
- [ ] Create `docs/DATABASE_SCHEMA.md`
- [ ] Document data processing scripts
- [ ] Add deployment documentation

### Phase 3 (Week 5-6): Extended Materials
- [ ] Create tutorial series
- [ ] Add use case examples
- [ ] Document interpretation guidelines
- [ ] Create FAQ document

### Phase 4 (Ongoing): Maintenance
- [ ] Establish changelog practice
- [ ] Document data update procedures
- [ ] Create contributor guidelines
- [ ] Build feedback collection system

---

## 8. Summary

The 5MP application is a powerful conservation monitoring platform with sophisticated data integration, but its documentation significantly lags its capabilities. The primary users—ministry staff, NGOs, and park managers—have no documentation to guide their usage. The recommended priority is to create user-facing guides before expanding technical documentation.

**Immediate Actions:**
1. Create a proper README.md that explains the project
2. Write a Park Manager quick-start guide
3. Add help text to the globe interface
4. Document the data sources and their limitations

---

*This review was generated by analyzing the codebase structure, database schema, API endpoints, and existing documentation files.*
