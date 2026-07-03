from aws_cdk import (
    Stack,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_ec2 as ec2,
    Duration,
    Size,
)
from constructs import Construct


class TwitterPostsSilverStack(Stack):

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
            "TwitterPostsSilverRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )

        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"))
        role.add_managed_policy(
            # Obavezno za Lambdu koja radi u VPC-u (ENI management).
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
        )
        data_bucket.grant_read(role, "bronze/twitter/*")
        data_bucket.grant_write(role, "silver/posts/*")

        wrangler_layer = _lambda.LayerVersion.from_layer_version_arn(
            self,
            "WranglerLayer",
            "arn:aws:lambda:eu-central-1:336392948345:layer:AWSSDKPandas-Python312:1"
        )

        fn = _lambda.Function(
            self,
            "TwitterPostsSilverLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="twitter_posts_extraction.handler",
            code=_lambda.Code.from_asset("lambdas/twitterPostsExtraction"),
            role=role,
            timeout=Duration.minutes(15),
            memory_size=3008,
            ephemeral_storage_size=Size.gibibytes(10),
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