import boto3
import pandas as pd
import io
import os
import logging
import re
from collections import defaultdict
from sqlalchemy import create_engine, MetaData, Table, Column, inspect, text
from sqlalchemy.types import String, Integer, Float, DateTime, Boolean

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ["S3_BUCKET"]
S3_PREFIX = os.environ.get("S3_PREFIX", "gold/")
PG_CONN = os.environ["PG_CONN"]
s3 = boto3.client("s3")


def list_parquet_files():
    logger.info("Listing S3 parquet files...")

    paginator = s3.get_paginator("list_objects_v2")

    files = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".parquet"):
                files.append(obj)

    logger.info(f"Found {len(files)} parquet files")
    return files


def get_all_files_per_metric(files):
    """Groups ALL parquet files by metric (instead of resolving only the
    latest one per metric). Files within each metric are sorted oldest ->
    newest by LastModified, so that when we upsert file-by-file, later
    files naturally overwrite earlier ones on primary-key conflict and we
    end up with the most recent version of every row."""
    grouped = defaultdict(list)

    for f in files:
        key = f["Key"]
        parts = key.split("/")

        if len(parts) < 2:
            continue

        metric = parts[1]
        grouped[metric].append(f)

    all_files = {}
    for metric, items in grouped.items():
        items_sorted = sorted(items, key=lambda x: x["LastModified"])
        all_files[metric] = [item["Key"] for item in items_sorted]

    total_files = sum(len(v) for v in all_files.values())
    logger.info(
        f"Resolved {total_files} files across {len(all_files)} metrics "
        f"for full backfill"
    )
    return all_files


def parse_hive_partitions(key):
    """Extracts Hive-style partition=value pairs from an S3 key path,
    e.g. 'gold/daily_users_metric/platform=X/year=2023/month=02/f.parquet'
    -> {'platform': 'X', 'year': '2023', 'month': '02'}.
    awswrangler writes partition values into the folder structure only,
    not into the parquet file itself, so we have to recover them here."""
    partitions = {}
    for part in key.split("/"):
        if "=" in part:
            col, _, val = part.partition("=")
            if col:
                partitions[col] = val
    return partitions


def read_parquet(key):
    logger.info(f"Reading parquet: {key}")

    obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    df = pd.read_parquet(io.BytesIO(obj["Body"].read()))

    partitions = parse_hive_partitions(key)
    for col, val in partitions.items():
        if col not in df.columns:
            df[col] = val
            logger.info(f"Restored partition column '{col}'={val} from S3 path")

    return df


# Fallback single-column keys, used only if a metric has no entry in
# COMPOSITE_PK_OVERRIDES below.
POSSIBLE_KEYS = ["id", "post_id", "user_id", "username"]

# Metrics whose gold data needs a multi-column key, either because there's
# no natural single-column key (daily_* / data_quality_score), or because
# a single column would collapse the daily history into one row per
# user/post (top_* tables — these keep one row per snapshot_date).
COMPOSITE_PK_OVERRIDES = {
    "daily_hn_posts_metric": ["date", "post_type"],
    "daily_users_metric": ["platform", "date"],
    "data_quality_score": ["snapshot_date", "table_name"],
    "top_hn_users_high_karma": ["snapshot_date", "username"],
    "top_hn_users_low_karma": ["snapshot_date", "username"],
    "top_hn_jobs_by_score": ["snapshot_date", "post_id"],
    "top_hn_posts_by_score": ["snapshot_date", "post_id"],
    "top_twitter_users_by_followers": ["snapshot_date", "username"],
}


def detect_pk(df, table_name):
    """Returns the primary key as a list of column names, or None if one
    can't be determined."""
    if table_name in COMPOSITE_PK_OVERRIDES:
        candidate = COMPOSITE_PK_OVERRIDES[table_name]
        missing = [c for c in candidate if c not in df.columns]
        if missing:
            logger.error(
                f"Composite PK override for {table_name} references "
                f"missing columns {missing}; columns present: {list(df.columns)}"
            )
            return None
        return candidate

    for k in POSSIBLE_KEYS:
        if k in df.columns:
            return [k]

    return None


def infer_sql_type(series):
    if pd.api.types.is_integer_dtype(series):
        return Integer()
    if pd.api.types.is_float_dtype(series):
        return Float()
    if pd.api.types.is_bool_dtype(series):
        return Boolean()
    if pd.api.types.is_datetime64_any_dtype(series):
        return DateTime()
    return String()


def normalize_pg_conn(conn_str):
    if conn_str.startswith("postgresql+psycopg2://"):
        return conn_str.replace("postgresql+psycopg2://", "postgresql+pg8000://", 1)
    if conn_str.startswith("postgresql://"):
        return conn_str.replace("postgresql://", "postgresql+pg8000://", 1)
    if conn_str.startswith("postgres://"):
        return conn_str.replace("postgres://", "postgresql+pg8000://", 1)
    return conn_str


def sanitize_table_name(name):
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def quote_ident(name):
    """Double-quote a Postgres identifier to protect against reserved
    words (e.g. 'date', 'rank') and mixed case."""
    return '"' + name.replace('"', '""') + '"'


