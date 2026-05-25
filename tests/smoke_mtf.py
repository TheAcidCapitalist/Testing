from scanner.cli import run_daily
from scanner.data.storage import Storage

# Run the daily orchestrator in memory or with a test db
run_daily(scope="sample", db_path="data/scanner_test.duckdb", backfill=True)

with Storage("data/scanner_test.duckdb") as storage:
    df = storage._con.execute("""
        SELECT resolution, direction, count(*) as cnt 
        FROM tbl_indicator_outputs 
        WHERE indicator_name = 'box_breakout' AND direction IN ('buy', 'sell')
        GROUP BY resolution, direction
    """).df()
    print("Detections per mode:")
    print(df)
