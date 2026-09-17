import { useMemo, useState } from "react";
import Plot from "react-plotly.js";
import { ApiError, fetchEvents, fetchHistorical } from "../api.js";
import { FIELD_GROUPS, FIELD_LABELS } from "../constants.js";
import { usePlotTheme } from "../theme.js";
import StatusMessage from "./StatusMessage.jsx";

const DEFAULT_FIELDS = ["kp", "dst_index"];

// Caps how many fields can be charted at once -- past this, the per-field
// y-axis stacking (see buildAxisLayout/AXIS_BAND below) eats too much of the
// plot's own width to stay readable. At AXIS_BAND=0.1, 6 fields means 4
// stacked extra axes (2 left + 2 right), shrinking the plot area by 0.4 --
// still comfortably plottable; much beyond that gets cramped fast.
const MAX_FIELDS = 6;

// One color per field, reused for its trace line and its y-axis's line/tick/
// title so a many-axis chart stays readable -- cycles if more fields than
// colors are ever selected (16 possible, 8 colors).
const AXIS_COLORS = ["#aa3bff", "#e05555", "#2ea3a3", "#e0a12b", "#4c72d8", "#8bc34a", "#c2185b", "#795548"];

// Fraction of the chart's own plotting area reserved per y-axis beyond the
// first two. The first field gets the normal left axis, the second the
// normal right axis (both anchored to the plot edge as before); the third
// and any further fields get their own free-floating axes, alternating
// right/left and stepping further out each time, inside a strip carved out
// of the x-axis's domain (the standard Plotly pattern for >2 y-axes).
const AXIS_BAND = 0.1;

function buildAxisLayout(fields, theme) {
  const extra = fields.slice(2);
  const rightExtraCount = Math.ceil(extra.length / 2);
  const leftExtraCount = Math.floor(extra.length / 2);
  const domainStart = leftExtraCount * AXIS_BAND;
  const domainEnd = 1 - rightExtraCount * AXIS_BAND;

  const layout = {
    xaxis: {
      title: { text: "Time (UTC)" },
      gridcolor: theme.gridcolor,
      ...(extra.length > 0 ? { domain: [domainStart, domainEnd] } : {}),
    },
  };

  fields.forEach((field, index) => {
    const color = AXIS_COLORS[index % AXIS_COLORS.length];
    const axisKey = index === 0 ? "yaxis" : `yaxis${index + 1}`;
    const base = {
      title: { text: FIELD_LABELS[field], font: { color } },
      tickfont: { color },
      linecolor: color,
      zeroline: false,
      showgrid: index === 0,
      gridcolor: theme.gridcolor,
    };
    if (index === 0) {
      layout[axisKey] = base;
    } else if (index === 1) {
      layout[axisKey] = { ...base, overlaying: "y", side: "right" };
    } else {
      const j = index - 2;
      const side = j % 2 === 0 ? "right" : "left";
      const stackPos = Math.floor(j / 2) + 1;
      const position = side === "right" ? domainEnd + stackPos * AXIS_BAND : domainStart - stackPos * AXIS_BAND;
      layout[axisKey] = { ...base, overlaying: "y", anchor: "free", side, position };
    }
  });

  return layout;
}

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

// The trailing 12 months, computed at load time rather than a fixed
// constant -- the OMNI2 dataset's most recent few weeks may show gaps for
// some fields (see historical.py's docs: the definitive, cross-calibrated
// magnetic-field columns lag ~4 weeks behind ground/model-derived ones like
// kp; this is NASA's own publication lag, not something this pipeline
// controls), which is expected, not a bug.
function defaultDateRange() {
  const end = new Date();
  const start = new Date(end);
  start.setUTCFullYear(start.getUTCFullYear() - 1);
  return { start: isoDate(start), end: isoDate(end) };
}

export default function HistoricalExplorer() {
  const [defaultRange] = useState(defaultDateRange);
  const [fields, setFields] = useState(DEFAULT_FIELDS);
  const [start, setStart] = useState(defaultRange.start);
  const [end, setEnd] = useState(defaultRange.end);
  const [state, setState] = useState({ status: "idle" });
  const [fieldLimitWarning, setFieldLimitWarning] = useState(false);
  const theme = usePlotTheme();

  function toggleField(field) {
    if (fields.includes(field)) {
      setFields((prev) => prev.filter((f) => f !== field));
      setFieldLimitWarning(false);
      return;
    }
    if (fields.length >= MAX_FIELDS) {
      setFieldLimitWarning(true);
      return;
    }
    setFieldLimitWarning(false);
    setFields((prev) => [...prev, field]);
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
      name: FIELD_LABELS[field],
      type: "scatter",
      mode: "lines",
      yaxis: index === 0 ? "y" : `y${index + 1}`,
      line: { color: AXIS_COLORS[index % AXIS_COLORS.length] },
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
    const axisLayout = buildAxisLayout(historical.fields, theme);
    const sortedEvents = [...events.events].sort((a, b) => a.start_time.localeCompare(b.start_time));
    return { traces, stormLines, eventCount: events.count, axisLayout, sortedEvents };
  }, [state, theme]);

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
          {fieldLimitWarning && (
            <p className="field-limit-warning">
              Maximum of {MAX_FIELDS} fields selected &mdash; remove one to add another.
            </p>
          )}
          <div className="field-groups">
            {FIELD_GROUPS.map((group) => (
              <div key={group.label} className="field-group">
                <div className="field-group-label">{group.label}</div>
                {group.fields.map((field) => (
                  <label key={field} className="checkbox">
                    <input type="checkbox" checked={fields.includes(field)} onChange={() => toggleField(field)} />
                    {FIELD_LABELS[field]}
                  </label>
                ))}
              </div>
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
              margin: { t: 20, r: 70, l: 70, b: 40 },
              font: theme.font,
              ...plot.axisLayout,
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
            {state.historical.count} {state.historical.resolution === "daily" ? "daily means" : "points"} &middot;{" "}
            {plot.eventCount} storm marker(s) in range
            {state.historical.resolution === "daily" &&
              " — range exceeds 90 days, showing daily means instead of hourly readings"}
          </p>
          {plot.eventCount > 0 && (
            <details className="events-list">
              <summary>
                Show {plot.eventCount} storm{plot.eventCount === 1 ? "" : "s"} in this range
              </summary>
              <table>
                <thead>
                  <tr>
                    <th>Onset (UTC)</th>
                    <th>Class</th>
                    <th>Max Kp</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {plot.sortedEvents.map((eventRecord) => (
                    <tr key={eventRecord.gst_id}>
                      <td>{new Date(eventRecord.start_time).toISOString().slice(0, 16).replace("T", " ")}</td>
                      <td>{eventRecord.storm_class}</td>
                      <td>{eventRecord.max_kp}</td>
                      <td>
                        {eventRecord.source_link ? (
                          <a href={eventRecord.source_link} target="_blank" rel="noreferrer noopener">
                            DONKI &#8599;
                          </a>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}
    </section>
  );
}
