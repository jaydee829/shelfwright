"""Read-only trope audit: real (scout) vs fallback (genre/mood) tropes for the most
recent import vs the rest of the catalog. Distinguisher: work_tropes.justification
(scout tropes carry one; genre/mood fallbacks don't)."""

from sqlalchemy import text

from agentic_librarian.db.session import DatabaseManager

dbm = DatabaseManager()

Q1 = """
SELECT j.id AS job_id, j.created_at, u.email, j.total_rows,
        count(*) FILTER (WHERE r.outcome = 'created')   AS created,
        count(*) FILTER (WHERE r.outcome = 'linked')    AS linked,
        count(*) FILTER (WHERE r.outcome = 'duplicate') AS duplicate,
        count(*) FILTER (WHERE r.outcome = 'not_found') AS not_found,
        count(*) FILTER (WHERE r.status  = 'failed')    AS failed
FROM import_jobs j
JOIN import_rows r ON r.import_job_id = j.id
JOIN users u ON u.id = j.user_id
GROUP BY j.id, j.created_at, u.email, j.total_rows
ORDER BY j.created_at DESC
LIMIT 10
"""

Q2 = """
WITH job AS (SELECT id FROM import_jobs ORDER BY created_at DESC LIMIT 1)
SELECT w.title,
        r.outcome,
        count(wt.trope_id) FILTER (WHERE wt.justification IS NOT NULL) AS real_tropes,
        count(wt.trope_id) FILTER (WHERE wt.justification IS NULL)     AS fallback_tropes,
        coalesce(array_length(w.genres, 1), 0)                  AS genres
FROM import_rows r
JOIN works w ON w.id = r.work_id
LEFT JOIN work_tropes wt ON wt.work_id = w.id
WHERE r.import_job_id = (SELECT id FROM job) AND r.work_id IS NOT NULL
GROUP BY w.id, w.title, r.outcome
ORDER BY real_tropes ASC, w.title
"""

Q3 = """
WITH job AS (SELECT id FROM import_jobs ORDER BY created_at DESC LIMIT 1),
    per_work AS (
        SELECT w.id,
            (SELECT count(*) FROM work_tropes wt WHERE wt.work_id = w.id AND wt.justification IS NOT NULL) AS real_cnt,
            (SELECT count(*) FROM work_tropes wt WHERE wt.work_id = w.id AND wt.justification IS NULL)     AS fb_cnt,
            EXISTS (SELECT 1 FROM import_rows r WHERE r.import_job_id = (SELECT id FROM job) AND r.work_id = w.id) AS in_recent
        FROM works w
    )
SELECT CASE WHEN in_recent THEN 'recent import' ELSE 'rest of catalog' END AS bucket,
        count(*) AS works,
        count(*) FILTER (WHERE real_cnt > 0) AS works_with_real_tropes,
        round(avg(real_cnt), 2) AS avg_real_tropes,
        round(avg(fb_cnt), 2)   AS avg_fallback_tropes
FROM per_work
GROUP BY 1
ORDER BY 1
"""


def run(label, sql):
    print(f"\n===== {label} =====")
    with dbm.get_session() as s:
        res = s.execute(text(sql))
        cols = list(res.keys())
        print(" | ".join(cols))
        print("-" * 60)
        for row in res:
            print(" | ".join("" if v is None else str(v) for v in row))


if __name__ == "__main__":
    run("1) recent import jobs", Q1)
    run("2) per-book real vs fallback tropes (latest job)", Q2)
    run("3) rollup: recent import vs rest of catalog", Q3)
