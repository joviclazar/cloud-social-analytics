from aws_cdk import (
    Duration,
    Stack,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_ec2 as ec2,
    BundlingOptions,
    CfnOutput,
    Size
)

from constructs import Construct


class TwitterFetcherFunctionStack(Stack):

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

        twitter_function = _lambda.Function(
            self,
            "TwitterFetcher",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="twitter_fetcher.twitter_fetcher",
            code=_lambda.Code.from_asset("lambdas/twitterFetcher",
                bundling=BundlingOptions(
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -r . /asset-output"
                    ]
                )),
            role=lambda_role,
            timeout=Duration.minutes(15),
            memory_size=1024,
            ephemeral_storage_size=Size.gibibytes(5),
            environment={
                "BUCKET_NAME": data_bucket.bucket_name,
            },
            # Bronze collector - u privatnom subnetu, izlaz ka X API-ju
            # preko NAT-a, kontrolisano collector_sg iz NetworkStack-a.
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[security_group],
        )

        tw_url = twitter_function.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.AWS_IAM,
            cors=_lambda.FunctionUrlCorsOptions(
                allowed_origins=["*"]
            )
        )



        CfnOutput(
            self,
            "TwitterFunctionUrl",
            value=tw_url.url
        )