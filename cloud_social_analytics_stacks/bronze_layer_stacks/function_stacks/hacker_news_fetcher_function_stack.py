from aws_cdk import (
    Duration,
    Stack,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_ec2 as ec2,
    aws_events as events,
    aws_events_targets as targets,
    BundlingOptions,
    CfnOutput,
)

from constructs import Construct


class HackerNewsFetcherFunctionStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        data_bucket: s3.IBucket,
        vpc: ec2.IVpc,
        security_group: ec2.ISecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambda_role = iam.Role(
            self,
            "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                # Obavezno za Lambdu koja radi u VPC-u (ENI management).
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        )
        data_bucket.grant_read_write(lambda_role)

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["events:PutEvents"],
                resources=["*"]
            )
        )

        hacker_news_function = _lambda.Function(
            self,
            "HackerNewsFetcher",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="hacker_news_fetcher.fetch_hacker_news",
            code=_lambda.Code.from_asset("lambdas/hackerNewsFetcher",
                                         bundling=BundlingOptions(
                                             image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                                             command=[
                                                 "bash", "-c",
                                                 "pip install --no-cache -r requirements.txt -t /asset-output && cp -r . /asset-output"
                                             ]
                                         )),
            role=lambda_role,
            timeout=Duration.minutes(10),
            memory_size=512,
            environment={
                "BUCKET_NAME": data_bucket.bucket_name,
            },
            # Bronze collector - u privatnom subnetu, izlaz ka HN API-ju
            # preko NAT-a, kontrolisano collector_sg iz NetworkStack-a.
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[security_group],
        )

        events.Rule(
            self,
            "HackerNewsSchedule",
            schedule=events.Schedule.cron(hour="9", minute="0"),
            targets=[targets.LambdaFunction(hacker_news_function)],
        )

        hn_url = hacker_news_function.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.AWS_IAM,
            cors=_lambda.FunctionUrlCorsOptions(
                allowed_origins=["*"]
            )
        )

        CfnOutput(
            self,
            "HackerNewsFunctionUrl",
            value=hn_url.url
        )