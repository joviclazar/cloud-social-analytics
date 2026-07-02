from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_secretsmanager as secretsmanager,
    aws_events as events,
    aws_events_targets as targets,
    BundlingOptions,
    Duration,
    aws_s3 as s3,
)
from constructs import Construct


class AnalyticsVisualizationStack(Stack):
    def __init__(self, scope: Construct, id: str, data_lake: s3.IBucket, **kwargs):
        super().__init__(scope, id, **kwargs)

        # Default VPC
        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        db_secret = secretsmanager.Secret(
            self,
            "AnalyticsDbSecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"admin"}',
                generate_string_key="password",
                exclude_punctuation=True,
                password_length=24,
            ),
        )

        superset_secret = secretsmanager.Secret(
            self,
            "SupersetSecretKey",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=48,
                exclude_punctuation=True,
            ),
        )

        sg = ec2.SecurityGroup(self, "AnalyticsSG", vpc=vpc, allow_all_outbound=True)
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.all_tcp())
        # sg.add_ingress_rule(ec2.Peer.ipv4("TVOJ.IP.ADRESA/32"), ec2.Port.tcp(8088))
        # sg.add_ingress_rule(ec2.Peer.ipv4("TVOJ.IP.ADRESA/32"), ec2.Port.tcp(22))
        # # 5432 samo unutar VPC-a da lambda može da piše:
        # sg.add_ingress_rule(ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(5432))

        # IAM rola za EC2
        ec2_role = iam.Role(
            self, "AnalyticsInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        )
        db_secret.grant_read(ec2_role)
        superset_secret.grant_read(ec2_role)

        lambda_role = iam.Role(
            self, "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )
        data_lake.grant_read(lambda_role)
        db_secret.grant_read(lambda_role)

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -eux",

            "if [ -f /etc/superset/.provisioned ]; then "
            "echo 'Vec provisionovano, preskacem setup'; exit 0; fi",

            "dnf update -y",
            "dnf install -y postgresql15 postgresql15-server postgresql15-contrib "
            "python3.11 python3.11-pip python3.11-devel gcc gcc-c++ make "
            "libffi-devel openssl-devel cyrus-sasl-devel openldap-devel jq",

            "postgresql-setup --initdb",
            "systemctl enable postgresql",
            "systemctl start postgresql",

            f"SECRET_JSON=$(aws secretsmanager get-secret-value "
            f"--secret-id {db_secret.secret_arn} --region {self.region} "
            f"--query SecretString --output text)",
            "DB_USER=$(echo \"$SECRET_JSON\" | jq -r .username)",
            "DB_PASS=$(echo \"$SECRET_JSON\" | jq -r .password)",

            f"SUPERSET_SECRET=$(aws secretsmanager get-secret-value "
            f"--secret-id {superset_secret.secret_arn} --region {self.region} "
            f"--query SecretString --output text)",

            "sudo -u postgres psql -tAc "
            "\"SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'\" | grep -q 1 || "
            "sudo -u postgres psql -v ON_ERROR_STOP=1 "
            "-c \"CREATE ROLE ${DB_USER} WITH LOGIN SUPERUSER PASSWORD '${DB_PASS}';\"",

            "sudo -u postgres psql -tAc "
            "\"SELECT 1 FROM pg_database WHERE datname='analytics'\" | grep -q 1 || "
            "sudo -u postgres psql -v ON_ERROR_STOP=1 "
            "-c \"CREATE DATABASE analytics OWNER ${DB_USER};\"",

            "sudo -u postgres psql -tAc "
            "\"SELECT 1 FROM pg_database WHERE datname='superset_meta'\" | grep -q 1 || "
            "sudo -u postgres psql -v ON_ERROR_STOP=1 "
            "-c \"CREATE DATABASE superset_meta OWNER ${DB_USER};\"",

            "HBA=$(sudo -u postgres psql -tAc \"show hba_file;\")",
            "grep -q '0.0.0.0/0 md5' \"$HBA\" || "
            "echo \"host all all 0.0.0.0/0 md5\" | sudo tee -a \"$HBA\"",
            "sudo sed -i \"s/^#listen_addresses.*/listen_addresses = '*'/\" "
            "$(sudo -u postgres psql -tAc \"show config_file;\")",
            "systemctl restart postgresql",

            "python3.11 -m venv /opt/superset-venv",
            "/opt/superset-venv/bin/pip install --upgrade pip",

            "/opt/superset-venv/bin/pip install apache-superset pg8000 psycopg2-binary gunicorn",

            "mkdir -p /etc/superset",
            "cat > /etc/superset/superset_config.py <<EOF\n"
            "SECRET_KEY = '${SUPERSET_SECRET}'\n"
            "SQLALCHEMY_DATABASE_URI = 'postgresql+pg8000://${DB_USER}:${DB_PASS}@localhost/superset_meta'\n"
            "EOF",

            "export SUPERSET_CONFIG_PATH=/etc/superset/superset_config.py",
            "export FLASK_APP=superset",
            "/opt/superset-venv/bin/superset db upgrade",
            "/opt/superset-venv/bin/superset fab create-admin "
            "--username admin --firstname admin --lastname admin "
            "--email admin@local.com --password \"${DB_PASS}\"",
            "/opt/superset-venv/bin/superset init",

            "/opt/superset-venv/bin/superset set-database-uri "
            "--database_name \"AnalyticsDB\" "
            "--uri \"postgresql+pg8000://${DB_USER}:${DB_PASS}@localhost/analytics\"",

            "cat > /etc/systemd/system/superset.service <<EOF\n"
            "[Unit]\nDescription=Apache Superset\nAfter=network.target postgresql.service\n\n"
            "[Service]\nEnvironment=SUPERSET_CONFIG_PATH=/etc/superset/superset_config.py\n"
            "ExecStart=/opt/superset-venv/bin/gunicorn -w 4 -b 0.0.0.0:8088 'superset.app:create_app()'\n"
            "Restart=always\nUser=root\n\n[Install]\nWantedBy=multi-user.target\nEOF",
            "systemctl daemon-reload",
            "systemctl enable superset",
            "systemctl start superset",

            "touch /etc/superset/.provisioned",
        )

        instance = ec2.Instance(
            self,
            "AnalyticsInstance",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            associate_public_ip_address=True,
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            security_group=sg,
            role=ec2_role,
            user_data=user_data,
        )

        pg_conn = (
            "postgresql+pg8000://"
            f"{db_secret.secret_value_from_json('username').unsafe_unwrap()}:"
            f"{db_secret.secret_value_from_json('password').unsafe_unwrap()}"
            f"@{instance.instance_public_dns_name}:5432/analytics"
        )

        aws_sdk_pandas_layer = _lambda.LayerVersion.from_layer_version_arn(
            self,
            "AwsSdkPandasLayer",
            f"arn:aws:lambda:{self.region}:336392948345:layer:AWSSDKPandas-Python311:21",
        )

        lambda_bundling = BundlingOptions(
            image=_lambda.Runtime.PYTHON_3_11.bundling_image,
            command=[
                "bash", "-c",
                "pip install --no-cache-dir sqlalchemy pg8000 -t /asset-output && cp -au . /asset-output",
            ],
        )

        # Inkrementalna lambda - čita samo najnoviji parquet fajl po metrici.
        # Okida se automatski svakog dana preko EventBridge rule ispod.
        incremental_lambda = _lambda.Function(
            self,
            "GoldToPostgresLambda",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="lambda_incremental_handler.lambda_handler",
            code=_lambda.Code.from_asset(
                "lambdas/analyticsVisualization",
                bundling=lambda_bundling,
            ),
            layers=[aws_sdk_pandas_layer],
            timeout=Duration.minutes(10),
            memory_size=1024,
            role=lambda_role,
            environment={
                "S3_BUCKET": data_lake.bucket_name,
                "S3_PREFIX": "gold/",
                "PG_CONN": pg_conn,
            },
        )

        # EventBridge rule: pokreće inkrementalnu lambdu svakog dana u 15h.
        # Napomena: cron izrazi u EventBridge-u su uvek u UTC i ne prate
        # letnje/zimsko računanje vremena. 13:00 UTC odgovara 15:00 po
        # centralnoevropskom letnjem vremenu (CEST, UTC+2); zimi (CET,
        # UTC+1) će ovo okinuti u 14:00 po lokalnom vremenu. Ako ti treba
        # da uvek bude tačno 15h po lokalnom vremenu tokom cele godine,
        # potrebno je ručno menjati cron izraz dva puta godišnje.
        daily_backfill_schedule = events.Rule(
            self,
            "GoldToPostgresDailySchedule",
            schedule=events.Schedule.cron(minute="0", hour="13"),
        )
        daily_backfill_schedule.add_target(targets.LambdaFunction(incremental_lambda))

        # Full-backfill lambda - čita SVE parquet fajlove po metrici i
        # popunjava postgres od nule. Namerno nema nikakav trigger
        # (ni EventBridge, ni S3 event) - poziva se isključivo ručno,
        # preko AWS konzole (Lambda -> Test) ili CLI-ja, kad zatreba
        # potpuni reload podataka.
        _lambda.Function(
            self,
            "GoldToPostgresFullBackfillLambda",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="lambda_full_backfill.lambda_handler",
            code=_lambda.Code.from_asset(
                "lambdas/analyticsVisualization",
                bundling=lambda_bundling,
            ),
            layers=[aws_sdk_pandas_layer],
            timeout=Duration.minutes(15),
            memory_size=1024,
            role=lambda_role,
            environment={
                "S3_BUCKET": data_lake.bucket_name,
                "S3_PREFIX": "gold/",
                "PG_CONN": pg_conn,
            },
        )