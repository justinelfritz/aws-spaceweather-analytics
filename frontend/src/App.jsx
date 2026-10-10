import { lazy, Suspense, useEffect, useState } from "react";
import "./App.css";
import StatusMessage from "./components/StatusMessage.jsx";

// Lazy-loaded per tab so the (Plotly-heavy) chart code for one view isn't
// pulled into the initial bundle for the other two.
const HistoricalExplorer = lazy(() => import("./components/HistoricalExplorer.jsx"));
const SeaVisualization = lazy(() => import("./components/SeaVisualization.jsx"));
const ForecastSkillView = lazy(() => import("./components/ForecastSkillView.jsx"));

const TABS = [
  { id: "historical", label: "Historical explorer", Component: HistoricalExplorer },
  { id: "sea", label: "Superposed epoch analysis", Component: SeaVisualization },
  { id: "forecast", label: "Forecast skill", Component: ForecastSkillView },
];

export default function App() {
  const [activeTab, setActiveTab] = useState(TABS[0].id);
  // Every tab ever visited stays mounted (see the render below) so
  // switching away and back preserves its state instead of losing it to
  // unmount/remount -- only the *first* visit needs to wait on the lazy
  // import.
  const [visitedTabs, setVisitedTabs] = useState(() => new Set([TABS[0].id]));

  function activateTab(id) {
    setVisitedTabs((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
    setActiveTab(id);
  }

  useEffect(() => {
    // A tab hidden via display:none reports zero size to Plotly, and
    // nothing tells it to remeasure once it's shown again -- each chart
    // already listens for window resize (react-plotly.js's
    // useResizeHandler), so nudging that on every tab switch is enough,
    // with no changes needed in the view components themselves. Deferred
    // to the next frame so the display:none -> block change has already
    // taken effect before Plotly remeasures.
    const raf = requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
    return () => cancelAnimationFrame(raf);
  }, [activeTab]);

  return (
    <div className="app">
      <header>
        <h1>Space Weather Analytics</h1>
        <p className="tagline">
          Superposed epoch analysis and forecast-skill diagnostics over decades of geomagnetic storm
          data, served from a curated AWS data lake.
        </p>
      </header>
      <nav className="tabs" aria-label="views">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={tab.id === activeTab ? "tab active" : "tab"}
            onClick={() => activateTab(tab.id)}
            aria-current={tab.id === activeTab}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <main>
        {TABS.map(
          (tab) =>
            visitedTabs.has(tab.id) && (
              <div key={tab.id} hidden={tab.id !== activeTab}>
                <Suspense fallback={<StatusMessage kind="loading">Loading&hellip;</StatusMessage>}>
                  <tab.Component />
                </Suspense>
              </div>
            ),
        )}
      </main>
    </div>
  );
}
