import json
import os
from collections.abc import Generator

from dagster import RunRequest, SensorEvaluationContext, sensor

from agentic_librarian.orchestration.jobs import enhance_job


@sensor(job_name=enhance_job.name)
def new_file_sensor(context: SensorEvaluationContext) -> Generator[RunRequest, None, None]:
    """
    Sensor to monitor 'data/raw' for new CSV files and trigger the enhancement job.

    Uses a JSON cursor to track (mtime, filename) to avoid missing files with
    identical timestamps when batching.

    Yields:
        RunRequest: A request to run the enhancement job for the found CSV file.
    """
    raw_path = "data/raw"

    # Load cursor state
    if context.cursor:
        try:
            cursor_data = json.loads(context.cursor)
            if isinstance(cursor_data, dict):
                last_mtime = cursor_data.get("last_mtime", 0)
                last_filename = cursor_data.get("last_filename", "")
            else:
                last_mtime = float(cursor_data)
                last_filename = ""
        except (json.JSONDecodeError, ValueError):
            # Fallback for migration from old numeric cursor
            last_mtime = float(context.cursor)
            last_filename = ""
    else:
        last_mtime = 0
        last_filename = ""

    # Get all eligible files
    all_files = []
    for filename in os.listdir(raw_path):
        if not filename.endswith(".csv"):
            continue
        filepath = os.path.join(raw_path, filename)
        if os.path.isfile(filepath):
            file_mtime = os.path.getmtime(filepath)
            # Tuple comparison ensures strict ordering even with identical timestamps
            if (file_mtime, filename) > (last_mtime, last_filename):
                all_files.append((file_mtime, filename))

    # FIFO based on (mtime, filename)
    all_files.sort()

    # Batching to manage local resources
    batch_size = 5
    current_batch = all_files[:batch_size]

    for _, filename in current_batch:
        partition_date = filename.replace(".csv", "")
        # Explicitly register the partition before yielding the RunRequest
        context.instance.add_dynamic_partitions("csv_files", [partition_date])
        yield RunRequest(run_key=partition_date, partition_key=partition_date)

    # Advance the cursor to the last file processed in this batch
    if current_batch:
        new_mtime, new_filename = current_batch[-1]
        context.update_cursor(json.dumps({"last_mtime": new_mtime, "last_filename": new_filename}))
