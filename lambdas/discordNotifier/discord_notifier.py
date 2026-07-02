import json
import os
import urllib.request

def handler(event, context):
    sns_message = event["Records"][0]["Sns"]["Message"]
    alarm = json.loads(sns_message)

    alarm_name = alarm.get("AlarmName", "Unknown alarm")
    description = alarm.get("AlarmDescription", "No description")
    state = alarm.get("NewStateValue", "UNKNOWN")
    reason = alarm.get("NewStateReason", "No reason")
    time = alarm.get("StateChangeTime", "Unknown time")
    region = alarm.get("Region", "Unknown region")

    if state != "ALARM":
        return

    payload = json.dumps({
        "embeds": [
            {
                "title": f"Lambda error: {alarm_name}",
                "color": 15158332,
                "fields": [
                    {"name": "Description", "value": description, "inline": False},
                    {"name": "Reason", "value": reason, "inline": False},
                    {"name": "Time", "value": time, "inline": True},
                    {"name": "Region", "value": region, "inline": True},
                ],
            }
        ]
    }).encode("utf-8")

    req = urllib.request.Request(
        os.environ["DISCORD_WEBHOOK_URL"],
        data=payload,
        headers={"Content-Type": "application/json",
                 "User-Agent": "DiscordBot (private use) Python-urllib/3.12"},
        method="POST",
    )

    print(f"Payload: {payload}")

    urllib.request.urlopen(req)