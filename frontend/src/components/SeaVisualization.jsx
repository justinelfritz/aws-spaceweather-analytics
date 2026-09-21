import { useEffect, useMemo, useState } from "react";
import Plot from "react-plotly.js";
import { ApiError, fetchEvents, fetchSeaResult } from "../api.js";
import { FIELD_LABELS, FIELDS_IN_DISPLAY_ORDER, NORMALIZATION_LABELS, NORMALIZATIONS } from "../constants.js";
import { usePlotTheme } from "../theme.js";
import StatusMessage from "./StatusMessage.jsx";

// A result computed from fewer than this many storms gets a "small sample"
// caveat under the chart -- a mean/percentile band from a handful of events
// is still a valid result (aggregation.py degenerates gracefully down to
// n=1), just not one that's statistically meaningful on its own. Purely a
// frontend convention (docs/sea-on-demand-design.md Decision 4) -- the API
// itself enforces no minimum beyond "not empty."
const SMALL_SAMPLE_THRESHOLD = 10;

const STORM_CLASSES = ["G1", "G2", "G3", "G4", "G5"];

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

  const [catalog, setCatalog] = useState({ status: "loading" });
  const [filters, setFilters] = useState({ start: "", end: "", minClass: "", minKp: "" });
  const [selectedIds, setSelectedIds] = useState(null); // null until the catalog loads, then a Set of every gst_id

  useEffect(() => {
    let cancelled = false;
    fetchEvents()
      .then((data) => {
        if (cancelled) return;
        const sorted = [...data.events].sort((a, b) => a.start_time.localeCompare(b.start_time));
        setCatalog({ status: "ready", events: sorted });
      })
      .catch((err) => {
        if (cancelled) return;
        setCatalog({ status: "error", message: err instanceof ApiError ? err.message : "failed to load storm catalog" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const visibleEvents = useMemo(() => {
    if (catalog.status !== "ready") return [];
    return catalog.events.filter((e) => {
      if (filters.start && e.start_time.slice(0, 10) < filters.start) return false;
      if (filters.end && e.start_time.slice(0, 10) > filters.end) return false;
      if (filters.minClass && e.storm_class < filters.minClass) return false;
      if (filters.minKp && !(e.max_kp >= Number(filters.minKp))) return false;
      return true;
    });
  }, [catalog, filters]);

  // Filters set the candidate set for analysis, not just what's shown in the
  // table below -- so changing a filter re-selects exactly the storms it now
  // matches (this also covers the initial load, since an all-empty `filters`
  // matches every storm). Individual checkbox toggles between filter changes
  // are left alone; they're the fine-tuning step on top of that baseline.
  useEffect(() => {
    if (catalog.status !== "ready") return;
    setSelectedIds(new Set(visibleEvents.map((e) => e.gst_id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog.status, filters]);

  function toggleEvent(gstId) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(gstId)) next.delete(gstId);
      else next.add(gstId);
      return next;
    });
  }

  const allVisibleSelected = visibleEvents.length > 0 && visibleEvents.every((e) => selectedIds?.has(e.gst_id));

  function toggleAllVisible() {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        visibleEvents.forEach((e) => next.delete(e.gst_id));
      } else {
        visibleEvents.forEach((e) => next.add(e.gst_id));
      }
      return next;
    });
  }

  async function runQuery(event) {
    event.preventDefault();
    if (!selectedIds || selectedIds.size === 0) {
      setState({ status: "error", message: "select at least one storm" });
      return;
    }
    setState({ status: "loading" });
    try {
      // Selecting every known storm is the default, full-catalog case --
      // send null so the request takes the same instant precomputed-result
      // path a plain GET would, instead of needlessly recomputing it.
      const eventIds = selectedIds.size === catalog.events.length ? null : Array.from(selectedIds);
      const result = await fetchSeaResult(field, normalization, eventIds);
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
        Storms aligned on onset time (offset = 0 on the x-axis), showing how a field typically evolves
        before and after a geomagnetic storm, averaged across whichever storms are selected below
        (every cataloged storm, by default).
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
          <button type="submit" disabled={catalog.status !== "ready"}>
            Load
          </button>
        </div>

        <fieldset>
          <legend>Storms to include</legend>
          {catalog.status === "loading" && <StatusMessage kind="loading">Loading storm catalog&hellip;</StatusMessage>}
          {catalog.status === "error" && <StatusMessage kind="error">{catalog.message}</StatusMessage>}
          {catalog.status === "ready" && (
            <>
              <div className="control-row">
                <label>
                  Onset after
                  <input
                    type="date"
                    value={filters.start}
                    onChange={(e) => setFilters((prev) => ({ ...prev, start: e.target.value }))}
                  />
                </label>
                <label>
                  Onset before
                  <input
                    type="date"
                    value={filters.end}
                    onChange={(e) => setFilters((prev) => ({ ...prev, end: e.target.value }))}
                  />
                </label>
                <label>
                  Min storm class
                  <select
                    value={filters.minClass}
                    onChange={(e) => setFilters((prev) => ({ ...prev, minClass: e.target.value }))}
                  >
                    <option value="">Any</option>
                    {STORM_CLASSES.map((c) => (
                      <option key={c} value={c}>
                        {c}+
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Min Kp
                  <input
                    type="number"
                    min="0"
                    max="9"
                    step="0.33"
                    value={filters.minKp}
                    onChange={(e) => setFilters((prev) => ({ ...prev, minKp: e.target.value }))}
                  />
                </label>
              </div>

              <details className="events-list" open={selectedIds && selectedIds.size < catalog.events.length}>
                <summary>
                  {selectedIds?.size ?? 0} of {catalog.events.length} storms available ({visibleEvents.length} selected by applied filters)
                </summary>
                <table>
                  <thead>
                    <tr>
                      <th>
                        <input type="checkbox" checked={allVisibleSelected} onChange={toggleAllVisible} />
                      </th>
                      <th>Onset (UTC)</th>
                      <th>Class</th>
                      <th>Max Kp</th>
                      <th>Min Dst [nT]</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleEvents.map((eventRecord) => (
                      <tr key={eventRecord.gst_id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selectedIds?.has(eventRecord.gst_id) ?? false}
                            onChange={() => toggleEvent(eventRecord.gst_id)}
                          />
                        </td>
                        <td>{new Date(eventRecord.start_time).toISOString().slice(0, 16).replace("T", " ")}</td>
                        <td>{eventRecord.storm_class}</td>
                        <td>{eventRecord.max_kp}</td>
                        <td>{eventRecord.min_dst ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </>
          )}
        </fieldset>
      </form>

      {state.status === "loading" && <StatusMessage kind="loading">Loading&hellip;</StatusMessage>}
      {state.status === "error" && <StatusMessage kind="error">{state.message}</StatusMessage>}
      {state.status === "empty" && (
        <StatusMessage kind="empty">
          No SEA result has been computed yet for <code>{field}</code>/<code>{normalization}</code> with
          this exact storm selection.
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
          {state.result.event_count < SMALL_SAMPLE_THRESHOLD && (
            <p className="sample-caveat">
              Small sample ({state.result.event_count} storm{state.result.event_count === 1 ? "" : "s"}) &mdash;
              interpret with caution.
            </p>
          )}
        </>
      )}
    </section>
  );
}
