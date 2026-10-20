# Run attachments for Agent clients

Both endpoints accept the same `Authorization: Bearer <agent-token>` header
used to query a Run. Permanent and expiring managed Agent tokens, legacy
Agent JWTs, and ordinary web authentication are supported.

## List attachments

`GET /api/lens/runs/{run_uuid}/output-files/?page=1&page_size=10`

The response lists only files published with the Run's final answer. Drafts
are excluded. A Run without a final answer returns an empty collection.
The caller must own the Run's session; another user's Run returns HTTP 404.

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
      {
        "uuid": "bb3f5235-d224-4be6-a191-95da9d1566d8",
        "url": "/api/lens/output-files/bb3f5235-d224-4be6-a191-95da9d1566d8/",
        "filename": "report.txt",
        "status": "published",
        "content_type": "text/plain",
        "byte_size": 6,
        "created_at": "2026-10-20T04:00:00Z"
      }
    ]
  }
}
```

Pagination follows the application's standard contract: `page` starts at 1,
`page_size` defaults to 10 and is capped at 10000. Follow `data.next` until it
is null to retrieve all files. Files are ordered by creation time.

`GET /api/lens/runs/{run_uuid}/` also includes an `output_files` array with
the same metadata, so clients already polling the Run can discover files
without a separate list request. No storage paths or public URLs are exposed.

## Download an attachment

`GET /api/lens/output-files/{file_uuid}/`

Use the returned `url` relative to the SourceLens server, supplying the same
Bearer credential. The response streams the file bytes, with the recorded
`Content-Type` (or `application/octet-stream`) and
`Content-Disposition: attachment` carrying the filename. It has no JSON envelope.

```shell
curl --fail --show-error \
  -H "Authorization: Bearer $SOURCELENS_TOKEN" \
  "$SOURCELENS_URL/api/lens/runs/$RUN_UUID/output-files/"

curl --fail --show-error \
  -H "Authorization: Bearer $SOURCELENS_TOKEN" \
  "$SOURCELENS_URL/api/lens/output-files/$FILE_UUID/" \
  --output ./report.txt
```

Agent credentials can download only published files belonging to their own
sessions, including when issued by a staff user. Draft files return HTTP 404;
another user's published files return HTTP 403. Ordinary staff web sessions
retain their existing audit access. Agent downloads use
`Cache-Control: private, no-store`.

Missing, expired, and revoked credentials return HTTP 401. Revocation is
checked on each new request; it cannot remove bytes already downloaded.
Both Agent endpoints are read-only. They reuse existing file records and
storage and require no additional database migration.
