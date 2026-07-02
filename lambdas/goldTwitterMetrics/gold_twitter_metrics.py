import os
import pandas as pd
import awswrangler as wr
from datetime import datetime, timezone, timedelta

BUCKET_NAME = os.environ["BUCKET_NAME"]
TODAY = str(datetime.now(timezone.utc).date())

def write_gold(df, path, partition_cols):
    if df.empty:
        return
    wr.s3.to_parquet(
        df=df,
        path=path,
        dataset=True,
        mode="overwrite_partitions",
        partition_cols=partition_cols
    )

def compute_dq_chunked(path, max_rows=50000, partition_filter=None):
    total_rows = 0
    total_cells = 0
    non_null_cells = 0
    try:
        read_kwargs = {"path": path, "dataset": True, "chunked": True}
        if partition_filter is not None:
            read_kwargs["partition_filter"] = partition_filter
        chunks = wr.s3.read_parquet(**read_kwargs)
        for chunk in chunks:
            rows, cols = chunk.shape
            total_rows += rows
            total_cells += rows * cols
            non_null_cells += int(chunk.notna().sum().sum())
            if total_rows >= max_rows:
                break
    except Exception as e:
        print(f"[compute_dq_chunked] error for {path}: {e}")
        return total_rows, 0.0
    dq_score = round((non_null_cells / total_cells * 100), 2) if total_cells > 0 else 0.0
    return total_rows, dq_score

def handler(event, context):
    daily_counts = {}
    top_candidates = []
    try:
        chunks = wr.s3.read_parquet(
            path=f"s3://{BUCKET_NAME}/silver/users/platform=X/",
            dataset=True,
            chunked=True
        )
        chunk_count = 0
        for chunk in chunks:
            chunk_count += 1
            print(f"[twitter] chunk {chunk_count}, cols={list(chunk.columns)}, rows={len(chunk)}")
            chunk["date"] = chunk["created_at"].dt.date.astype(str)
            for dv, cnt in chunk["date"].value_counts().items():
                daily_counts[dv] = daily_counts.get(dv, 0) + cnt
            if "followers_count" in chunk.columns:
                f = chunk[chunk["followers_count"].notna()]
                if not f.empty:
                    top_candidates.append(f[["username", "followers_count"]].nlargest(10, "followers_count"))
        print(f"[twitter] total chunks={chunk_count}, top_candidates={len(top_candidates)}")
    except Exception as e:
        print(f"[twitter] ERROR: {e}")

    if daily_counts:
        du = pd.DataFrame(list(daily_counts.items()), columns=["date", "new_users"]).sort_values("date")
        du["total_users"] = du["new_users"].cumsum()
        du["platform"] = "X"
        du["year"] = du["date"].str[:4]
        du["month"] = du["date"].str[5:7]
        write_gold(du, f"s3://{BUCKET_NAME}/gold/daily_users_metric/", ["platform", "year", "month"])

    if top_candidates:
        tf = pd.concat(top_candidates, ignore_index=True).drop_duplicates(subset=["username"]).nlargest(10, "followers_count").reset_index(drop=True)
        tf["rank"] = tf.index + 1
        tf["snapshot_date"] = TODAY
        write_gold(tf, f"s3://{BUCKET_NAME}/gold/top_twitter_users_by_followers/", ["snapshot_date"])

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    y, m, d = int(yesterday.year), int(yesterday.month), int(yesterday.day)

    def yesterday_partition_filter(partition):
        return (
            partition.get("year") == str(y)
            and partition.get("month") == str(m)
            and partition.get("day") == str(d)
        )

    tables = {
        "silver_posts": (f"s3://{BUCKET_NAME}/silver/posts/", yesterday_partition_filter),
        "silver_hn_users": (f"s3://{BUCKET_NAME}/silver/users/platform=HackerNews/", None),
        "silver_twitter_users": (f"s3://{BUCKET_NAME}/silver/users/platform=X/", None),
    }

    rows = []
    for tn, (p, f) in tables.items():
        tr, ds = compute_dq_chunked(p, partition_filter=f)
        rows.append({"table_name": tn, "total_rows": tr, "non_null_pct": ds, "snapshot_date": TODAY})

    write_gold(pd.DataFrame(rows), f"s3://{BUCKET_NAME}/gold/data_quality_score/", ["snapshot_date"])
    return {"status": "Completed", "snapshot_date": TODAY}
