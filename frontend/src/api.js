const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(path) {
  if (!API_BASE_URL) {
    throw new ApiError(0, "VITE_API_BASE_URL is not set -- see frontend/.env.example");
  }
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`);
  } catch {
    throw new ApiError(0, "could not reach the API -- check your connection and try again");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, body.error || `request failed with status ${response.status}`);
  }
  return body;
}

export function fetchHistorical({ fields, start, end }) {
  const params = new URLSearchParams({ fields: fields.join(","), start, end });
  return request(`/historical?${params}`);
}

export function fetchEvents({ start, end } = {}) {
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  const query = params.toString();
  return request(`/events${query ? `?${query}` : ""}`);
}

export function fetchSeaResult(field, normalization) {
  return request(`/sea/${field}/${normalization}`);
}

export function fetchForecastSkill(target, errorType) {
  return request(`/forecast-skill/${target}/${errorType}`);
}
