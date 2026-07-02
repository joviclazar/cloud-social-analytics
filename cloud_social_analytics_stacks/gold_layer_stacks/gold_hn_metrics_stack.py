from aws_cdk import (
    Stack,
    Duration,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_ec2 as ec2,
    aws_events as events,
    aws_events_targets as targets,
)
from constructs import Construct


class GoldHnMetricsStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        data_bucket,
        vpc: ec2.IVpc,
        security_group: ec2.ISecurityGroup,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        role = iam.Role(
            self,
            "GoldHnMetricsRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )

        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )
        role.add_managed_policy(
            # Obavezno za Lambdu koja radi u VPC-u (ENI management).
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaVPCAccessExecutionRole"
            )
        )

        data_bucket.grant_read(role, "silver/*")
        data_bucket.grant_write(role, "gold/*")

        wrangler_layer = _lambda.LayerVersion.from_layer_version_arn(
            self,
            "WranglerLayer",
            "arn:aws:lambda:eu-central-1:336392948345:layer:AWSSDKPandas-Python312:1"
        )

        fn = _lambda.Function(
            self,
            "GoldHnMetricsLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="gold_hn_metrics.handler",
            code=_lambda.Code.from_asset("lambdas/goldHnMetrics"),
            role=role,
            timeout=Duration.minutes(15),
            memory_size=3008,
            environment={
                "BUCKET_NAME": data_bucket.bucket_name,
            },
            layers=[wrangler_layer],
            # Gold Lambda - samo S3 pristup preko gateway endpoint-a,
            # kontrolisano processing_sg iz NetworkStack-a.
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[security_group],
        )

        fn.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.AWS_IAM,
        )

        events.Rule(
            self,
            "GoldHnMetricsSchedule",
            schedule=events.Schedule.cron(hour="10", minute="0"),
            targets=[targets.LambdaFunction(fn)],
        )