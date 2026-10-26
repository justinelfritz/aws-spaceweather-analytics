import { useState } from "react";
import Plot from "react-plotly.js";
import { ApiError, fetchForecastSkill } from "../api.js";
import {
  CLASSIFICATION_TARGETS,
  ERROR_TYPE_LABELS,
  ERROR_TYPES,
  FIELD_LABELS,
  FORECAST_TARGETS,
  SEVERITY_BAND_LABELS,
  SEVERITY_BANDS,
} from "../constants.js";
import { usePlotTheme } from "../theme.js";
import StatusMessage from "./StatusMessage.jsx";

// Same threshold/wording convention as SeaVisualization.jsx's small-sample
// caveat (docs/sea-on-demand-design.md Decision 4) -- relevant here too
// since the "severe" severity band only has ~12 storms in the test period.
const SMALL_SAMPLE_THRESHOLD = 10;

// Same "name + parenthetical qualifier" convention as SeaVisualization.jsx's
// yAxisTitle -- unlike SEA's z-score case, the error stays in the target's
// own units (it's a plain difference, not a normalized/scaled quantity),
// so the unit is kept rather than stripped.
function yAxisTitle(target, errorType) {
  return `${FIELD_LABELS[target]} (${ERROR_TYPE_LABELS[errorType]})`;
}

function bandTraces(offsets) {
  const x = offsets.map((o) => o.offset);
  return [
    {
      x,
      y: offsets.map((o) => o.percentiles["75"]),
      type: "scatter",
      mode: "lines",
      line: { width: 0 },
      showlegend: false,
      hoverinfo: "skip",
    },
    {
      x,
      y: offsets.map((o) => o.percentiles["25"]),
      name: "25th–75th percentile",
      type: "scatter",
      mode: "lines",
      fill: "tonexty",
      fillcolor: "rgba(224, 85, 85, 0.15)",
      line: { width: 0 },
    },
    {
      x,
      y: offsets.map((o) => o.mean),
      name: "mean",
      type: "scatter",
      mode: "lines",
      line: { color: "#e05555", width: 2 },
    },
    {
      x,
      y: offsets.map((o) => o.median),
      name: "median",
      type: "scatter",
      mode: "lines",
      line: { color: "#aa3bff", width: 2, dash: "dot" },
    },
  ];
}

