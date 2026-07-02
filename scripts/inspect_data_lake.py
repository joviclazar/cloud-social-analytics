# Run with ./venv/bin/python3 (not .venv) -- venv has awswrangler/pandas installed for local data inspection.
import os
import awswrangler as wr
import boto3

BUCKET = os.environ.get("BUCKET_NAME", "data-lake-bucket-social-analytics")
s3 = boto3.client("s3")

def bronze_summary(prefix):
    paginator = s3.get_paginator("list_objects_v2")
    count, total_bytes = 0, 0
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            count += 1
            total_bytes += obj["Size"]
    return count, total_bytes

def table_summary(name, path):
    print(f"\n--- {name} ({path}) ---")
    try:
        df = wr.s3.read_parquet(path=path, dataset=True)
    except Exception as e:
        print("ERROR:", e)
        return
    rows, cols = df.shape
    print(f"rows={rows} cols={cols}")
    null_pct = (df.isna().sum() / max(rows, 1) * 100).round(1)
    for c in df.columns:
        dtype = df[c].dtype
        flag = "  <-- object dtype, check for mixed types" if str(dtype) == "object" else ""
        print(f"  {c:<22} {str(dtype):<22} null%={null_pct[c]:<6}{flag}")

def main():
    print("=" * 70)
    print("BRONZE (raw, not used for visualization)")
    print("=" * 70)
    for label, prefix in [("hacker-news", "bronze/hacker-news/"), ("twitter", "bronze/twitter/")]:
        n, b = bronze_summary(prefix)
        print(f"{label:<15} files={n:<8} size={b / 1024 / 1024:.1f} MB")

    print("\n" + "=" * 70)
    print("SILVER")
    print("=" * 70)
    table_summary("silver/posts", f"s3://{BUCKET}/silver/posts/")
    table_summary("silver/users", f"s3://{BUCKET}/silver/users/")
    table_summary("silver/post_relations", f"s3://{BUCKET}/silver/post_relations/")

    print("\n" + "=" * 70)
    print("GOLD")
    print("=" * 70)
    gold_tables = [
        "daily_hn_posts_metric",
        "daily_users_metric",
        "data_quality_score",
        "top_hn_jobs_by_score",
        "top_hn_posts_by_score",
        "top_hn_users_high_karma",
        "top_hn_users_low_karma",
        "top_twitter_users_by_followers",
    ]
    for t in gold_tables:
        table_summary(t, f"s3://{BUCKET}/gold/{t}/")

if __name__ == "__main__":
    main()
