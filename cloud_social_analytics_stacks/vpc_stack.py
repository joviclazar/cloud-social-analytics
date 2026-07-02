from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    custom_resources as cr,
)
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "SocialAnalyticsVpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        self.s3_endpoint = self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )

        self.secrets_endpoint = self.vpc.add_interface_endpoint(
            "SecretsManagerEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
        )

        self.events_endpoint = self.vpc.add_interface_endpoint(
            "EventsEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_EVENTS,
        )

        s3_prefix_list_lookup = cr.AwsCustomResource(
            self,
            "S3PrefixListLookup",
            on_update=cr.AwsSdkCall(
                service="EC2",
                action="describeManagedPrefixLists",
                parameters={
                    "Filters": [
                        {
                            "Name": "prefix-list-name",
                            "Values": [f"com.amazonaws.{self.region}.s3"],
                        }
                    ]
                },
                physical_resource_id=cr.PhysicalResourceId.of("S3PrefixListLookup"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
        )
        s3_prefix_list_id = s3_prefix_list_lookup.get_response_field(
            "PrefixLists.0.PrefixListId"
        )

        self.collector_sg = ec2.SecurityGroup(
            self,
            "CollectorLambdaSG",
            vpc=self.vpc,
            description="Bronze collector Lambda - HTTPS ka eksternim API-jima (HN/X) i S3",
            allow_all_outbound=False,
        )
        self.collector_sg.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "HTTPS ka eksternim API-jima (preko NAT-a) i S3 endpoint-u",
        )

        self.processing_sg = ec2.SecurityGroup(
            self,
            "ProcessingLambdaSG",
            vpc=self.vpc,
            description="Silver/Gold Lambde - samo S3 pristup preko gateway endpoint-a",
            allow_all_outbound=False,
        )
        self.processing_sg.add_egress_rule(
            ec2.Peer.prefix_list(s3_prefix_list_id),
            ec2.Port.tcp(443),
            "HTTPS ka S3 preko gateway endpoint-a (ograničeno na S3 IP opseg)",
        )

        self.db_loader_sg = ec2.SecurityGroup(
            self,
            "DbLoaderLambdaSG",
            vpc=self.vpc,
            description="Loader Lambda - S3 pristup + upis u Postgres na EC2",
            allow_all_outbound=False,
        )
        self.db_loader_sg.add_egress_rule(
            ec2.Peer.prefix_list(s3_prefix_list_id),
            ec2.Port.tcp(443),
            "HTTPS ka S3 preko gateway endpoint-a (ograničeno na S3 IP opseg)",
        )

        self.ec2_db_sg = ec2.SecurityGroup(
            self,
            "Ec2DatabaseSG",
            vpc=self.vpc,
            description="EC2 (Postgres + Superset) - ingress samo od Loader Lambde i admin pristupa",
            allow_all_outbound=False,
        )
        self.ec2_db_sg.add_ingress_rule(
            self.db_loader_sg,
            ec2.Port.tcp(5432),
            "Postgres pristup samo od Loader Lambde",
        )

        self.ec2_db_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(8088),
            "Superset UI - pristup sa bilo kog IP-a",
        )
        self.ec2_db_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(22),
            "SSH - pristup sa bilo kog IP-a",
        )
        self.ec2_db_sg.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "HTTPS - za instalaciju paketa (apt/pip) prilikom setup-a",
        )

        self.db_loader_sg.add_egress_rule(
            self.ec2_db_sg,
            ec2.Port.tcp(5432),
            "Postgres konekcija ka EC2 instanci",
        )

        self.secrets_endpoint.connections.allow_from(
            self.db_loader_sg, ec2.Port.tcp(443), "Pristup Postgres kredencijalima"
        )

        for sg in (self.collector_sg, self.processing_sg, self.db_loader_sg):
            self.events_endpoint.connections.allow_from(
                sg, ec2.Port.tcp(443), "Slanje evenata na EventBridge"
            )