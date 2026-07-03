import datetime
import os
import boto3
import requests
import json
from concurrent.futures import ThreadPoolExecutor

bucket_name = os.environ['BUCKET_NAME']
s3 = boto3.resource('s3')
events_client = boto3.client("events")

def split_day(start_dt, parts=48):
    step = datetime.timedelta(minutes=30)
    return [
        (start_dt + i * step, start_dt + (i + 1) * step)
        for i in range(parts)
    ]


def fetch_interval(bucket, tag, start, end, interval_id, base_prefix):
    base_url = "https://hn.algolia.com/api/v1"

    response = requests.get(
        base_url + "/search_by_date",
        params={
            "tags": tag,
            "numericFilters": f"created_at_i>{int(start.timestamp())},created_at_i<{int(end.timestamp())}",
            "hitsPerPage": 1000
        }
    )

    data = response.json()
    hits = data["hits"]

    key = f"{base_prefix}/interval_{interval_id:03d}.json"

    bucket.put_object(
        Key=key,
        Body=json.dumps(hits),
        ContentType="application/json"
    )


def fetch_hacker_news(event, context):
    bucket = s3.Bucket(bucket_name)

    now = datetime.datetime.now(datetime.timezone.utc)
    yesterday = now.date() - datetime.timedelta(days=1)

    start_day = datetime.datetime.combine(
        yesterday,
        datetime.time.min,
        tzinfo=datetime.timezone.utc
    )

    tags = ["story", "comment", "ask_hn", "job", "poll"]
    intervals = split_day(start_day, parts=48)

    jobs = []
    for tag in tags:
        for i, (start, end) in enumerate(intervals):
            prefix = f"bronze/hacker-news/year={yesterday.year}/month={yesterday.month:02d}/day={yesterday.day:02d}/{tag}"
            jobs.append((bucket, tag, start, end, i, prefix))

    def worker(job):
        fetch_interval(*job)

    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(worker, jobs)

    prefix = (
        f"bronze/hacker-news/"
        f"year={yesterday.year}/"
        f"month={yesterday.month:02d}/"
        f"day={yesterday.day:02d}/"
    )

    events_client.put_events(
        Entries=[
            {
                "Source": "socialanalytics.hackernews",
                "DetailType": "HackerNewsIngestionCompleted",
                "Detail": json.dumps({
                    "prefix": prefix,
                    "year": yesterday.year,
                    "month": yesterday.month,
                    "day": yesterday.day
                })
            }
        ]
    )

    return {
        "statusCode": 200,
        "body": "Hacker News ingestion completed"
    }