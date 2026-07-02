import os
import re
import boto3
import pandas as pd
import awswrangler as wr

BUCKET_NAME = os.environ["BUCKET_NAME"]
s3 = boto3.client("s3")

def clean_html(text):
    if pd.isna(text): 
        return ""
    return re.sub(r'<[^>]+>', '', str(text))

def map_post_type(row):
        if row.get('is_retweet') == 'True':
            return 'retweet'
        return 'tweet'

def handler(event, context):
    prefix = "bronze/twitter/"
    response = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)
    
    if "Contents" not in response:
        return {"status": "No files to process"}

    for obj in response["Contents"]:
        key = obj["Key"]
        if key.endswith(".csv"):
            print(f"Processing posts from: {key}")
            
            s3_path = f"s3://{BUCKET_NAME}/{key}"
            reader = wr.s3.read_csv(
                path=s3_path,
                usecols=["text", "date", "user_name", "is_retweet"],
                chunksize=100000,
                dataset=False
            )
            
            for chunk in reader:
                posts_df = pd.DataFrame({
                    "post_id": chunk["date"].astype(str) + "_" + chunk["user_name"], # Proxy ID
                    "author_username": chunk["user_name"],
                    "content_text": chunk["text"].apply(clean_html),
                    "post_type": chunk.apply(map_post_type, axis=1),
                    "created_at": pd.to_datetime(chunk["date"], utc=True, errors="coerce"),
                    "score": pd.Series(pd.NA, index=chunk.index, dtype="Int64")
                })
                
                posts_df = posts_df.dropna(subset=["created_at"])
                if posts_df.empty:
                    continue
                
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
                
    return {"status": "Completed"}