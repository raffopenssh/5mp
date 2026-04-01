// Command backup-zenodo creates a SQLite backup and uploads it to Zenodo as a draft.
package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"time"

	zenodo "github.com/raffopenssh/zenodo-mirror-go-pkg"
)

func main() {
	token := os.Getenv("ZENODO_TOKEN")
	if token == "" {
		log.Fatal("ZENODO_TOKEN environment variable required")
	}

	dbPath := "db.sqlite3"
	date := time.Now().UTC().Format("20060102")
	backupFile := fmt.Sprintf("5mp_db_backup_%s.sqlite3", date)
	manifestFile := "data/db_backup_zenodo_manifest.json"
	manifestKey := "db_backup"

	// Step 1: Create backup using sqlite3 .backup
	log.Printf("Creating backup: %s", backupFile)
	cmd := exec.Command("sqlite3", dbPath, fmt.Sprintf(".backup %s", backupFile))
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		log.Fatalf("sqlite3 backup failed: %v", err)
	}

	// Step 2: Verify integrity
	log.Println("Verifying backup integrity...")
	out, err := exec.Command("sqlite3", backupFile, "PRAGMA integrity_check;").Output()
	if err != nil {
		log.Fatalf("integrity check failed: %v", err)
	}
	if string(out) != "ok\n" {
		log.Fatalf("integrity check returned: %s", out)
	}
	log.Println("Integrity check: ok")

	// Step 3: Upload to Zenodo as draft (Upload does NOT publish)
	client := zenodo.New(token,
		zenodo.WithMaxRetries(3),
		zenodo.WithRetryWait(5*time.Second),
		zenodo.WithHTTPClient(&http.Client{Timeout: 30 * time.Minute}),
	)

	manifest, err := zenodo.NewManifest(manifestFile)
	if err != nil {
		log.Fatalf("load manifest: %v", err)
	}

	version := time.Now().UTC().Format("2006-01-02T15:04:05")

	meta := func(key, filename, ver string) map[string]interface{} {
		return map[string]interface{}{
			"metadata": map[string]interface{}{
				"title":        fmt.Sprintf("5MP Conservation Monitoring Database Backup - %s", date),
				"upload_type":  "dataset",
				"description":  "SQLite database backup for the 5MP Conservation Monitoring platform. Contains 6.1M+ fire detections, 458K feature geometries, settlement data, species data, and climate data for 162 African protected areas.",
				"access_right": "restricted",
				"creators":     []map[string]interface{}{{"name": "5MP Conservation Team"}},
			},
		}
	}

	log.Println("Uploading to Zenodo (draft only, will NOT publish)...")
	if err := client.Upload(manifestKey, backupFile, version, meta, manifest); err != nil {
		log.Fatalf("Zenodo upload failed: %v", err)
	}

	// Step 4: Verify upload is accessible
	log.Println("Verifying upload on Zenodo...")
	status, err := client.HeadFile(manifestKey, manifest)
	if err != nil {
		log.Fatalf("HEAD check failed: %v", err)
	}
	if status < 200 || status >= 300 {
		log.Fatalf("HEAD check returned HTTP %d", status)
	}
	log.Printf("Zenodo HEAD check: HTTP %d ✓", status)

	// Step 5: Print result
	entry := manifest.Get(manifestKey)
	if entry == nil {
		log.Fatal("entry not found in manifest after upload")
	}

	log.Println("")
	log.Println("=== Backup uploaded successfully (DRAFT - not published) ===")
	log.Printf("Deposition ID: %d", entry.DepoID)
	log.Printf("Bucket URL:    %s", entry.BucketURL)
	log.Printf("Filename:      %s", entry.Filename)
	log.Printf("Size:          %d bytes", entry.Size)
	log.Printf("MD5:           %s", entry.Checksum)
	log.Printf("Uploaded at:   %s", entry.UploadedAt.Format(time.RFC3339))
	log.Printf("Manifest:      %s", manifestFile)
	log.Printf("")
	log.Printf("View draft:    https://zenodo.org/deposit/%d", entry.DepoID)
	log.Printf("Download:      %s/%s (requires auth)", entry.BucketURL, entry.Filename)

	// Step 6: Remove local backup
	log.Printf("Removing local backup: %s", backupFile)
	if err := os.Remove(backupFile); err != nil {
		log.Printf("WARNING: failed to remove local backup: %v", err)
	} else {
		log.Println("Local backup removed.")
	}
}
