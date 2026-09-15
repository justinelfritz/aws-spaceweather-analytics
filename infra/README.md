# infra

CDK (Python) app for the Space Weather Analytics platform.

## Stacks

- `SpaceWeather-Storage-<stage>` — S3 raw/curated buckets, Glue catalog
- `SpaceWeather-Ingestion-<stage>` — ingestion Lambdas, EventBridge schedules, DLQ
- `SpaceWeather-Sea-<stage>` — superposed epoch analysis pipeline
- `SpaceWeather-Ml-<stage>` — forecasting pipeline (SageMaker)
- `SpaceWeather-Api-<stage>` — API Gateway (REST), Cognito
- `SpaceWeather-Frontend-<stage>` — S3 + CloudFront hosting for the dashboard

`<stage>` defaults to `dev`; pass `-c stage=prod` to target prod.

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

## Useful commands

- `cdk synth` — emit the synthesized CloudFormation templates
- `cdk diff` — compare deployed stacks with current state
- `cdk deploy --all -c stage=dev` — deploy all stacks to the dev stage
- `python -m pytest tests/` — run unit tests
