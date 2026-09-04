package srv

// Encryption at rest for shared files and private tile sources.
//
// TWO PRIMITIVES, ONE KEY.
//
//   - Large files (an MBTiles can be gigabytes) use XChaCha20 as a stream
//     cipher with a per-file 24-byte nonce. A stream cipher is chosen over an
//     AEAD deliberately: HTTP Range requests must keep working (invariant in
//     docs/agents/exports.md — a download must be measured and resumable), and
//     a seekable keystream lets us serve byte N without decrypting bytes 0..N.
//     Integrity is not the goal here; confidentiality of a file on this disk
//     and in its backups is. The plaintext size equals the ciphertext size so
//     Content-Length and If-Range behave exactly as for a plain file.
//   - Small secrets (a tile URL that may carry a user's API key) use
//     AES-256-GCM, nonce prefixed, because there the authenticated form costs
//     nothing and a tampered URL should fail to decrypt rather than fetch.
//
// The master key comes from SHARED_FILES_KEY (hex, 32 bytes) in the
// environment or secrets.env. Absent, one is generated once into
// data/shared_files/.key (0600) so a fresh checkout works; losing that file
// loses every encrypted file, which the ops doc says in as many words.
//
// "Encrypted with the user password" in the product sense means: only the
// login that created the object can list or decrypt it. The binding to the
// login is the pwd_ref scope on the row; the key derivation deliberately does
// NOT use the password itself, because passwords rotate and a rotated
// password must not orphan a user's files. The threat this defeats is a copy
// of data/ or a database dump read without the key file — not a root on the
// running server, which can read anything anyway.

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"golang.org/x/crypto/chacha20"
)

var (
	fileKeyOnce sync.Once
	fileKey     []byte
	fileKeyErr  error
)

const fileKeyPath = sharedFileDir + "/.key"

// fileMasterKey returns the 32-byte master key, loading or generating it once.
func fileMasterKey() ([]byte, error) {
	fileKeyOnce.Do(func() {
		h := strings.TrimSpace(os.Getenv("SHARED_FILES_KEY"))
		if h == "" {
			h = secretsEnv("SHARED_FILES_KEY")
		}
		if h == "" {
			if b, err := os.ReadFile(fileKeyPath); err == nil {
				h = strings.TrimSpace(string(b))
			}
		}
		if h != "" {
			k, err := hex.DecodeString(h)
			if err != nil || len(k) != 32 {
				fileKeyErr = errors.New("SHARED_FILES_KEY must be 64 hex characters (32 bytes)")
				return
			}
			fileKey = k
			return
		}
		k := make([]byte, 32)
		if _, err := rand.Read(k); err != nil {
			fileKeyErr = err
			return
		}
		os.MkdirAll(filepath.Dir(fileKeyPath), 0o755)
		if err := os.WriteFile(fileKeyPath, []byte(hex.EncodeToString(k)+"\n"), 0o600); err != nil {
			fileKeyErr = err
			return
		}
		slog.Warn("generated a new shared-files master key; back it up or every encrypted file is lost with this disk",
			"path", fileKeyPath, "or_set", "SHARED_FILES_KEY in secrets.env")
		fileKey = k
	})
	return fileKey, fileKeyErr
}

// newFileNonce returns a fresh 24-byte XChaCha20 nonce.
func newFileNonce() ([]byte, error) {
	n := make([]byte, chacha20.NonceSizeX)
	_, err := rand.Read(n)
	return n, err
}

// encryptingWriter wraps w so plaintext written to it lands as ciphertext.
func encryptingWriter(w io.Writer, nonce []byte) (io.Writer, error) {
	key, err := fileMasterKey()
	if err != nil {
		return nil, err
	}
	c, err := chacha20.NewUnauthenticatedCipher(key, nonce)
	if err != nil {
		return nil, err
	}
	return &cipher.StreamWriter{S: c, W: w}, nil
}

// cryptReadSeeker is an io.ReadSeeker over an encrypted file: it seeks the
// underlying file and re-positions the keystream, so http.ServeContent can
// answer Range requests on it exactly as on a plain file.
type cryptReadSeeker struct {
	f     io.ReadSeeker
	key   []byte
	nonce []byte
	c     *chacha20.Cipher
	pos   int64
}

func newDecryptingReadSeeker(f io.ReadSeeker, nonce []byte) (*cryptReadSeeker, error) {
	key, err := fileMasterKey()
	if err != nil {
		return nil, err
	}
	r := &cryptReadSeeker{f: f, key: key, nonce: nonce}
	if err := r.reset(0); err != nil {
		return nil, err
	}
	return r, nil
}

func (r *cryptReadSeeker) reset(pos int64) error {
	c, err := chacha20.NewUnauthenticatedCipher(r.key, r.nonce)
	if err != nil {
		return err
	}
	// The keystream is generated in 64-byte blocks; SetCounter jumps to the
	// block, then we burn the remainder within it.
	const block = 64
	c.SetCounter(uint32(pos / block))
	if rem := int(pos % block); rem > 0 {
		junk := make([]byte, rem)
		c.XORKeyStream(junk, junk)
	}
	r.c = c
	r.pos = pos
	return nil
}

func (r *cryptReadSeeker) Read(p []byte) (int, error) {
	n, err := r.f.Read(p)
	if n > 0 {
		r.c.XORKeyStream(p[:n], p[:n])
		r.pos += int64(n)
	}
	return n, err
}

func (r *cryptReadSeeker) Seek(offset int64, whence int) (int64, error) {
	pos, err := r.f.Seek(offset, whence)
	if err != nil {
		return 0, err
	}
	if pos != r.pos {
		if err := r.reset(pos); err != nil {
			return 0, err
		}
	}
	return pos, nil
}

// sealSecret encrypts a short string (AES-256-GCM, nonce prefixed).
func sealSecret(plain string) ([]byte, error) {
	key, err := fileMasterKey()
	if err != nil {
		return nil, err
	}
	blk, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	g, err := cipher.NewGCM(blk)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, g.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return append(nonce, g.Seal(nil, nonce, []byte(plain), nil)...), nil
}

// openSecret reverses sealSecret. A tampered or foreign-key blob errors.
func openSecret(blob []byte) (string, error) {
	key, err := fileMasterKey()
	if err != nil {
		return "", err
	}
	blk, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}
	g, err := cipher.NewGCM(blk)
	if err != nil {
		return "", err
	}
	if len(blob) < g.NonceSize() {
		return "", errors.New("ciphertext too short")
	}
	out, err := g.Open(nil, blob[:g.NonceSize()], blob[g.NonceSize():], nil)
	if err != nil {
		return "", err
	}
	return string(out), nil
}
