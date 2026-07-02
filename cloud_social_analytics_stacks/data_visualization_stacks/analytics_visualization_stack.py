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
    def __init__(
        self,
        scope: Construct,
        id: str,
        data_lake: s3.IBucket,
        vpc: ec2.IVpc,
        ec2_security_group: ec2.ISecurityGroup,
        lambda_security_group: ec2.ISecurityGroup,
        **kwargs,
    ):
        super().__init__(scope, id, **kwargs)

        # vpc, ec2_security_group (network_stack.ec2_db_sg) i
        # lambda_security_group (network_stack.db_loader_sg) dolaze iz
        # NetworkStack-a - ne kreiramo ih ovde, samo ih koristimo.

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

        # IAM rola za EC2
        ec2_role = iam.Role(
            self, "AnalyticsInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        )
        db_secret.grant_read(ec2_role)
        superset_secret.grant_read(ec2_role)
        # Opciono: omogućava SSM Session Manager pristup instanci umesto
        # (ili pored) SSH-a - ne otvara nikakav port, kontroliše se IAM-om.
        ec2_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )

        # Zaseban role po Lambda funkciji (incremental vs. full backfill),
        # umesto jednog deljenog - manji blast radius ako se jedna
        # kompromituje, i jasnija priča za "least privilege" na odbrani.
        def make_lambda_role(id_suffix: str) -> iam.Role:
            role = iam.Role(
                self,
                f"LambdaRole{id_suffix}",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaBasicExecutionRole"
                    ),
                    # Obavezno za Lambdu u VPC-u (ENI management).
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaVPCAccessExecutionRole"
                    ),
                ],
            )
            data_lake.grant_read(role)
            # Lambda dobija SAMO dozvolu da PROČITA secret preko boto3 u
            # runtime-u - ne dobija lozinku upisanu u plaintext env varijablu.
            db_secret.grant_read(role)
            return role

        incremental_role = make_lambda_role("Incremental")
        full_backfill_role = make_lambda_role("FullBackfill")

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -eux",

            "if [ -f /etc/superset/.provisioned ]; then "
            "echo 'Vec provisionovano, preskacem setup'; exit 0; fi",

            # ------------------------
            # SYSTEM PACKAGES
            # ------------------------
            "dnf update -y",
            "dnf install -y postgresql15 postgresql15-server postgresql15-contrib "
            "python3.11 python3.11-pip python3.11-devel gcc gcc-c++ make "
            "libffi-devel openssl-devel cyrus-sasl-devel openldap-devel jq",

            # ------------------------
            # POSTGRES INIT
            # ------------------------
            "postgresql-setup --initdb",
            "systemctl enable postgresql",
            "systemctl start postgresql",

            # ------------------------
            # SECRETS
            # ------------------------
            f"SECRET_JSON=$(aws secretsmanager get-secret-value "
            f"--secret-id {db_secret.secret_arn} --region {self.region} "
            f"--query SecretString --output text)",

            "DB_USER=$(echo \"$SECRET_JSON\" | jq -r .username)",
            "DB_PASS=$(echo \"$SECRET_JSON\" | jq -r .password)",

            f"SUPERSET_SECRET=$(aws secretsmanager get-secret-value "
            f"--secret-id {superset_secret.secret_arn} --region {self.region} "
            f"--query SecretString --output text)",

            # ------------------------
            # FIX pg_hba.conf (ISPRAVLJENO)
            # ------------------------
            "HBA=$(sudo -u postgres psql -tAc \"show hba_file;\")",

            # SAMO ident -> md5; PEER OSTAVLJAMO za lokalne socket konekcije
            "sed -i 's/ident/md5/g' \"$HBA\" || true",

            # eksplicitno dozvoli TCP konekcije uz md5
            f"echo \"host all all 127.0.0.1/32 md5\" | sudo tee -a \"$HBA\"",
            f"echo \"host all all ::1/128 md5\" | sudo tee -a \"$HBA\"",
            f"echo \"host all all {vpc.vpc_cidr_block} md5\" | sudo tee -a \"$HBA\"",

            # ------------------------
            # POSTGRES CONFIG
            # ------------------------
            "sudo sed -i \"s/^#listen_addresses.*/listen_addresses = '*'/\" "
            "$(sudo -u postgres psql -tAc \"show config_file;\")",

            "systemctl restart postgresql",

            # ------------------------
            # DB + ROLE SETUP (SAFE IDENTITY)
            # ------------------------
            "sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL\n"
            "DO \\$\\$\n"
            "BEGIN\n"
            "   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN\n"
            "      CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}' SUPERUSER;\n"
            "   END IF;\n"
            "END\n"
            "\\$\\$;\n"
            "\n"
            "DO \\$\\$\n"
            "BEGIN\n"
            "   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'analytics') THEN\n"
            "      CREATE DATABASE analytics OWNER ${DB_USER};\n"
            "   END IF;\n"
            "END\n"
            "\\$\\$;\n"
            "\n"
            "DO \\$\\$\n"
            "BEGIN\n"
            "   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'superset_meta') THEN\n"
            "      CREATE DATABASE superset_meta OWNER ${DB_USER};\n"
            "   END IF;\n"
            "END\n"
            "\\$\\$;\n"
            "SQL",

            # ------------------------
            # PYTHON ENV
            # ------------------------
            "python3.11 -m venv /opt/superset-venv",
            "/opt/superset-venv/bin/pip install --upgrade pip",
            "/opt/superset-venv/bin/pip install apache-superset pg8000 psycopg2-binary gunicorn rich",

            # ------------------------
            # SUPERSET CONFIG
            # ------------------------
            "mkdir -p /etc/superset",
            "cat > /etc/superset/superset_config.py <<EOF\n"
            "SECRET_KEY = '${SUPERSET_SECRET}'\n"
            "SQLALCHEMY_DATABASE_URI = 'postgresql+pg8000://${DB_USER}:${DB_PASS}@127.0.0.1:5432/superset_meta'\n"
            "EOF",

            "export SUPERSET_CONFIG_PATH=/etc/superset/superset_config.py",
            "export FLASK_APP=superset",

            # ------------------------
            # SUPERSET INIT
            # ------------------------
            "/opt/superset-venv/bin/superset db upgrade",
            "/opt/superset-venv/bin/superset fab create-admin "
            "--username admin --firstname admin --lastname admin "
            "--email admin@local.com --password \"${DB_PASS}\" || true",
            "/opt/superset-venv/bin/superset init",

            # ------------------------
            # ANALYTICS DB REGISTRATION
            # ------------------------
            "/opt/superset-venv/bin/superset set-database-uri "
            "--database_name \"AnalyticsDB\" "
            "--uri \"postgresql+pg8000://${DB_USER}:${DB_PASS}@127.0.0.1:5432/analytics\" || true",

            # ------------------------
            # SYSTEMD
            # ------------------------
            "cat > /etc/systemd/system/superset.service <<EOF\n"
            "[Unit]\nDescription=Apache Superset\nAfter=network.target postgresql.service\n\n"
            "[Service]\nEnvironment=SUPERSET_CONFIG_PATH=/etc/superset/superset_config.py\n"
            "ExecStart=/opt/superset-venv/bin/gunicorn -w 4 -b 0.0.0.0:8088 'superset.app:create_app()'\n"
            "Restart=always\nUser=root\n\n[Install]\nWantedBy=multi-user.target\nEOF",

            "systemctl daemon-reload",
            "systemctl enable superset",
            "systemctl start superset",

            # ------------------------
            # DONE FLAG
            # ------------------------
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
            security_group=ec2_security_group,
            role=ec2_role,
            user_data=user_data,
            user_data_causes_replacement=True,
        )

        aws_sdk_pandas_layer = _lambda.LayerVersion.from_layer_version_arn(
            self,
            "AwsSdkPandasLayer",
            f"arn:aws:lambda:{self.region}:336392948345:layer:AWSSDKPandas-Python311:21",
        )

        lambda_bundling = BundlingOptions(
            image=_lambda.Runtime.PYTHON_3_11.bundling_image,
            # user="root": na Windows-u (Docker Desktop, WSL2 backend)
            # podrazumevani CDK non-root korisnik (uid 1000) nema write
            # pristup mapiranom /asset-output folderu, pa pip install
            # puca sa "docker exited with status 1" bez jasne poruke u
            # izlazu. Pokretanje kao root u kontejneru to zaobilazi.
            user="root",
            command=[
                "bash", "-c",
                "pip install --no-cache-dir sqlalchemy pg8000 -t /asset-output && cp -au . /asset-output",
            ],
        )

        # Lambda NE dobija lozinku u env varijabli. Dobija samo host, port,
        # ime baze i ARN secreta - lozinku sama povlači preko boto3 u
        # runtime-u (secretsmanager.get_secret_value), zahvaljujući
        # db_secret.grant_read() koji je već dodeljen roli iznad.
        # Konekcija ide preko PRIVATNOG DNS imena instance (ne javnog),
        # jer je Lambda sad u istom VPC-u kao i EC2 instanca.
        pg_env = {
            "S3_BUCKET": data_lake.bucket_name,
            "S3_PREFIX": "gold/",
            "PG_HOST": instance.instance_private_dns_name,
            "PG_PORT": "5432",
            "PG_DATABASE": "analytics",
            "DB_SECRET_ARN": db_secret.secret_arn,
        }

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
            role=incremental_role,
            environment=pg_env,
            # Lambda je u istom VPC-u kao Postgres, koristi db_loader_sg
            # (egress: S3 preko prefix liste + 5432 ka ec2_db_sg).
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_security_group],
        )
        # Instanca mora biti spremna pre nego što Lambda pokuša da se poveže.
        incremental_lambda.node.add_dependency(instance)

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
        full_backfill_lambda = _lambda.Function(
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
            role=full_backfill_role,
            environment=pg_env,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_security_group],
        )
        full_backfill_lambda.node.add_dependency(instance)