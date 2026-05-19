export async function GET(request, { params }) {
  const { nctId } = await params;
  if (!nctId || !/^NCT\d+$/i.test(nctId)) {
    return Response.json({ error: "Invalid NCT ID" }, { status: 400 });
  }

  try {
    const fields = "NCTId,LocationFacility,LocationCity,LocationState,LocationCountry,LocationGeoPoint";
    const url = `https://clinicaltrials.gov/api/v2/studies/${nctId}?fields=${fields}`;
    const res = await fetch(url, {
      headers: { Accept: "application/json" },
      next: { revalidate: 3600 },
    });

    if (!res.ok) return Response.json({ locations: [] });

    const data = await res.json();
    const locations = extractLocations(data);
    return Response.json({ nct_id: nctId, locations });
  } catch {
    return Response.json({ locations: [] });
  }
}

function extractLocations(data) {
  const module =
    data?.protocolSection?.contactsLocationsModule ||
    data?.contactsLocationsModule ||
    {};

  const rawLocations = module?.locations || [];

  return rawLocations
    .map((loc) => {
      const lat = loc?.geoPoint?.lat ?? null;
      const lng = loc?.geoPoint?.lon ?? null;
      return {
        facility: loc?.facility || null,
        city: loc?.city || null,
        state: loc?.state || null,
        country: loc?.country || null,
        lat,
        lng,
      };
    })
    .filter((loc) => loc.lat !== null && loc.lng !== null)
    .slice(0, 50);
}
