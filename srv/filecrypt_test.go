package srv

import (
	"sync"
	"bytes"
	"io"
	"os"
	"testing"
)

func TestCryptReadSeekerRangesMatchPlain(t *testing.T) {
	dir := t.TempDir()
	os.Setenv("SHARED_FILES_KEY", "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")
	fileKeyOnce = sync.Once{}; fileKey = nil; fileKeyErr = nil
	plain := make([]byte, 10_000)
	for i := range plain {
		plain[i] = byte(i * 7)
	}
	nonce, _ := newFileNonce()
	var enc bytes.Buffer
	w, err := encryptingWriter(&enc, nonce)
	if err != nil {
		t.Fatal(err)
	}
	w.Write(plain[:3000])
	w.Write(plain[3000:])
	if bytes.Equal(enc.Bytes(), plain) {
		t.Fatal("ciphertext equals plaintext")
	}
	if enc.Len() != len(plain) {
		t.Fatalf("size changed: %d vs %d", enc.Len(), len(plain))
	}
	_ = dir
	r, err := newDecryptingReadSeeker(bytes.NewReader(enc.Bytes()), nonce)
	if err != nil {
		t.Fatal(err)
	}
	for _, off := range []int64{0, 1, 63, 64, 65, 1000, 4097, 9999} {
		if _, err := r.Seek(off, io.SeekStart); err != nil {
			t.Fatal(err)
		}
		got := make([]byte, 200)
		n, _ := io.ReadFull(r, got)
		if !bytes.Equal(got[:n], plain[off:off+int64(n)]) {
			t.Errorf("range at %d does not decrypt to plaintext", off)
		}
	}
	s, err := sealSecret("https://example.test/{z}/{x}/{y}?key=abc")
	if err != nil {
		t.Fatal(err)
	}
	back, err := openSecret(s)
	if err != nil || back != "https://example.test/{z}/{x}/{y}?key=abc" {
		t.Fatalf("secret roundtrip: %q %v", back, err)
	}
	s[len(s)-1] ^= 1
	if _, err := openSecret(s); err == nil {
		t.Error("tampered secret opened")
	}
}

func TestTileURLSchemes(t *testing.T) {
	cases := map[string]string{
		"https://h/{z}/{x}/{y}.jpg":        "https://h/6/35/32.jpg",
		"https://h/{z}/{x}/{-y}.png":       "https://h/6/35/31.png",
		"https://t{s}.h/a{q}.jpeg?g=1":     "https://t3.h/a300011.jpeg?g=1",
		"https://{a-c}.h/{z}/{x}/{y}":      "https://b.h/6/35/32",
		"https://mt{s}.h/vt?x={x}&y={y}&z={z}": "https://mt3.h/vt?x=35&y=32&z=6",
	}
	for tpl, want := range cases {
		if got := tileURL(tpl, 6, 35, 32); got != want {
			t.Errorf("%s → %s, want %s", tpl, got, want)
		}
	}
	for _, bad := range []string{"http://h/{z}/{x}/{y}", "https://localhost/{z}/{x}/{y}", "https://h/tiles", "https://10.0.0.1/{z}/{x}/{y}", ""} {
		if _, _, _, err := validateTileTemplate(bad); err == nil {
			t.Errorf("accepted %q", bad)
		}
	}
	if _, _, sch, err := validateTileTemplate("https://t{s}.h/a{quadkey}.jpeg"); err != nil || sch != "quadkey" {
		t.Errorf("quadkey: %v %s", err, sch)
	}
}
