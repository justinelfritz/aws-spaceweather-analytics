import { useState } from "react";
import Plot from "react-plotly.js";
import { ApiError, fetchSeaResult } from "../api.js";
import { FIELD_LABELS, FIELDS_IN_DISPLAY_ORDER, NORMALIZATION_LABELS, NORMALIZATIONS } from "../constants.js";
import { usePlotTheme } from "../theme.js";
import StatusMessage from "./StatusMessage.jsx";

// normalized_amplitude divides a baseline deviation by that same baseline's
// own standard deviation -- both in the field's original units, so they
// cancel. The result is a dimensionless z-score, not a quantity in the
// field's units, so the axis label needs to drop the unit and say so
// (raw and baseline_deviation both stay in the field's real units --
// subtracting a mean doesn't change units -- so they keep the label as-is).
function yAxisTitle(field, normalization) {
  const label = FIELD_LABELS[field];
  if (normalization !== "normalized_amplitude") return label;
  return `${label.replace(/\s*\[[^\]]*\]$/, "")} (Z-Score)`;
}

function bandTraces(offsets, color) {
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
      fillcolor: color,
      line: { width: 0 },
    },
    {
      x,
      y: offsets.map((o) => o.mean),
      name: "mean",
      type: "scatter",
      mode: "lines",
      line: { color: "#aa3bff", width: 2 },
    },
    {
      x,
      y: offsets.map((o) => o.median),
      name: "median",
      type: "scatter",
      mode: "lines",
      line: { color: "#e05555", width: 2, dash: "dot" },
    },
  ];
}

export default function SeaVisualization() {
  const [field, setField] = useState("dst_index");
  const [normalization, setNormalization] = useState("raw");
  const [state, setState] = useState({ status: "idle" });
  const theme = usePlotTheme();

  async function runQuery(event) {
    event.preventDefault();
    setState({ status: "loading" });
    try {
      const result = await fetchSeaResult(field, normalization);
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
      <h2>Superposed epoch analysis</h2>
      <p className="section-note">
        Storms in the DONKI catalog aligned on onset time (offset = 0 on the x-axis), showing how a
        field typically evolves before and after a geomagnetic storm, averaged across every cataloged
        event.
      </p>
      <form className="controls" onSubmit={runQuery}>
        <div className="control-row">
          <label>
            Field
            <select value={field} onChange={(e) => setField(e.target.value)}>
              {FIELDS_IN_DISPLAY_ORDER.map((f) => (
                <option key={f} value={f}>
                  {FIELD_LABELS[f]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Normalization
            <select value={normalization} onChange={(e) => setNormalization(e.target.value)}>
              {NORMALIZATIONS.map((n) => (
                <option key={n} value={n}>
                  {NORMALIZATION_LABELS[n]}
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
          No SEA result has been computed yet for <code>{field}</code>/<code>{normalization}</code>.
          The weekly SEA job currently only runs <code>dst_index</code>/<code>raw</code> &mdash; try
          that combination.
        </StatusMessage>
      )}
      {state.status === "ready" && (
        <>
          <Plot
            data={bandTraces(state.result.offsets, "rgba(170, 59, 255, 0.15)")}
            layout={{
              autosize: true,
              margin: { t: 20, r: 30, l: 60, b: 40 },
              font: theme.font,
              xaxis: { title: { text: "Hours from Storm Onset [h]" }, gridcolor: theme.gridcolor, zeroline: true },
              yaxis: {
                title: { text: yAxisTitle(state.result.field, state.result.normalization) },
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
            {state.result.event_count} events &middot; generated{" "}
            {new Date(state.result.generated_at).toLocaleString()}
          </p>
        </>
      )}
    </section>
  );
}
