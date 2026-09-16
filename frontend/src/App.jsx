import { lazy, Suspense, useState } from "react";
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
  const ActiveComponent = TABS.find((tab) => tab.id === activeTab).Component;

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
            onClick={() => setActiveTab(tab.id)}
            aria-current={tab.id === activeTab}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <main>
        <Suspense fallback={<StatusMessage kind="loading">Loading&hellip;</StatusMessage>}>
          <ActiveComponent />
        </Suspense>
      </main>
    </div>
  );
}
