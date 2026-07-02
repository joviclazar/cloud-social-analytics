# cloud_social_analytics_stacks/aspects/lambda_alarm_aspect.py
from aws_cdk import (
    IAspect,
    Duration,
    aws_lambda as _lambda,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
)
from constructs import IConstruct
import jsii


@jsii.implements(IAspect)
class LambdaAlarmAspect:
    def __init__(self, alarm_topic: sns.ITopic, exclude_ids: set[str] | None = None):
        self.alarm_topic = alarm_topic
        self.exclude_ids = exclude_ids or set()

    def visit(self, node: IConstruct) -> None:
        if isinstance(node, _lambda.Function) and node.node.id not in self.exclude_ids:
            alarm = cloudwatch.Alarm(
                node, "ErrorAlarm",
                metric=node.metric_errors(period=Duration.minutes(1)),
                threshold=1,
                evaluation_periods=1,
                alarm_description=f"{node.node.id} lambda failed",
            )
            alarm.add_alarm_action(cw_actions.SnsAction(self.alarm_topic))