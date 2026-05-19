import { NextResponse } from "next/server";

const BACKEND_BASE =
  process.env.CLINICAL_TRIAL_MATCHING_AGENT_BACKEND_URL
  || process.env.BACKEND_URL
  || "http://localhost:8000";

async function fetchWithReloadRetry(url, options) {
  try {
    return await fetch(url, options);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    if (!/fetch failed|ECONNREFUSED|ECONNRESET|socket hang up|other side closed/i.test(detail)) {
      throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 450));
    return fetch(url, options);
  }
}

export async function GET() {
  const startedAt = Date.now();

  try {
    const response = await fetchWithReloadRetry(
      `${BACKEND_BASE}/clinical-trials/health`,
      {
        method: "GET",
        cache: "no-store",
      }
    );

    const body = await response.json().catch(() => ({}));

    return NextResponse.json(
      {
        ok: response.ok,
        status: response.ok ? "ready" : "degraded",
        backendBaseUrl: BACKEND_BASE,
        durationMs: Date.now() - startedAt,
        backend: body,
      },
      { status: response.ok ? 200 : 503 }
    );
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        status: "unavailable",
        backendBaseUrl: BACKEND_BASE,
        durationMs: Date.now() - startedAt,
        details: error instanceof Error ? error.message : String(error),
      },
      { status: 503 }
    );
  }
}
