#!/bin/sh
# SageMaker runs `docker run <image> train`; this script ignores that "train"
# argument and instead builds train.py's CLI from environment variables set
# by the Training Job definition (infra/stacks/ml_stack.py). Model artifacts
# go to /opt/ml/model, which SageMaker automatically tars and uploads to the
# Training Job's OutputDataConfig S3 location once the container exits.
set -eu

exec python train.py \
  --curated-bucket "$CURATED_BUCKET" \
  --model-dir /opt/ml/model \
  --horizon-hours "${HORIZON_HOURS:-3}" \
  --train-start-year "${TRAIN_START_YEAR:-1995}" \
  --train-end-year "${TRAIN_END_YEAR:-2020}" \
  --test-start-year "${TEST_START_YEAR:-2021}" \
  --test-end-year "${TEST_END_YEAR:-2025}"
