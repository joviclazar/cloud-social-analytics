#!/usr/bin/env python3
import os
from aws_cdk import App, Environment, Aspects
from cloud_social_analytics_stacks.bronze_layer_stacks.data_stack.data_stack import DataStack
from cloud_social_analytics_stacks.bronze_layer_stacks.function_stacks.twitter_fetcher_function_stack import TwitterFetcherFunctionStack
from cloud_social_analytics_stacks.bronze_layer_stacks.function_stacks.hacker_news_fetcher_function_stack import HackerNewsFetcherFunctionStack
from cloud_social_analytics_stacks.data_visualization_stacks.analytics_visualization_stack import AnalyticsVisualizationStack
from cloud_social_analytics_stacks.notifier_stack import NotifierStack
from cloud_social_analytics_stacks.silver_layer_stacks.hacker_news_posts_function_stack import HackerNewsPostsSilverStack
from cloud_social_analytics_stacks.silver_layer_stacks.hacker_news_posts_manual_function_stack import HackerNewsPostsManualSilverStack
from cloud_social_analytics_stacks.silver_layer_stacks.hacker_news_users_function_stack import HackerNewsUsersSilverStack
from cloud_social_analytics_stacks.silver_layer_stacks.hacker_news_users_manual_function_stack import HackerNewsUsersManualSilverStack
from cloud_social_analytics_stacks.silver_layer_stacks.twitter_posts_function_stack import TwitterPostsSilverStack
from cloud_social_analytics_stacks.silver_layer_stacks.twitters_users_function_stack import TwitterUsersSilverStack
from cloud_social_analytics_stacks.gold_layer_stacks.gold_hn_metrics_stack import GoldHnMetricsStack
from cloud_social_analytics_stacks.gold_layer_stacks.gold_twitter_metrics_stack import GoldTwitterMetricsStack
from aspects.lambda_alarm_aspect import LambdaAlarmAspect


from dotenv import load_dotenv
load_dotenv()


app = App()

env = Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"), 
    region="eu-central-1"
)

data_stack = DataStack(app, "SocialAnalyticsDataStack", env = env)

twitter_function_stack = TwitterFetcherFunctionStack(
    app,
    "SocialAnalyticsTwitterFunctionStack",
    data_stack.data_lake,
    env = env
)

hacker_news_function_stack = HackerNewsFetcherFunctionStack(
    app,
    "SocialAnalyticsHackerNewsFunctionStack",
    data_stack.data_lake,
    env = env
)

twitter_users_silver_stack = TwitterUsersSilverStack(
    app,
    "SocialAnalyticsTwitterUsersSilverStack",
    data_stack.data_lake,
    env = env
)

twitter_posts_silver_stack = TwitterPostsSilverStack(
    app,
    "SocialAnalyticsTwitterPostsSilverStack",
    data_stack.data_lake,
    env = env
)

hacker_news_users_manual_silver_stack = HackerNewsUsersManualSilverStack(
    app,
    "SocialAnalyticsHackerNewsUsersManualSilverStack",
    data_stack.data_lake,
    env = env
)

hacker_news_posts_manual_silver_stack = HackerNewsPostsManualSilverStack(
    app,
    "SocialAnalyticsHackerNewsPostsManualSilverStack",
    data_stack.data_lake,
    env = env
)

hacker_news_users_silver_stack = HackerNewsUsersSilverStack(
    app,
    "SocialAnalyticsHackerNewsUsersSilverStack",
    data_stack.data_lake,
    env = env
)

hacker_news_posts_silver_stack = HackerNewsPostsSilverStack(
    app,
    "SocialAnalyticsHackerNewsPostsSilverStack",
    data_stack.data_lake,
    env = env
)

gold_hn_metrics_stack = GoldHnMetricsStack(
    app,
    "SocialAnalyticsGoldHnMetricsStack",
    data_stack.data_lake,
    env = env
)

gold_twitter_metrics_stack = GoldTwitterMetricsStack(
    app,
    "SocialAnalyticsGoldTwitterMetricsStack",
    data_stack.data_lake,
    env = env
)

data_visualization_stack = AnalyticsVisualizationStack(
    app,
    "SocialAnalyticsDataVisualizationStack",
    data_stack.data_lake,
    env = env
)

notifier_stack = NotifierStack(app, "NotifierStack", env=env)

Aspects.of(app).add(
    LambdaAlarmAspect(
        notifier_stack.alarm_topic,
        exclude_ids={"DiscordNotifier"},
    )
)

app.synth()