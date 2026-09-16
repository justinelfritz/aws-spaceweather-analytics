import { useMemo, useState } from "react";
import Plot from "react-plotly.js";
import { ApiError, fetchEvents, fetchHistorical } from "../api.js";
import { QUERYABLE_FIELDS } from "../constants.js";
import { usePlotTheme } from "../theme.js";
import StatusMessage from "./StatusMessage.jsx";

const DEFAULT_FIELDS = ["kp", "dst_index"];
// The 2015-03-17 "St. Patrick's Day storm" (G4) -- picked as the default
// range because, unlike earlier storms, it's covered by both the OMNI2
// data (1963-present) *and* the DONKI event catalog (2010-present), so the
// view shows a real storm-onset marker on first load instead of an empty one.
const DEFAULT_START = "2015-03-15";
const DEFAULT_END = "2015-03-20";

export default function HistoricalExplorer() {
  const [fields, setFields] = useState(DEFAULT_FIELDS);
  const [start, setStart] = useState(DEFAULT_START);
  const [end, setEnd] = useState(DEFAULT_END);
  const [state, setState] = useState({ status: "idle" });
  const theme = usePlotTheme();

  function toggleField(field) {
    setFields((prev) => (prev.includes(field) ? prev.filter((f) => f !== field) : [...prev, field]));
  }

  async function runQuery(event) {
    event.preventDefault();
    if (fields.length === 0) {
      setState({ status: "error", message: "select at least one field" });
      return;
    }
    setState({ status: "loading" });
    try {
      const [historical, events] = await Promise.all([
        fetchHistorical({ fields, start, end }),
        fetchEvents({ start, end }),
      ]);
      setState({ status: "ready", historical, events });
    } catch (err) {
      setState({ status: "error", message: err instanceof ApiError ? err.message : "request failed" });
    }
  }

  const plot = useMemo(() => {
    if (state.status !== "ready") return null;
    const { historical, events } = state;
    const timestamps = historical.data.map((row) => row.timestamp);
    const traces = historical.fields.map((field, index) => ({
      x: timestamps,
      y: historical.data.map((row) => row[field]),
      name: field,
      type: "scatter",
      mode: "lines",
      yaxis: index === 0 ? "y" : "y2",
    }));
    const stormLines = events.events.map((eventRecord) => ({
      type: "line",
      xref: "x",
      x0: eventRecord.start_time,
      x1: eventRecord.start_time,
      yref: "paper",
      y0: 0,
      y1: 1,
      line: { color: "#e05555", width: 1, dash: "dot" },
    }));
    return { traces, stormLines, eventCount: events.count };
  }, [state]);

  return (
    <section>
      <h2>Historical explorer</h2>
      <p className="section-note">
        Range query over the curated OMNI2 hourly dataset (1963&ndash;present). Dotted red lines mark
        DONKI-cataloged geomagnetic storm onsets within the selected range.
      </p>
      <form className="controls" onSubmit={runQuery}>
        <div className="control-row">
          <label>
            Start
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label>
            End
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <button type="submit">Query</button>
        </div>
        <fieldset>
          <legend>Fields</legend>
          <div className="field-grid">
            {QUERYABLE_FIELDS.map((field) => (
              <label key={field} className="checkbox">
                <input type="checkbox" checked={fields.includes(field)} onChange={() => toggleField(field)} />
                {field}
              </label>
            ))}
          </div>
        </fieldset>
      </form>

      {state.status === "loading" && <StatusMessage kind="loading">Loading&hellip;</StatusMessage>}
      {state.status === "error" && <StatusMessage kind="error">{state.message}</StatusMessage>}
      {state.status === "ready" && state.historical.data.length === 0 && (
        <StatusMessage kind="empty">No data in this range.</StatusMessage>
      )}
      {state.status === "ready" && state.historical.data.length > 0 && plot && (
        <>
          <Plot
            data={plot.traces}
            layout={{
              autosize: true,
              margin: { t: 20, r: 60, l: 60, b: 40 },
              font: theme.font,
              xaxis: { title: "Time (UTC)", gridcolor: theme.gridcolor },
              yaxis: { title: state.historical.fields[0], gridcolor: theme.gridcolor },
              yaxis2:
                state.historical.fields.length > 1
                  ? { title: state.historical.fields.slice(1).join(" / "), overlaying: "y", side: "right" }
                  : undefined,
              shapes: plot.stormLines,
              legend: { orientation: "h" },
              paper_bgcolor: "transparent",
              plot_bgcolor: "transparent",
            }}
            useResizeHandler
            style={{ width: "100%", height: "480px" }}
            config={{ responsive: true, displaylogo: false }}
          />
          <p className="section-note">
            {state.historical.count} points &middot; {plot.eventCount} storm marker(s) in range
          </p>
        </>
      )}
    </section>
  );
}
