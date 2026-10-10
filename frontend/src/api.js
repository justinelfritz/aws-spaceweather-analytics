const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = "GET", body } = {}) {
  if (!API_BASE_URL) {
    throw new ApiError(0, "VITE_API_BASE_URL is not set -- see frontend/.env.example");
  }
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      ...(body !== undefined ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
  } catch {
    throw new ApiError(0, "could not reach the API -- check your connection and try again");
  }
  const responseBody = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, responseBody.error || `request failed with status ${response.status}`);
  }
  return responseBody;
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

// eventIds omitted/null means "the full DONKI catalog" -- a plain GET,
// identical to the precomputed batch result. A non-null array (see
// docs/sea-on-demand-design.md) POSTs it as an on-demand, caller-chosen
// subset instead; the backend takes the same fast path as the GET if that
// array happens to name every known event.
export function fetchSeaResult(field, normalization, eventIds = null) {
  if (eventIds === null) {
    return request(`/sea/${field}/${normalization}`);
  }
  return request(`/sea/${field}/${normalization}`, { method: "POST", body: { event_ids: eventIds } });
}

export function fetchForecastSkill(target, errorType) {
  return request(`/forecast-skill/${target}/${errorType}`);
}
