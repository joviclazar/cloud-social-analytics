import os
import json
import re
import time
import boto3
import pandas as pd
import urllib3
import awswrangler as wr
from concurrent.futures import ThreadPoolExecutor, as_completed

BUCKET_NAME = os.environ["BUCKET_NAME"]
s3 = boto3.client("s3")

FIREBASE_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"

http = urllib3.PoolManager()


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


def fetch_score(post_id, max_retries=3):
    delay = 0.5
    for attempt in range(max_retries):
        try:
            resp = http.request(
                "GET",
                FIREBASE_ITEM_URL.format(post_id),
                timeout=5.0
            )
            if resp.status != 200:
                raise urllib3.exceptions.HTTPError(f"Status {resp.status}")

            item = json.loads(resp.data.decode("utf-8"))
            if item is None:
                return post_id, None
            return post_id, item.get("score")
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
            else:
                return post_id, None


def enrich_scores(posts_df, max_workers=20):
    # comment vec ima score=0 postavljen ranije, njih ne fetchujemo
    mask = (posts_df["post_type"] != "comment") & posts_df["score"].isna()
    to_fetch = posts_df.loc[mask, "post_id"].tolist()

    if not to_fetch:
        return posts_df

    fetched = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_score, pid): pid for pid in to_fetch}
        for future in as_completed(futures):
            pid, score = future.result()
            if score is not None:
                fetched[pid] = score

    if fetched:
        posts_df["score"] = posts_df["score"].fillna(posts_df["post_id"].map(fetched))

    return posts_df


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

            post_type = detect_post_type(item)

            # Komentari na HN-u nemaju glasanje/score - postavljamo 0 umesto NULL
            if post_type == "comment":
                score_value = 0
            else:
                score_value = pd.to_numeric(item.get("points"), errors="coerce")

            posts_rows.append({
                "post_id": post_id,
                "author_username": author,
                "content_text": content,
                "post_type": post_type,
                "created_at": created_at,
                "score": score_value
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
        posts_df["score"] = pd.to_numeric(posts_df["score"], errors="coerce")

        # Popuni score preko zvanicnog HN API-ja za story/job/ask_hn/poll gde je Algolia vratila null
        posts_df = enrich_scores(posts_df)

        posts_df["score"] = posts_df["score"].astype("Int64")

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