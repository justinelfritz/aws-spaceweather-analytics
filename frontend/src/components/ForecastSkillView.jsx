import { useState } from "react";
import Plot from "react-plotly.js";
import { ApiError, fetchForecastSkill } from "../api.js";
import { ERROR_TYPES, FORECAST_TARGETS } from "../constants.js";
import { usePlotTheme } from "../theme.js";
import StatusMessage from "./StatusMessage.jsx";

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

export default function ForecastSkillView() {
  const [target, setTarget] = useState("kp");
  const [errorType, setErrorType] = useState("abs_error");
  const [state, setState] = useState({ status: "idle" });
  const theme = usePlotTheme();

  async function runQuery(event) {
    event.preventDefault();
    setState({ status: "loading" });
    try {
      const result = await fetchForecastSkill(target, errorType);
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
        Held-out backtest of the model's t+{state.result?.horizon_hours ?? "3"}h prediction error,
        aligned on the same storm catalog SEA uses. Offset 0 is storm onset; the band shows how much
        the forecast typically misses by at each point around a storm, not a live prediction.
      </p>
      <form className="controls" onSubmit={runQuery}>
        <div className="control-row">
          <label>
            Target
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              {FORECAST_TARGETS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label>
            Error type
            <select value={errorType} onChange={(e) => setErrorType(e.target.value)}>
              {ERROR_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <button type="submit">Load</button>
        </div>
      </form>

      {state.status === "loading" && <StatusMessage kind="loading">Loading&hellip;</StatusMessage>}
      {state.status === "error" && <StatusMessage kind="error">{state.message}</StatusMessage>}
      {state.status === "empty" && (
        <StatusMessage kind="empty">
          No forecast-skill result has been computed yet for <code>{target}</code>/
          <code>{errorType}</code>.
        </StatusMessage>
      )}
      {state.status === "ready" && (
        <>
          <Plot
            data={bandTraces(state.result.offsets)}
            layout={{
              autosize: true,
              margin: { t: 20, r: 30, l: 60, b: 40 },
              font: theme.font,
              xaxis: { title: { text: "Hours from storm onset" }, gridcolor: theme.gridcolor, zeroline: true },
              yaxis: {
                title: { text: `${state.result.target} ${state.result.error_type}` },
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
            {state.result.event_count} events &middot; model {state.result.model_version?.git_commit}{" "}
            trained on {state.result.model_version?.train_years} &middot; generated{" "}
            {new Date(state.result.generated_at).toLocaleString()}
          </p>
        </>
      )}
    </section>
  );
}
