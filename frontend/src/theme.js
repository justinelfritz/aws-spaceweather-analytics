import { useEffect, useState } from "react";

const DARK = { font: { color: "#c9cbd3" }, gridcolor: "#2e303a" };
const LIGHT = { font: { color: "#3a3640" }, gridcolor: "#e5e4e7" };

// Plotly can't read CSS custom properties, so its layout colors are kept
// in sync with prefers-color-scheme by hand here.
export function usePlotTheme() {
  const query = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const [dark, setDark] = useState(() => query?.matches ?? false);

  useEffect(() => {
    if (!query) return;
    const handler = (event) => setDark(event.matches);
    query.addEventListener("change", handler);
    return () => query.removeEventListener("change", handler);
  }, [query]);

  return dark ? DARK : LIGHT;
}
