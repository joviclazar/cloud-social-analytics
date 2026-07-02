import os
import json
import re
import boto3
import pandas as pd
import awswrangler as wr

BUCKET_NAME = os.environ["BUCKET_NAME"]
s3 = boto3.client("s3")

def clean_html(text):
    if text is None or pd.isna(text):
        return ""
    return re.sub(r"<[^>]+>", "", str(text))

def detect_post_type(item):
    tags = item.get("_tags", [])

    if "comment" in tags:
        return "comment"
    if "job" in tags:
        return "job"
    if "ask_hn" in tags:
        return "ask_hn"
    if "poll" in tags:
        return "poll"
    return "story"

def handler(event, context):
    prefix = "bronze/hacker-news/"

    response = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)

    if "Contents" not in response:
        return {"status": "No files"}

    seen_posts = set()
    seen_relations = set()

    posts_rows = []
    relations_rows = []

    for obj in response["Contents"]:
        key = obj["Key"]

        if not key.endswith(".json"):
            continue

        file_obj = s3.get_object(Bucket=BUCKET_NAME, Key=key)
        data = json.loads(file_obj["Body"].read())

        for item in data:
            post_id = str(item.get("objectID"))

            if not post_id or post_id in seen_posts:
                continue
            seen_posts.add(post_id)

            author = item.get("author")
            if not author:
                continue

            created_at = pd.to_datetime(item.get("created_at"), utc=True, errors="coerce")
            if pd.isna(created_at):
                continue

            content = clean_html(
                item.get("story_text")
                or item.get("comment_text")
                or item.get("title")
            )

            posts_rows.append({
                "post_id": post_id,
                "author_username": author,
                "content_text": content,
                "post_type": detect_post_type(item),
                "created_at": created_at,
                "score": pd.to_numeric(item.get("points"), errors="coerce")
            })

            children = item.get("children") or item.get("kids") or []

            if isinstance(children, list):
                for child_id in children:
                    cid = str(child_id)
                    rel_key = f"{post_id}-{cid}"
                    if rel_key in seen_relations:
                        continue
                    seen_relations.add(rel_key)

                    relations_rows.append({
                        "parent_id": post_id,
                        "child_id": cid,
                        "relation_type": "has_child"
                    })

    posts_df = pd.DataFrame(posts_rows)

    if not posts_df.empty:
        posts_df["score"] = pd.to_numeric(posts_df["score"], errors="coerce").astype("Int64")
        posts_df["year"] = posts_df["created_at"].dt.year
        posts_df["month"] = posts_df["created_at"].dt.month
        posts_df["day"] = posts_df["created_at"].dt.day

        wr.s3.to_parquet(
            df=posts_df,
            path=f"s3://{BUCKET_NAME}/silver/posts/",
            dataset=True,
            mode="append",
            partition_cols=["year", "month", "day"]
        )

    relations_df = pd.DataFrame(relations_rows)

    if not relations_df.empty:
        wr.s3.to_parquet(
            df=relations_df,
            path=f"s3://{BUCKET_NAME}/silver/post_relations/",
            dataset=True,
            mode="append"
        )

    return {
        "status": "Completed",
        "posts": len(posts_df),
        "relations": len(relations_df)
    }