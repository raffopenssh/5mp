"""The two geology sheets: where they come from and what their graticule says.

Both entries carry an *initial* pixel<->degree guess only.  The guess exists to
put the search window on the right line; every shipped coordinate is measured
off the printed graticule by scripts/geomaps/gridfit.py and the initial numbers
never reach the output.  They were read off the sheets themselves:

  sudan  the Zenodo TIFF already carries a rough affine (someone's world file,
         units nominally "fathom" and a visible skew term).  It is right to
         about +-30 px, which is fine as a seed and not fine as an answer.
  car    the NLA scan has no georeferencing at all.  Seeded from two printed
         labels per axis, located by the collar tick marks:
           lon 15 deg at x=1812, one degree = 1723 px
           lat 10 deg at y=2395, one degree = 1757 px
"""

SHEETS = {
    "sudan": dict(
        id="sudan",
        title="Geological Map of the Sudan (2004)",
        short="Sudan geology",
        year=2004,
        publisher="Geological Research Authority of the Sudan (GRAS)",
        scale="1:2,000,000",
        source_url="https://zenodo.org/records/19150268",
        src="data/geomaps/src/sudan_geology_2004.tif",
        # ISO3 whose territory the sheet actually maps; everything outside is dropped
        countries=["SDN", "SSD"],
        grid=dict(lons=list(range(22, 39)), lats=list(range(4, 24))),
        # seed affine: lon/lat -> px  (from the file's own geotransform)
        seed=dict(x0=21.87195993657761, dx=0.002381475128887203, dxy=1.812855317971832e-05,
                  y0=23.81795240588686, dyx=1.570073196061058e-05, dy=-0.002279727971629338),
        legend="Rock units after GRAS 2004 compilation; letter codes as printed.",
    ),
    "car": dict(
        id="car",
        title="Carte geologique de la Republique Centrafricaine (1964)",
        short="CAR geology",
        year=1964,
        publisher="Bureau de recherches geologiques et minieres (BRGM), coord. J-L. Mestraud",
        scale="1:1,500,000",
        source_url="https://nla.gov.au/nla.obj-2981820452",
        src="data/geomaps/src/car_geology_1964.tif",
        countries=["CAF"],
        grid=dict(lons=list(range(15, 28)), lats=list(range(2, 12))),
        seed=dict(lon0=15.0, x0=1812.0, xdeg=1723.0, lat0=10.0, y0=2395.0, ydeg=1757.0),
        legend="Units after the 1:500,000 reconnaissance mapping, 1946-1961.",
    ),
}
