from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_ec2 as ec2,
)
from constructs import Construct

class HackerNewsPostsManualSilverStack(Stack):

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
            "HackerNewsPostsManualSilverRole",
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

        data_bucket.grant_read(role, "bronze/hacker-news/*")
        data_bucket.grant_write(role, "silver/posts/*")
        data_bucket.grant_write(role, "silver/post_relations/*")

        wrangler_layer = _lambda.LayerVersion.from_layer_version_arn(
            self,
            "WranglerLayer",
            "arn:aws:lambda:eu-central-1:336392948345:layer:AWSSDKPandas-Python312:1"
        )

        fn = _lambda.Function(
            self,
            "HackerNewsPostsManualSilverLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="hacker_news_posts_extraction_manual.handler",
            code=_lambda.Code.from_asset("lambdas/hackerNewsPostsExtractionManual"),
            role=role,
            timeout=Duration.minutes(15),
            memory_size=3007,
            environment={
                "BUCKET_NAME": data_bucket.bucket_name,
            },
            layers=[wrangler_layer],
            # Silver Lambda - samo S3 pristup preko gateway endpoint-a,
            # kontrolisano processing_sg iz NetworkStack-a.
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[security_group],
        )

        CfnOutput(
            self,
            "ExportsOutputRefHackerNewsPostsManualSilverLambdaB7FF446C50BEDA80",
            value=fn.function_name,
            export_name="SocialAnalyticsHackerNewsPostsManualSilverStack:ExportsOutputRefHackerNewsPostsManualSilverLambdaB7FF446C50BEDA80"
        )