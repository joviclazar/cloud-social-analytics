import os
from aws_cdk import Stack, aws_sns as sns, aws_sns_subscriptions as subscriptions, aws_lambda as _lambda
from constructs import Construct

class NotifierStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        self.discord_notifier = _lambda.Function(
            self, "DiscordNotifier",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="discord_notifier.handler",
            code=_lambda.Code.from_asset("lambdas/discordNotifier"),
            environment={"DISCORD_WEBHOOK_URL": os.environ["DISCORD_WEBHOOK_URL"]},
        )

        self.alarm_topic = sns.Topic(self, "LambdaAlarmTopic")
        self.alarm_topic.add_subscription(
            subscriptions.LambdaSubscription(self.discord_notifier)
        )