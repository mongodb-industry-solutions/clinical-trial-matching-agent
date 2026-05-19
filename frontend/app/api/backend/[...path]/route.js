import { NextResponse } from "next/server";

const BACKEND_BASE =
  process.env.CLINICAL_TRIAL_MATCHING_AGENT_BACKEND_URL
  || process.env.BACKEND_URL
  || "http://localhost:8000";

function buildBackendUrl(pathSegments, requestUrl) {
  const upstream = new URL(BACKEND_BASE);
  const request = new URL(requestUrl);
  upstream.pathname = `/${(pathSegments || []).join("/")}`;
  upstream.search = request.search;
  return upstream.toString();
}

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

async function resolvePathSegments(params) {
  const resolvedParams = await params;
  return Array.isArray(resolvedParams?.path) ? resolvedParams.path : [];
}

async function proxy(request, { params }) {
  const pathSegments = await resolvePathSegments(params);
  const upstreamUrl = buildBackendUrl(pathSegments, request.url);
  const requestBody =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.text();

  try {
    const response = await fetchWithReloadRetry(upstreamUrl, {
      method: request.method,
      headers: {
        Accept: request.headers.get("accept") || "application/json",
        "Content-Type": request.headers.get("content-type") || "application/json",
      },
      body: requestBody,
      cache: "no-store",
    });

    const contentType = response.headers.get("content-type") || "application/json";
    const body = await response.text();

    return new NextResponse(body, {
      status: response.status,
      headers: {
        "content-type": contentType,
      },
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: "Backend proxy request failed",
        backendBaseUrl: BACKEND_BASE,
        details: error instanceof Error ? error.message : String(error),
      },
      { status: 502 }
    );
  }
}

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
