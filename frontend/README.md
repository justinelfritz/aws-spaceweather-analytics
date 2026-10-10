# Space weather analytics dashboard

React + Vite + Plotly.js frontend for the space weather analytics platform.
Three views, each backed by the API in [`../infra/stacks/api_stack.py`](../infra/stacks/api_stack.py)
(see [`../docs/api-reference.md`](../docs/api-reference.md)):

- **Historical explorer** — date-range query over the curated OMNI2 hourly
  dataset (1963–present), with DONKI geomagnetic storm onsets overlaid.
- **Superposed epoch analysis** — mean/median/percentile bands of a field's
  behavior aligned on storm onset, across the full DONKI catalog.
- **Forecast skill** — the ML model's backtested t+3h prediction error,
  aligned the same way. Not a live forecast — see the todo's section 8 notes
  on why a live-prediction view was descoped.

## Local development

```sh
cp .env.example .env   # then set VITE_API_BASE_URL to a deployed API's URL
npm install
npm run dev
```

## Build & deploy

Vite bakes `VITE_API_BASE_URL` into the build at build time, so `.env` must
point at the right stage's API *before* building:

```sh
echo "VITE_API_BASE_URL=$(aws cloudformation describe-stacks \
    --stack-name SpaceWeather-Api-<stage> --profile sw-bootstrap \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
    --output text)" > .env
npm run build
cd ../infra && cdk deploy SpaceWeather-Frontend-<stage>
```

`cdk deploy` uploads `dist/` to the stack's S3 bucket and invalidates the
CloudFront distribution automatically (see `infra/stacks/frontend_stack.py`).