def get_existing_pk_columns(engine, table_name):
    """Returns the list of PK columns for an existing table, or None if
    the table doesn't exist."""
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        return None
    pk_constraint = inspector.get_pk_constraint(table_name)
    return pk_constraint.get("constrained_columns") or []


def ensure_correct_schema(engine, table_name, pk):
    """If the table already exists with a different PK than the one we
    need (e.g. leftover from before this fix, or before top_* tables got
    composite keys), drop it so it can be recreated with the right PK.
    Safe here because gold data in S3 is the source of truth and will be
    fully re-upserted right after."""
    existing_pk = get_existing_pk_columns(engine, table_name)
    if existing_pk is None:
        return  # table doesn't exist yet, nothing to do

    if sorted(existing_pk) != sorted(pk):
        logger.warning(
            f"Table {table_name} exists with PK {existing_pk}, "
            f"expected {pk}. Dropping and recreating table."
        )
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE {quote_ident(table_name)}"))


def create_table_if_not_exists(engine, table_name, df, pk):
    metadata = MetaData()

    columns = []
    for col in df.columns:
        col_type = infer_sql_type(df[col])
        columns.append(Column(col, col_type, primary_key=(col in pk)))

    table = Table(table_name, metadata, *columns)
    metadata.create_all(engine)

    logger.info(f"Table ensured: {table_name} (pk={pk})")
    return table


def batch_upsert(engine, df, table_name, pk):
    logger.info(f"Upserting {len(df)} rows into {table_name}")

    cols = list(df.columns)
    update_cols = [c for c in cols if c not in pk]

    rows = [tuple(x) for x in df.to_numpy()]

    chunk_size = 1000

    quoted_table = quote_ident(table_name)
    quoted_cols = [quote_ident(c) for c in cols]
    quoted_pk = [quote_ident(c) for c in pk]

    with engine.begin() as conn:
        for i in range(0, len(rows), chunk_size):
            batch = rows[i:i + chunk_size]

            update_clause = ""
            if update_cols:
                update_clause = ",".join(
                    [f"{quote_ident(c)}=EXCLUDED.{quote_ident(c)}" for c in update_cols]
                )

            insert_sql = f"""
                INSERT INTO {quoted_table} ({",".join(quoted_cols)})
                VALUES ({",".join(["%s"] * len(cols))})
                ON CONFLICT ({",".join(quoted_pk)})
                {"DO UPDATE SET " + update_clause if update_clause else "DO NOTHING"}
            """

            conn.exec_driver_sql(insert_sql, batch)

            logger.info(f"Inserted batch {i + len(batch)}/{len(rows)}")

    logger.info(f"Finished upsert for {table_name}")


def process_metric(engine, metric, keys):
    """Reads and upserts every parquet file for a metric one at a time,
    so memory usage stays bounded to a single file regardless of how much
    history the metric has. Files are processed oldest -> newest, and
    since batch_upsert does ON CONFLICT DO UPDATE, a row from a later
    file naturally overwrites the same PK from an earlier file."""
    table_name = sanitize_table_name(metric)
    logger.info(f"Processing metric: {metric} as table {table_name} ({len(keys)} files)")

    pk = None
    total_rows = 0

    for idx, key in enumerate(keys):
        df = read_parquet(key)

        if df.empty:
            continue

        if pk is None:
            # Determine PK and ensure table exists using the first
            # non-empty file we encounter for this metric.
            pk = detect_pk(df, table_name)
            if pk is None:
                raise Exception(f"No primary key found for metric {metric}")

            ensure_correct_schema(engine, table_name, pk)
            create_table_if_not_exists(engine, table_name, df, pk)

        # Guard against duplicate PKs within a single file: a multi-row
        # INSERT can't ON CONFLICT the same row twice in one statement.
        df = df.drop_duplicates(subset=pk, keep="last")

        batch_upsert(engine, df, table_name, pk)
        total_rows += len(df)

        logger.info(
            f"Finished file {idx + 1}/{len(keys)} for metric {metric} "
            f"({total_rows} rows upserted so far)"
        )

    if pk is None:
        raise Exception(f"All files empty or no data found for metric {metric}")

    return table_name, pk, total_rows


def lambda_handler(event, context):

    logger.info("Lambda started (FULL BACKFILL mode - reading ALL parquet files, file-by-file)")

    engine = create_engine(normalize_pg_conn(PG_CONN), pool_pre_ping=True)

    files = list_parquet_files()
    files_per_metric = get_all_files_per_metric(files)

    results = []

    for metric, keys in files_per_metric.items():
        try:
            table_name, pk, total_rows = process_metric(engine, metric, keys)

            results.append({
                "metric": table_name,
                "files": len(keys),
                "rows": total_rows,
                "pk": pk
            })

        except Exception as e:
            logger.error(f"Error processing {metric}: {str(e)}")
            continue

    logger.info("Lambda finished")

    return {
        "status": "success",
        "processed": results
    }