function pct(value) {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

// Classification results have no `offsets` -- they're a single whole-test-
// period summary (ml/classification_skill.py), not an epoch-aligned series,
// so they get their own render branch instead of a Plotly band chart.
function ClassificationSummary({ result }) {
  const cm = result.confusion_matrix;
  return (
    <div className="classification-summary">
      <p className="section-note">
        Storm defined as <code>{result.storm_definition}</code>, scored over all {result.row_count.toLocaleString()}{" "}
        hourly predictions in the held-out test period (not aligned on storm onset — see the note above).
      </p>
      <div className="metric-tiles">
        <div className="metric-tile">
          <span className="metric-label">Precision</span>
          <span className="metric-value">{pct(result.precision)}</span>
        </div>
        <div className="metric-tile">
          <span className="metric-label">Recall</span>
          <span className="metric-value">{pct(result.recall)}</span>
        </div>
        <div className="metric-tile">
          <span className="metric-label">F1</span>
          <span className="metric-value">{pct(result.f1)}</span>
        </div>
        <div className="metric-tile">
          <span className="metric-label">False alarm rate</span>
          <span className="metric-value">{pct(result.false_alarm_rate)}</span>
        </div>
      </div>
      <table className="confusion-matrix">
        <thead>
          <tr>
            <th></th>
            <th>Predicted storm</th>
            <th>Predicted quiet</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th>Actual storm</th>
            <td>{cm.tp.toLocaleString()}</td>
            <td>{cm.fn.toLocaleString()}</td>
          </tr>
          <tr>
            <th>Actual quiet</th>
            <td>{cm.fp.toLocaleString()}</td>
            <td>{cm.tn.toLocaleString()}</td>
          </tr>
        </tbody>
      </table>
      <p className="section-note">
        model {result.model_version?.git_commit} trained on {result.model_version?.train_years} &middot; generated{" "}
        {new Date(result.generated_at).toLocaleString()}
      </p>
    </div>
  );
}

export default function ForecastSkillView() {
  const [target, setTarget] = useState("kp");
  const [errorType, setErrorType] = useState("abs_error");
  const [severity, setSeverity] = useState("");
  const [state, setState] = useState({ status: "idle" });
  const theme = usePlotTheme();

  const isClassification = errorType === "classification";
  const targetOptions = isClassification ? CLASSIFICATION_TARGETS : FORECAST_TARGETS;

  function handleErrorTypeChange(nextErrorType) {
    setErrorType(nextErrorType);
    // ae_index has no classification result (no established storm
    // threshold -- see ml/classification_skill.py) -- fall back rather
    // than submit a request that can only 404.
    if (nextErrorType === "classification" && target === "ae_index") {
      setTarget("kp");
    }
  }

  async function runQuery(event) {
    event.preventDefault();
    setState({ status: "loading" });
    try {
      const result = await fetchForecastSkill(target, errorType, isClassification ? undefined : severity || undefined);
      setState({ status: "ready", result });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setState({ status: "empty" });
      } else {
        setState({ status: "error", message: err instanceof ApiError ? err.message : "request failed" });
      }
    }
  }

  return (
    <section>
      <h2>Forecast skill</h2>
      <p className="section-note">
        Held-out backtest of the model's t+{state.result?.horizon_hours ?? "3"}h prediction, aligned on the same
        storm catalog SEA uses (or, for storm classification, scored directly over the held-out test period). Offset
        0 is storm onset; this is a retrospective backtest, not a live prediction.
      </p>
      <form className="controls" onSubmit={runQuery}>
        <div className="control-row">
          <label>
            Target
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              {targetOptions.map((t) => (
                <option key={t} value={t}>
                  {FIELD_LABELS[t]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Error type
            <select value={errorType} onChange={(e) => handleErrorTypeChange(e.target.value)}>
              {ERROR_TYPES.map((t) => (
                <option key={t} value={t}>
                  {ERROR_TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </label>
          {!isClassification && (
            <label>
              Storm severity
              <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
                <option value="">All storms</option>
                {SEVERITY_BANDS.map((band) => (
                  <option key={band} value={band}>
                    {SEVERITY_BAND_LABELS[band]}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button type="submit">Load</button>
        </div>
      </form>

      {state.status === "loading" && <StatusMessage kind="loading">Loading&hellip;</StatusMessage>}
      {state.status === "error" && <StatusMessage kind="error">{state.message}</StatusMessage>}
      {state.status === "empty" && (
        <StatusMessage kind="empty">
          No forecast-skill result has been computed yet for <code>{target}</code>/
          <code>{errorType}</code>
          {severity && !isClassification ? (
            <>
              {" "}
              at severity <code>{severity}</code>
            </>
          ) : null}
          .
        </StatusMessage>
      )}
      {state.status === "ready" && state.result.offsets && (
        <>
          <Plot
            data={bandTraces(state.result.offsets)}
            layout={{
              autosize: true,
              margin: { t: 20, r: 30, l: 60, b: 40 },
              font: theme.font,
              xaxis: { title: { text: "Hours from Storm Onset [h]" }, gridcolor: theme.gridcolor, zeroline: true },
              yaxis: {
                title: { text: yAxisTitle(state.result.target, state.result.error_type) },
                gridcolor: theme.gridcolor,
              },
              legend: { orientation: "h" },
              paper_bgcolor: "transparent",
              plot_bgcolor: "transparent",
            }}
            useResizeHandler
            style={{ width: "100%", height: "440px" }}
            config={{ responsive: true, displaylogo: false }}
          />
          <p className="section-note">
            {state.result.event_count} events
            {state.result.severity ? ` (${SEVERITY_BAND_LABELS[state.result.severity]})` : ""} &middot; model{" "}
            {state.result.model_version?.git_commit} trained on {state.result.model_version?.train_years} &middot;
            generated {new Date(state.result.generated_at).toLocaleString()}
          </p>
          {state.result.event_count < SMALL_SAMPLE_THRESHOLD && (
            <p className="sample-caveat">
              Small sample ({state.result.event_count} storm{state.result.event_count === 1 ? "" : "s"}) &mdash;
              interpret with caution.
            </p>
          )}
        </>
      )}
      {state.status === "ready" && !state.result.offsets && <ClassificationSummary result={state.result} />}
    </section>
  );
}
