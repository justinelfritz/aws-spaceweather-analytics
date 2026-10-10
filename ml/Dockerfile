# Trains the Kp(t+3h)/Dst(t+3h) models as a SageMaker Training Job.
#
# Follows the classic SageMaker "bring your own container" contract: the
# service runs `docker run <image> train`, so the entrypoint below ignores
# whatever argument SageMaker appends and instead reads its config from
# environment variables (set by infra/stacks/ml_stack.py's Training Job
# definition) -- simpler than adopting the SageMaker Training Toolkit's
# hyperparameters.json convention for a script this small.
FROM python:3.11-slim

WORKDIR /opt/program

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY data.py features.py train.py sagemaker_entrypoint.sh ./
RUN chmod +x sagemaker_entrypoint.sh

ENTRYPOINT ["./sagemaker_entrypoint.sh"]
