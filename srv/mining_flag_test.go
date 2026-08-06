package srv

import "testing"

func TestPublicSettlementNarrativeStripsTurbidityEvidence(t *testing.T) {
	cases := []struct{ in, want string }{
		{
			"Possible mining site 35km west-northwest of Sattio. Low fire activity but 0.11 km² of forest loss with scattered pattern. Proximity to Chinko suggests alluvial extraction. Sentinel-2 shows a sediment plume on the Chinko 2.1km away (~67km of river turbid downstream, 2026-06-29) — consistent with active alluvial gold washing. 45 GFW canopy-disturbance alerts within 5km corroborate recent ground activity.",
			"Possible mining site 35km west-northwest of Sattio. Low fire activity but 0.11 km² of forest loss with scattered pattern. Proximity to Chinko suggests alluvial extraction. 45 GFW canopy-disturbance alerts within 5km corroborate recent ground activity.",
		},
		{
			// river-proximity mining inference must survive untouched
			"Possible mining site 26km east-northeast of Kirigbo. Low fire activity but 0.00 km² of forest loss. Proximity to Ngoangoa suggests alluvial extraction.",
			"Possible mining site 26km east-northeast of Kirigbo. Low fire activity but 0.00 km² of forest loss. Proximity to Ngoangoa suggests alluvial extraction.",
		},
		{"Fishing camp 1.2km from Ubangi River.", "Fishing camp 1.2km from Ubangi River."},
	}
	for i, c := range cases {
		if got := publicSettlementNarrative("mining", c.in); got != c.want {
			t.Errorf("case %d:\n got  %q\n want %q", i, got, c.want)
		}
	}
}

func TestScannerInjectedSettlement(t *testing.T) {
	yes := []string{
		"[Pit detection 2026-07-12] 3.2 ha bare-earth cluster 0.4 km from waterway; suspected mining pits/camp.",
		"[Turbidity alert 2026-07-04] Suspected alluvial mining site.",
	}
	no := []string{
		"Possible mining site 26km east-northeast of Kirigbo. Proximity to Ngoangoa suggests alluvial extraction.",
		"", "Village near Bangui.",
	}
	for _, n := range yes {
		if !scannerInjectedSettlement(n) {
			t.Errorf("want injected: %q", n)
		}
	}
	for _, n := range no {
		if scannerInjectedSettlement(n) {
			t.Errorf("want NOT injected: %q", n)
		}
	}
}

// The plume sentence is full of decimals ("2.1km away", "0.11 km²"); a naive
// [^.]* matcher stops mid-number and leaves debris in the served narrative.
func TestPublicSettlementNarrativeHandlesDecimals(t *testing.T) {
	in := "Site here. Sentinel-2 shows a sediment plume on the Chinko 2.1km away (~67.5km of river turbid downstream, 2026-06-29) — consistent with active alluvial gold washing. Tail sentence."
	want := "Site here. Tail sentence."
	if got := publicSettlementNarrative("mining", in); got != want {
		t.Errorf("\n got  %q\n want %q", got, want)
	}
}

func TestPublicSettlementNarrativePassthroughWhenEnabled(t *testing.T) {
	// scannerInjectedSettlement/publicSettlementNarrative must be inert for
	// non-mining text regardless.
	in := "Herding camp 3.5km from the Vovodo. Seasonal cattle presence."
	if got := publicSettlementNarrative("pastoral", in); got != in {
		t.Errorf("pastoral narrative altered: %q", got)
	}
}
