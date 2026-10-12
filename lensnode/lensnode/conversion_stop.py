import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class ConversionStopStore:
    """Persist task fences and original terminal results across daemon restarts."""

    def __init__(self, workspace_path):
        self.path = Path(workspace_path) / ".lensnode" / "conversion-stops.sqlite3"

    @contextmanager
    def _connect(self):
        """Commit a durable fence before it can be acknowledged."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS stops "
                    "(task_id TEXT PRIMARY KEY, datasource_uuid TEXT NOT NULL, frame TEXT)"
                )
                yield connection
        finally:
            connection.close()

    def get(self, task_id):
        """Return an existing fence and its original result, if available."""

        if not self.path.exists():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT datasource_uuid, frame FROM stops WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return {"datasource_uuid": row[0], "frame": json.loads(row[1]) if row[1] else None}

    def fence(self, task_id, datasource_uuid, frame=None):
        """Fence one exact identity, retaining the first real terminal result."""

        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO stops (task_id, datasource_uuid) VALUES (?, ?)",
                (task_id, datasource_uuid),
            )
            row = connection.execute("SELECT datasource_uuid FROM stops WHERE task_id = ?", (task_id,)).fetchone()
            if row[0] != datasource_uuid:
                return False
            if frame is not None:
                connection.execute(
                    "UPDATE stops SET frame = ? WHERE task_id = ? AND frame IS NULL",
                    (json.dumps(frame), task_id),
                )
        return True
