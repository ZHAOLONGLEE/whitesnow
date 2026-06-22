"""Media scanning API endpoints."""

import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from database import db
from services.scanner import MediaScanner

router = APIRouter()
scanner = MediaScanner()

scan_task_id = None


@router.post("/scan/start")
async def start_scan(background_tasks: BackgroundTasks):
    """Start a new media scan."""
    global scan_task_id

    async with db.connect() as conn:
        # Check if already running
        log_row = await conn.execute(
            "SELECT * FROM scan_log WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        )
        running = await log_row.fetchone()

        if running:
            raise HTTPException(status_code=409, detail="Scan already in progress")

        # Create new scan log
        await conn.execute(
            "INSERT INTO scan_log (status) VALUES ('running')"
        )
        await conn.commit()
        row = await conn.execute("SELECT last_insert_rowid()")
        scan_task_id = (await row.fetchone())[0]

    # Run scan in background
    background_tasks.add_task(run_scan, scan_task_id)

    return {"status": "started", "scan_id": scan_task_id}


async def run_scan(scan_id: int):
    """Run media scan in background."""
    import traceback
    print(f"Starting scan task {scan_id}...")
    try:
        result = await scanner.scan()
        print(f"Scan completed: {result}")

        async with db.connect() as conn:
            await conn.execute(
                """UPDATE scan_log
                   SET finished_at = CURRENT_TIMESTAMP,
                       status = 'completed',
                       items_scanned = ?,
                       items_added = ?
                   WHERE id = ?""",
                [result["items_scanned"], result["items_added"], scan_id]
            )
            await conn.commit()
            print(f"Scan log updated for task {scan_id}")
    except Exception as e:
        print(f"Scan failed: {e}")
        traceback.print_exc()
        try:
            async with db.connect() as conn:
                await conn.execute(
                    """UPDATE scan_log
                       SET finished_at = CURRENT_TIMESTAMP,
                           status = 'failed',
                           error_message = ?
                       WHERE id = ?""",
                    [str(e), scan_id]
                )
                await conn.commit()
        except Exception as update_error:
            print(f"Failed to update scan log: {update_error}")


@router.get("/scan/status")
async def get_scan_status():
    """Get current scan status."""
    async with db.connect() as conn:
        row = await conn.execute(
            "SELECT * FROM scan_log ORDER BY started_at DESC LIMIT 1"
        )
        log = await row.fetchone()

        if not log:
            return {"status": "idle", "last_scan": None}

        return {
            "status": log["status"],
            "scan_id": log["id"],
            "started_at": log["started_at"],
            "finished_at": log["finished_at"],
            "items_scanned": log["items_scanned"],
            "items_added": log["items_added"],
        }


@router.get("/scan/history")
async def get_scan_history(limit: int = 10):
    """Get recent scan history."""
    async with db.connect() as conn:
        rows = await conn.execute(
            "SELECT * FROM scan_log ORDER BY started_at DESC LIMIT ?",
            [limit]
        )
        return [dict(row) for row in await rows.fetchall()]
