package dbgen

import (
	"context"
	"database/sql"
	"time"
)

// Upload Queue queries

type QueueUploadParams struct {
	UserID      string
	UserEmail   string
	Filename    string
	FileHash    sql.NullString
	FileContent []byte
}

type UploadQueueItem struct {
	ID            int64
	UserID        string
	UserEmail     string
	Filename      string
	FileHash      sql.NullString
	FileContent   []byte
	Status        string
	ErrorMessage  sql.NullString
	CreatedAt     time.Time
	StartedAt     sql.NullTime
	CompletedAt   sql.NullTime
	ResultUploadID sql.NullInt64
	ResultJson    sql.NullString
}

func (q *Queries) QueueUpload(ctx context.Context, arg QueueUploadParams) (UploadQueueItem, error) {
	row := q.db.QueryRowContext(ctx, `
		INSERT INTO upload_queue (user_id, user_email, filename, file_hash, file_content, status)
		VALUES (?, ?, ?, ?, ?, 'pending')
		RETURNING id, user_id, user_email, filename, file_hash, file_content, status, error_message, created_at, started_at, completed_at, result_upload_id, result_json
	`, arg.UserID, arg.UserEmail, arg.Filename, arg.FileHash, arg.FileContent)
	
	var i UploadQueueItem
	err := row.Scan(
		&i.ID, &i.UserID, &i.UserEmail, &i.Filename, &i.FileHash, &i.FileContent,
		&i.Status, &i.ErrorMessage, &i.CreatedAt, &i.StartedAt, &i.CompletedAt,
		&i.ResultUploadID, &i.ResultJson,
	)
	return i, err
}

func (q *Queries) GetUploadQueueByHash(ctx context.Context, fileHash sql.NullString) (UploadQueueItem, error) {
	row := q.db.QueryRowContext(ctx, `
		SELECT id, user_id, user_email, filename, file_hash, file_content, status, error_message, created_at, started_at, completed_at, result_upload_id, result_json
		FROM upload_queue WHERE file_hash = ? AND status != 'failed' LIMIT 1
	`, fileHash)
	
	var i UploadQueueItem
	err := row.Scan(
		&i.ID, &i.UserID, &i.UserEmail, &i.Filename, &i.FileHash, &i.FileContent,
		&i.Status, &i.ErrorMessage, &i.CreatedAt, &i.StartedAt, &i.CompletedAt,
		&i.ResultUploadID, &i.ResultJson,
	)
	return i, err
}

type GetPendingUploadsRow struct {
	ID          int64
	UserID      string
	UserEmail   string
	Filename    string
	FileHash    sql.NullString
	FileContent []byte
	Status      string
	CreatedAt   time.Time
}

func (q *Queries) GetPendingUploads(ctx context.Context, limit int64) ([]GetPendingUploadsRow, error) {
	rows, err := q.db.QueryContext(ctx, `
		SELECT id, user_id, user_email, filename, file_hash, file_content, status, created_at 
		FROM upload_queue WHERE status = 'pending' ORDER BY created_at LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	
	var items []GetPendingUploadsRow
	for rows.Next() {
		var i GetPendingUploadsRow
		if err := rows.Scan(&i.ID, &i.UserID, &i.UserEmail, &i.Filename, &i.FileHash, &i.FileContent, &i.Status, &i.CreatedAt); err != nil {
			return nil, err
		}
		items = append(items, i)
	}
	return items, rows.Err()
}

func (q *Queries) MarkUploadProcessing(ctx context.Context, id int64) error {
	_, err := q.db.ExecContext(ctx, `UPDATE upload_queue SET status = 'processing', started_at = CURRENT_TIMESTAMP WHERE id = ?`, id)
	return err
}

type MarkUploadCompletedParams struct {
	ResultUploadID sql.NullInt64
	ResultJson     sql.NullString
	ID             int64
}

func (q *Queries) MarkUploadCompleted(ctx context.Context, arg MarkUploadCompletedParams) error {
	_, err := q.db.ExecContext(ctx, `UPDATE upload_queue SET status = 'completed', completed_at = CURRENT_TIMESTAMP, result_upload_id = ?, result_json = ? WHERE id = ?`, arg.ResultUploadID, arg.ResultJson, arg.ID)
	return err
}

type MarkUploadFailedParams struct {
	ErrorMessage sql.NullString
	ID           int64
}

func (q *Queries) MarkUploadFailed(ctx context.Context, arg MarkUploadFailedParams) error {
	_, err := q.db.ExecContext(ctx, `UPDATE upload_queue SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ? WHERE id = ?`, arg.ErrorMessage, arg.ID)
	return err
}

type GetUploadQueueStatusRow struct {
	ID             int64
	Status         string
	ErrorMessage   sql.NullString
	ResultUploadID sql.NullInt64
	ResultJson     sql.NullString
	CreatedAt      time.Time
	CompletedAt    sql.NullTime
}

func (q *Queries) GetUploadQueueStatus(ctx context.Context, id int64) (GetUploadQueueStatusRow, error) {
	row := q.db.QueryRowContext(ctx, `
		SELECT id, status, error_message, result_upload_id, result_json, created_at, completed_at
		FROM upload_queue WHERE id = ?
	`, id)
	
	var i GetUploadQueueStatusRow
	err := row.Scan(&i.ID, &i.Status, &i.ErrorMessage, &i.ResultUploadID, &i.ResultJson, &i.CreatedAt, &i.CompletedAt)
	return i, err
}
