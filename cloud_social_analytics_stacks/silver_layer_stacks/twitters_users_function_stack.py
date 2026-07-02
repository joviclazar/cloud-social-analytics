from aws_cdk import (
    Stack,
    CfnOutput,
    aws_lambda as _lambda,
    aws_iam as iam,
    Duration,
)
from constructs import Construct

class TwitterUsersSilverStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, data_bucket, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        role = iam.Role(
            self,
            "TwitterUsersSilverRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )

        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        data_bucket.grant_read(role, "bronze/twitter/*")
        data_bucket.grant_write(role, "silver/users/*")

        wrangler_layer = _lambda.LayerVersion.from_layer_version_arn(
            self,
            "WranglerLayer",
            "arn:aws:lambda:eu-central-1:336392948345:layer:AWSSDKPandas-Python312:1"
        )

        fn = _lambda.Function(
            self,
            "TwitterUsersSilverLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="twitter_users_extraction.handler",
            code=_lambda.Code.from_asset("lambdas/twitterUsersExtraction"),
            role=role,
            timeout=Duration.minutes(15),
            memory_size=3007,
            environment={
                "BUCKET_NAME": data_bucket.bucket_name,
            },
            layers=[wrangler_layer],
        )

        twitter_users_fn = fn

        CfnOutput(
            self,
            "ExportsOutputRefTwitterUsersSilverLambda74C24C6531CE18B3",
            value=fn.function_name,
            export_name="SocialAnalyticsTwitterUsersSilverStack:ExportsOutputRefTwitterUsersSilverLambda74C24C6531CE18B3"
        )