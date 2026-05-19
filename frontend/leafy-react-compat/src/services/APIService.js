import { logActivity } from "./LoggerService";
import axios from "axios";
import { API_ROUTES } from "./APIConfig"

const dicomImageUrl = process.env.REACT_APP_DICOM_IMAGE_ENDPOINT;

const buildErrorLogData = (error) => ({
  message: error?.message || "Unknown error",
  status: error?.response?.status || null,
  data: error?.response?.data || null,
});

const startApiTransaction = (title, requestMessage, requestData) => {
  const transactionID = logActivity.start(title);
  if (requestMessage) {
    logActivity.addEntry(transactionID, "code-request", requestMessage, requestData);
  }
  return transactionID;
};

const finishApiTransactionSuccess = (transactionID, responseMessage, responseData) => {
  logActivity.addEntry(transactionID, "code-response", responseMessage, responseData);
  logActivity.end(transactionID);
};

const finishApiTransactionError = (transactionID, responseMessage, error) => {
  if (!transactionID) {
    return;
  }
  logActivity.addEntry(transactionID, "code-response", responseMessage, buildErrorLogData(error));
  logActivity.end(transactionID);
};

export const isApiRequestCanceled = (error) =>
  axios.isCancel(error) ||
  error?.code === "ERR_CANCELED" ||
  error?.name === "CanceledError";

const sanitizeRequestHeaders = (headers = {}) => {
  const normalizedHeaders = { ...headers };
  if (normalizedHeaders.Authorization) {
    normalizedHeaders.Authorization = "[redacted]";
  }
  if (normalizedHeaders.authorization) {
    normalizedHeaders.authorization = "[redacted]";
  }
  return normalizedHeaders;
};

const sanitizePayloadForInspect = (value, depth = 0) => {
  const maxDepth = 4;
  const maxStringLength = 4000;
  const maxArrayItems = 25;
  const maxObjectKeys = 40;

  if (depth > maxDepth) {
    return "[truncated: max depth reached]";
  }
  if (value === null || value === undefined) {
    return value;
  }
  if (typeof value === "string") {
    if (value.length <= maxStringLength) {
      return value;
    }
    return `${value.slice(0, maxStringLength)}...[truncated ${value.length - maxStringLength} chars]`;
  }
  if (Array.isArray(value)) {
    const sanitizedItems = value
      .slice(0, maxArrayItems)
      .map((item) => sanitizePayloadForInspect(item, depth + 1));
    if (value.length > maxArrayItems) {
      sanitizedItems.push(`[truncated: ${value.length - maxArrayItems} more items]`);
    }
    return sanitizedItems;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value);
    const sanitizedObject = {};
    keys.slice(0, maxObjectKeys).forEach((key) => {
      sanitizedObject[key] = sanitizePayloadForInspect(value[key], depth + 1);
    });
    if (keys.length > maxObjectKeys) {
      sanitizedObject.__truncatedKeys = keys.length - maxObjectKeys;
    }
    return sanitizedObject;
  }
  return value;
};

let axiosInspectInterceptorsInstalled = false;
const installAxiosInspectInterceptors = () => {
  if (axiosInspectInterceptorsInstalled) {
    return;
  }
  axiosInspectInterceptorsInstalled = true;

  axios.interceptors.request.use(
    (config) => {
      if (config?.inspectAutoLogging === false) {
        return config;
      }

      const method = (config?.method || "GET").toUpperCase();
      const requestUrl = config?.url || "";
      const transactionID = logActivity.start(`HTTP ${method} ${requestUrl}`);
      config.__inspectTransactionID = transactionID;

      logActivity.addEntry(transactionID, "code-request", "HTTP request", {
        method,
        url: requestUrl,
        params: sanitizePayloadForInspect(config?.params ?? null),
        data: sanitizePayloadForInspect(config?.data ?? null),
        headers: sanitizeRequestHeaders(config?.headers || {}),
      });
      return config;
    },
    (error) => Promise.reject(error)
  );

  axios.interceptors.response.use(
    (response) => {
      const transactionID = response?.config?.__inspectTransactionID;
      if (transactionID) {
        logActivity.addEntry(transactionID, "code-response", "HTTP response", {
          status: response?.status,
          statusText: response?.statusText,
          data: sanitizePayloadForInspect(response?.data ?? null),
        });
        logActivity.end(transactionID);
      }
      return response;
    },
    (error) => {
      const transactionID = error?.config?.__inspectTransactionID;
      if (transactionID) {
        finishApiTransactionError(transactionID, "HTTP error response", error);
      }
      return Promise.reject(error);
    }
  );
};

installAxiosInspectInterceptors();

const resolveDicomImageEndpoint = () => {
  const configuredEndpoint = (dicomImageUrl || "").trim();
  const fallbackEndpoint = (API_ROUTES.dicomImage || "/newImage").trim();
  const endpoint = configuredEndpoint || fallbackEndpoint;

  if (/^https?:\/\//i.test(endpoint)) {
    return endpoint;
  }
  if (endpoint.startsWith("/api/")) {
    return endpoint;
  }
  if (endpoint.startsWith("/")) {
    return `/api${endpoint}`;
  }
  return `/api/${endpoint}`;
};

const calculateDateOfBirth = (age) => {
  const today = new Date();
  const birthDate = new Date(
    today.getFullYear() - age,
    today.getMonth(),
    today.getDate()
  );
  birthDate.setHours(0, 0, 0, 0);
  return birthDate;
};

const GetFormattedMongoDBMatchFilter = (facets) => {
  const filter = {};
  console.log("GetFormattedMongoDBMatchFilter Facets", facets)
  const locationFilters = facets?.find((filter) => filter.category === "center");
  const statusFilters = facets?.find((filter) => filter.category === "resource.status");
  const periodFilters = facets?.filter((filter) => filter.category === "period");
  const ageFilters = facets?.filter((filter) => filter.category === "age");

  if (locationFilters) {
    // Gio's modification
    // filter["location.display"] = { $in: locationFilters.value };
    filter.center = locationFilters.value
  }
  
  if (statusFilters) {
    // Gio's modification
    // filter.status = { $in: statusFilters.value };
    filter.status = statusFilters.value
  }

  if (ageFilters && ageFilters.length > 0) {
    const ageConditions = ageFilters.flatMap((ageFilter) => {
      return ageFilter.value.map((ageFilterValue) => {
        switch (ageFilterValue) {
          case "40-49":
            return {
              "metadata.extraSearchParams.subject.birthDate": {
                $gte: new Date(calculateDateOfBirth(50)),
              },
            };
          case "50-59":
            return {
              "metadata.extraSearchParams.subject.birthDate": {
                $gte: new Date(calculateDateOfBirth(60)),
                $lt: new Date(calculateDateOfBirth(50)),
              },
            };
          case "60-69":
            return {
              "metadata.extraSearchParams.subject.birthDate": {
                $gte: new Date(calculateDateOfBirth(70)),
                $lt: new Date(calculateDateOfBirth(60)),
              },
            };
          case "70-79":
            return {
              "metadata.extraSearchParams.subject.birthDate": {
                $lte: new Date(calculateDateOfBirth(70)),
              },
            };
          default:
            console.error("Unsupported age filter", ageFilterValue);
            return null;
        }
      });
    });

    const validAgeConditions = ageConditions.filter(
      (condition) => condition !== null
    );
    
    // Gio's modification
    if (validAgeConditions.length > 0) {
      filter.$or = validAgeConditions;
      filter.ageFilter = validAgeConditions;
    }
  }

  if (periodFilters?.length > 0) {
    const today = new Date();
    const startOfDay = new Date(today);
    startOfDay.setHours(0, 0, 0, 0);
    const endOfDay = new Date(today);
    endOfDay.setHours(23, 59, 59, 999);

    const selectedOptions = periodFilters.flatMap(
      (periodFilter) => periodFilter.value
    );

    filter.start = selectedOptions

  //   if (selectedOptions.includes("month")) {
  //     const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
  //     const monthEnd = new Date(today.getFullYear(), today.getMonth() + 1, 0);
  //     monthEnd.setHours(23, 59, 59, 999);
  //     filter.start = { $gte: monthStart, $lte: monthEnd };
  //   } else if (selectedOptions.includes("week")) {
  //     const weekStart = new Date(today);
  //     weekStart.setDate(today.getDate() - today.getDay() + 1);
  //     weekStart.setHours(0, 0, 0, 0);
  //     const weekEnd = new Date(today);
  //     weekEnd.setDate(today.getDate() - today.getDay() + 7);
  //     weekEnd.setHours(23, 59, 59, 999);
  //     filter.start = { $gte: weekStart, $lte: weekEnd };
  //   } else if (selectedOptions.includes("today")) {
  //     filter.start = { $gte: startOfDay, $lte: endOfDay };
  //   } else {
  //     console.error("Unsupported period filter");
  //   }
  }
  
  if (filter.start === undefined) {
    filter.start = null
  }

  if (filter.status === undefined) {
    filter.status = null
  }

  if (filter.ageFilter === undefined) {
    filter.ageFilter = null
  }

  if (filter.center === undefined) {
    filter.center = null
  }

  console.log("GetFormattedMongoDBMatchFilter Filter", filter)
  
  return filter;
};

export async function getLocationId(preferences, currentAppUser) {
  try {
    const persona = await getDemoPersona(currentAppUser);
    const personaLocationName = (persona?.locationName || "").trim();
    if (personaLocationName) {
      return personaLocationName;
    }

    const response = await axios.get(`/api${API_ROUTES.locations}`, {
      params: {
        currentUser: currentAppUser,
        limit: 1,
      },
      paramsSerializer: (params) => new URLSearchParams(params).toString(),
    });
    const locationDocument = response?.data?.locations?.data || [];
    if (locationDocument && locationDocument.length > 0) {
      return locationDocument[0].name;
    } else {
      return null;
    }
  } catch (err) {
    console.log(
      "Error occurred while fetching location document:",
      err.message
    );
    return null;
  }
}

export async function getDemoPersona(currentAppUser) {
  try {
    const response = await axios.get(`/api${API_ROUTES.demoPersona}`, {
      params: {
        currentUser: currentAppUser,
      },
      paramsSerializer: (params) => new URLSearchParams(params).toString(),
    });
    return response?.data?.persona || null;
  } catch (err) {
    console.log(
      "Error occurred while fetching demo persona:",
      err.message
    );
    return null;
  }
}

export async function APILoadLocations(preferences, currentAppUser) {
  try {
    const response = await axios.get(`/api${API_ROUTES.locations}`, {
      params: {
        currentUser: currentAppUser,
      },
      paramsSerializer: (params) => new URLSearchParams(params).toString(),
    });
    const locationDocuments = response?.data?.locations?.data || [];
    return Array.isArray(locationDocuments) ? locationDocuments : [];
  } catch (err) {
    console.log(
      "Error occurred while fetching locations for map:",
      err.message
    );
    return [];
  }
}

const getFormattedFHIRQueryParams = (
  facets,
  currentAppUser,
  currentCenter = "",
  options = {}
) => {
  const disableProviderDefaultLocation = options?.disableProviderDefaultLocation === true;
  const queryParams = [];
  console.log("getFormattedFHIRQueryParams", facets);
  console.log("getFormattedFHIRQueryParams currentAppUser", currentAppUser);

  const locationFilters = facets?.find(
    (filter) => filter.category === "center"
  );
  if (facets && Array.isArray(facets) && facets.length > 0) {
    const statusFilters = facets.find((filter) => filter.category === "resource.status");
    const periodFilters = facets.filter(
      (filter) => filter.category === "period"
    );
    const ageFilters = facets.filter((filter) => filter.category === "age");
    
    if (locationFilters && Array.isArray(locationFilters.value) && locationFilters.value.length > 0) {
      const encodedString = locationFilters.value
        .map((value) => encodeURIComponent(value))
        .join(",");
      const locationFilterParam = `location=${encodedString}`;
      queryParams.push(locationFilterParam);
    }

    if (ageFilters && ageFilters.length > 0) {
      const ageConditions = ageFilters.flatMap((ageFilter) => {
        return ageFilter.value.map((ageFilterValue) => {
          switch (ageFilterValue) {
            case "40-49":
              return `birthdate=ge${calculateDateOfBirth(50).toISOString().split("T")[0]
                }`;
            case "50-59":
              return `birthdate=ge${calculateDateOfBirth(60).toISOString().split("T")[0]
                },lt${calculateDateOfBirth(50).toISOString().split("T")[0]}`;
            case "60-69":
              return `birthdate=ge${calculateDateOfBirth(70).toISOString().split("T")[0]
                },lt${calculateDateOfBirth(60).toISOString().split("T")[0]}`;
            case "70":
            case "70-79":
              return `birthdate=le${calculateDateOfBirth(70).toISOString().split("T")[0]
                }`;
            default:
              console.error("Unsupported age filter", ageFilterValue);
              return null;
          } // filtering by age we can't select several filter for age, they will get overwritten in the API
        });
      });

      const validAgeConditions = ageConditions.filter(
        (condition) => condition !== null
      );
      if (validAgeConditions.length > 0) {
        queryParams.push(...validAgeConditions);
      }
    }

    if (statusFilters && statusFilters.value) {
      const statusParam = `status=${statusFilters.value.join(",")}`;
      queryParams.push(statusParam);
    }

    if (periodFilters && periodFilters.length > 0) {
      periodFilters.forEach((periodFilter) => {
        let periodParam = "";

        switch (periodFilter.value[0]) {
          case "today":
            const today = new Date().toISOString().split("T")[0];
            const tomorrow = new Date();
            tomorrow.setDate(tomorrow.getDate() + 1);
            periodParam = `date=ge${today},le${tomorrow.toISOString().split("T")[0]
              }`;
            break;
          case "week":
            const weekStart = new Date();
            weekStart.setDate(weekStart.getDate() - weekStart.getDay() + 1);
            const weekEnd = new Date();
            weekEnd.setDate(weekEnd.getDate() - weekEnd.getDay() + 8);
            periodParam = `date=ge${weekStart.toISOString().split("T")[0]},le${weekEnd.toISOString().split("T")[0]
              }`;
            break;
          case "month":
            const monthStart = new Date();
            monthStart.setDate(1);
            const monthEnd = new Date(
              monthStart.getFullYear(),
              monthStart.getMonth() + 1,
              2
            );
            periodParam = `date=ge${monthStart.toISOString().split("T")[0]},le${monthEnd.toISOString().split("T")[0]
              }`;
            break;
          default:
            console.error("Unsupported period filter");
        }

        queryParams.push(periodParam);
      });
    }
  }

  // Provider default location should follow the active center to keep MQL/FHIR behavior aligned.
  // Apply only when no location facet is explicitly selected.
  if (
    currentAppUser === "hc-provider" &&
    !disableProviderDefaultLocation &&
    (!locationFilters || !Array.isArray(locationFilters.value) || locationFilters.value.length === 0)
  ) {
    const fallbackCenter =
      (currentCenter || "").trim() || "Mayo Clinic Hospital - Phoenix";
    queryParams.push(`location=${encodeURIComponent(fallbackCenter)}`);
  }

  return queryParams.join("&");
};

const postProcessFHIRResult = (fhirResult) => {
  const entries = Array.isArray(fhirResult?.entry) ? fhirResult.entry : [];
  return entries.map((entry) => {
    // const resource = entry.resource.resource;

    // let created = resource.created;
    // console.log("postProcessFHIRResult created", created)
    // if (typeof created === "object" && created.$date) {
    //   created = new Date(created.$date.$numberLong);
    // }

    // let start = resource.start;
    // console.log("postProcessFHIRResult start", start)
    // if (typeof start === "object" && start.$date) {
    //   start = new Date(start.$date.$numberLong);
    // }

    // const subject = resource.subject || {};
    // console.log("postProcessFHIRResult subject", subject)

    // const participant1 = resource.participant[0] || {};
    // console.log("postProcessFHIRResult participant1", participant1)

    return entry?.resource

    // const actor1Display = participant1.actor
    //   ? Array.isArray(participant1.actor.display)
    //     ? {
    //       given: participant1.actor.display[0]?.given || [],
    //       family: participant1.actor.display[0]?.family || "",
    //     }
    //     : {
    //       given: [participant1.actor.display?.given || ""],
    //       family: participant1.actor.display?.family || "",
    //     }
    //   : { given: [], family: "" };

    // return {
    //   _id: resource._id.$oid,
    //   resourceType: resource.resourceType,
    //   status: resource.status,
    //   created: created,
    //   start: start,
    //   description: resource.description,
    //   serviceType: resource.serviceType,
    //   subject: subject,
    //   participant: [
    //     {
    //       actor: {
    //         reference: participant1.actor
    //           ? participant1.actor.reference.$oid
    //           : "",
    //         display: [
    //           {
    //             given: actor1Display.given,
    //             family: actor1Display.family,
    //           },
    //         ],
    //         birthDate:
    //           participant1.actor && participant1.actor.birthDate
    //             ? new Date(participant1.actor.birthDate).toISOString()
    //             : null,
    //       },
    //       required: participant1.required,
    //       status: participant1.status,
    //     },
    //   ],
    //   location: {
    //     reference: resource.location.reference.$oid,
    //     display: resource.location.display,
    //   },
    // };
  }).filter(Boolean);
};

export const APILoadInitialAppointments = async (
  selectedFilters,
  sortField,
  sortOrder,
  preferences,
  currentAppUser,
  currentCenter,
  requestOptions = {}
) => {
  let transactionID;
  try {
    const limit = Number(preferences?.screening?.loadInitialAppointments || 24);
    const params = {
      q: selectedFilters?.searchQuery || "",
      currentCenter: currentCenter || "",
      currentUser: currentAppUser || "",
      limit: Number.isFinite(limit) ? limit : 24,
      sortField: sortField || "start",
      sortOrder: sortOrder || "desc",
    };

    transactionID = startApiTransaction(
      "Load Appointments",
      "GET /api/appointments",
      params
    );

    const responseAppointments = await axios.get("/api/appointments", {
      signal: requestOptions?.signal,
      params,
      paramsSerializer: (queryParams) =>
        new URLSearchParams(queryParams).toString(),
    });

    const initialAppointments = responseAppointments?.data?.appointments?.data || [];
    const responseMetadata = responseAppointments?.data?.appointments?.metadata || {};
    const appointmentsMetadata = {
      ...responseMetadata,
      totalCount: Number.isFinite(responseMetadata?.totalCount)
        ? responseMetadata.totalCount
        : initialAppointments.length,
    };

    finishApiTransactionSuccess(
      transactionID,
      "Appointments response",
      responseAppointments?.data || {}
    );

    return { initialAppointments, appointmentsMetadata };
  } catch (error) {
    if (isApiRequestCanceled(error)) {
      throw error;
    }
    finishApiTransactionError(transactionID, "Error loading initial appointments", error);
    throw error;
  }
};

export const APILoadAppointmentsInsights = async (
  selectedFilters,
  preferences,
  currentAppUser,
  currentCenter
) => {
  let transactionID;
  try {
    let apptTimeFilter = null;
    let apptStatusFilter = null;
    let apptCenterFilter = null;
    let apptAgeFilter = null;
    let searchStageFilter = null;

    if (selectedFilters?.searchQuery) {
      const searchStage = JSON.parse(preferences.screening.atlasSearchStage);
      updateQueryParams(searchStage, selectedFilters.searchQuery);
      searchStageFilter = searchStage;
    }

    if (selectedFilters?.facets && selectedFilters.facets.length > 0) {
      const matchFilter = GetFormattedMongoDBMatchFilter(selectedFilters.facets);
      apptTimeFilter = matchFilter.start;
      apptStatusFilter = matchFilter.status;
      apptAgeFilter = matchFilter.ageFilter;
      apptCenterFilter = matchFilter.center;
    }

    const requestParams = {
      apptTime: apptTimeFilter,
      statusFilter: apptStatusFilter,
      centerFilter: apptCenterFilter,
      currentCenter: currentCenter,
      ageFilter: JSON.stringify(apptAgeFilter),
      searchStage: JSON.stringify(searchStageFilter),
      currentUser: currentAppUser,
      includePipelines: "true",
    };

    transactionID = startApiTransaction(
      "Loading appointments insights (attendance + geo)"
    );

    const response = await axios.get(`/api/appointments_insights`, {
      params: {
        ...requestParams
      },
      paramsSerializer: (params) => {
        return new URLSearchParams(params).toString();
      },
    });

    if (response?.data?.pipelineDebug) {
      logActivity.addEntry(
        transactionID,
        "code-request",
        "MQL aggregation pipelines",
        {
          countPipeline: response.data.pipelineDebug.countPipeline,
          statusPipeline: response.data.pipelineDebug.statusPipeline,
          weekdayPipeline: response.data.pipelineDebug.weekdayPipeline,
          weekPipeline: response.data.pipelineDebug.weekPipeline,
          yearPipeline: response.data.pipelineDebug.yearPipeline,
          locationPressurePipeline: response.data.pipelineDebug.locationPressurePipeline,
        }
      );
    } else {
      logActivity.addEntry(
        transactionID,
        "code-request",
        "GET /api/appointments_insights params",
        requestParams
      );
    }

    finishApiTransactionSuccess(
      transactionID,
      "appointments_insights response",
      response?.data?.insights || response?.data || {}
    );

    if (response?.data?.success && response.data.insights) {
      return response.data.insights;
    }

    return {
      totalCount: 0,
      statusCounts: {},
      attendedByWeekday: [],
      attendedByWeek: [],
      attendedByYear: [],
      locationPressure: [],
    };
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading appointments insights", error);
    console.error("Error loading appointments insights:", error);
    return {
      totalCount: 0,
      statusCounts: {},
      attendedByWeekday: [],
      attendedByWeek: [],
      attendedByYear: [],
      locationPressure: [],
    };
  }
};

export const APILoadMoreAppointments = async (
  appointments,
  selectedFilters,
  sortField,
  sortOrder,
  preferences,
  currentAppUser,
  currentCenter,
  appointmentsMetadata,
  requestOptions = {}
) => {
  try {
    let apptTimeFilter = null
    let apptStatusFilter = null
    let apptCenterFilter = null
    let apptAgeFilter = null
    let searchStageFilter = null
    const sort = {};
    sort[sortField] = sortOrder === "asc" ? 1 : -1;

    const pipeline = [];

    if (selectedFilters.searchQuery) {
      const searchStage = JSON.parse(preferences.screening.atlasSearchStage);
      console.log("Gio, Before stage", searchStage)
      console.log("searchStage value is BEFORE:", searchStage);
      updateQueryParams(searchStage, selectedFilters.searchQuery);
      console.log("searchStage value is AFTER:", searchStage);
      console.log("Gio, After stage", searchStage)
      pipeline.push({
        $search: searchStage,
      });

      searchStageFilter = searchStage
    }

    if (selectedFilters.facets && selectedFilters.facets.length > 0) {
      const matchFilter = GetFormattedMongoDBMatchFilter(
        selectedFilters.facets
      );
      
      apptTimeFilter = matchFilter.start
      apptStatusFilter = matchFilter.status
      apptAgeFilter = matchFilter.ageFilter
      apptCenterFilter = matchFilter.center

      pipeline.push({ $match: matchFilter });
    }

    // const project = {
    //   status: 1,
    //   start: 1,
    //   "participant[0].actor.display[0]": 1,
    //   subject: 1,
    //   "location.display": 1,
    // };

    // pipeline.push(
    //   {
    //     $match: {
    //       _id: { $nin: appointments.map((appointment) => appointment._id) },
    //     },
    //   }, // Exclude existing appointments
    //   { $project: project },
    //   { $sort: sort },
    //   { $limit: preferences.screening.loadMoreAppointments }
    // );

    const transactionID = logActivity.start(
      "Loading More Appointments",
      "User",
      "Cluster",
      "API"
    );
    logActivity.addEntry(
      transactionID,
      "code-request",
      "Query to Atlas - collection.aggregate()",
      pipeline
    );

    // const newAppointments = await collection.aggregate(pipeline);
    const responseAppointments = await axios.get(`/api/appointments`, {
      signal: requestOptions?.signal,
      params: {
        apptTime: apptTimeFilter,
        statusFilter: apptStatusFilter,
        centerFilter: apptCenterFilter,
        currentCenter: currentCenter,
        currentUser: currentAppUser,
        page: (appointmentsMetadata.page) + 1,
        pageSize: (appointmentsMetadata.pageSize),
        ageFilter: JSON.stringify(apptAgeFilter),
        searchStage: JSON.stringify(searchStageFilter),
        includeCount: "false",
      },
      paramsSerializer: (params) => {
        return new URLSearchParams(params).toString();
    }
    });

    const newAppointments = responseAppointments.data.appointments.data
    const responseMetadata = responseAppointments.data.appointments.metadata || {}
    appointmentsMetadata = {
      ...responseMetadata,
      totalCount: Number.isFinite(responseMetadata?.totalCount)
        ? responseMetadata.totalCount
        : (Number.isFinite(appointmentsMetadata?.totalCount)
            ? appointmentsMetadata.totalCount
            : (appointments.length + newAppointments.length)),
    };

    // const newAppointments = await collection.aggregate(pipeline);
    logActivity.addEntry(
      transactionID,
      "code-response",
      "Response",
      newAppointments
    );
    logActivity.end(transactionID);

    return { newAppointments, appointmentsMetadata };
  } catch (error) {
    if (isApiRequestCanceled(error)) {
      throw error;
    }
    console.error("Error loading new appointments:", error);
    throw error;
  }
};

export const APILoadPage = async (
  URL,
  preferences,
  currentAppUser,
  requestOptions = {}
) => {
  let transactionID;
  try {
    const urlValue = typeof URL === "string" ? URL : "";
    const queryString = urlValue.includes("?") ? urlValue.split("?")[1] : "";
    const requestPath = queryString
      ? `/api/get_fhir_appointments?${queryString}`
      : `/api/get_fhir_appointments`;

    transactionID = startApiTransaction(
      "Loading Appointments Page - FHIR",
      "GET /api/get_fhir_appointments page request",
      { sourceURL: URL, requestPath, currentAppUser }
    );

    const response = await axios.get(requestPath, {
      signal: requestOptions?.signal,
    });
    const fhirResult = response.data;
    const nextAppointments = postProcessFHIRResult(fhirResult);

    const links = Array.isArray(fhirResult?.link) ? fhirResult.link : [];
    const nextLink = links.find((link) => link.relation === "next") || null;
    const prevLink = links.find((link) => link.relation === "previous" || link.relation === "prev") || null;

    finishApiTransactionSuccess(transactionID, "FHIR page response", {
      total: fhirResult?.total || 0,
      nextLink,
      prevLink,
      entryCount: Array.isArray(nextAppointments) ? nextAppointments.length : 0,
      payload: fhirResult,
    });

    return { nextAppointments, nextLink, prevLink };
  } catch (error) {
    if (isApiRequestCanceled(error)) {
      throw error;
    }
    finishApiTransactionError(transactionID, "Error loading appointments page (FHIR)", error);
    console.error("Error loading new appointments:", error);
    throw error;
  }
};

function updateQueryParams(obj, queryValue) {
  for (const key in obj) {
    if (typeof obj[key] === "object" && obj[key] !== null) {
      updateQueryParams(obj[key], queryValue); // Recursively update
    } else if (key === "query") {
      obj[key] = queryValue;
    }
  }
}

// export const APICountAppointments = async (
//   selectedFilters,
//   applyFilters,
//   preferences,
//   currentAppUser,
//   currentCenter
// ) => {
//   try {
//     let apptTimeFilter = null
//     let apptStatusFilter = null
//     let apptAgeFilter = null

//     if (
//       preferences.dataSets &&
//       preferences.dataSets[0] &&
//       preferences.dataSets[0].screening.selected[
//       currentAppUser === "hc-provider" ? "HCProviderApi" : "HCAgencyApi"
//       ] === "MongoDB"
//     ) {
//       const pipeline = [];

//       if (applyFilters) {
//         if (selectedFilters.searchQuery) {
//           const searchStage = JSON.parse(
//             preferences.screening.atlasSearchStage
//           );
//           console.log("searchStage value is BEFORE:", searchStage);
//           updateQueryParams(searchStage, selectedFilters.searchQuery);
//           console.log("searchStage value is AFTER:", searchStage);
//           pipeline.push({
//             $search: searchStage,
//           });
//         }

//         if (selectedFilters.facets && selectedFilters.facets.length > 0) {
//           const matchFilter = GetFormattedMongoDBMatchFilter(
//             selectedFilters.facets
//           );
//           pipeline.push({ $match: matchFilter });

//           apptTimeFilter = matchFilter.start
//           apptStatusFilter = matchFilter.status
//           apptAgeFilter = matchFilter.ageFilter
//         }
//       }
      
//       pipeline.push({ $count: "total" });

//       const transactionID = logActivity.start(
//         "Count Appointments",
//         "User",
//         "Cluster",
//         "API"
//       );
//       logActivity.addEntry(
//         transactionID,
//         "code-request",
//         "Query to Atlas - collection.aggregate()",
//         pipeline
//       );
//       const count = await collection.aggregate(pipeline);
//       logActivity.addEntry(transactionID, "code-response", "Response", count);
//       logActivity.end(transactionID);

//       if (count.length > 0) {
//         return count[0].total;
//       } else {
//         return 0;
//       }
//     } else {
//       //FHIR
//       const params = {
//         method: "GET",
//         headers: {
//           Authorization: `Bearer ${authToken.access_token}`,
//           "Content-Type": "application/json",
//         },
//       };

//       let endpointURL;

//       if (applyFilters) {
//         const _count = preferences.screening.loadMoreAppointments;
//         const queryParams = getFormattedFHIRQueryParams(selectedFilters.facets);
//         endpointURL = `${fhirEndpoint}enrichedAppointments?${queryParams}&_count=${_count}`;
//       } else {
//         endpointURL = `${fhirEndpoint}eenrichedAppointments?_count=0`;
//       }

//       const response = await fetch(endpointURL, params);
//       const fhirResult = await response.json();

//       return fhirResult.total;
//     }
//   } catch (error) {
//     console.error("Error counting appointments:", error);
//     throw error;
//   }
// };

export const APIGetClinicalReport = async (
  appointmentId,
  preferences,
  currentAppUser
) => {
  try {
    // TODO: enable this function to work under FHIR
    const appointmentIdStr = appointmentId.toString();

    const transactionID = logActivity.start(
      "Get Clinical Report",
      "User",
      "Cluster",
      "API"
    );
    logActivity.addEntry(
      transactionID,
      "code-request",
      "Query to Atlas - collection.aggregate()",
      appointmentIdStr
    );

    // console.log("appointmentIdStr: ", appointmentIdStr)
    // console.log("collection: ", collection)

    // const report = await collection.findOne({
    //   appointmentID: appointmentIdStr,
    // });

    const responseClinicalReports = await axios.get(`/api/clinical_reports`, {
      params: {
        userAppointment: appointmentIdStr,
      },
      paramsSerializer: (params) => {
        return new URLSearchParams(params).toString();
      }
    });

    const report = responseClinicalReports.data.clinical_reports.body

    logActivity.addEntry(transactionID, "code-response", "Response", report);
    logActivity.end(transactionID);

    return report;
  } catch (error) {
    console.error("Error fetching clinical report:", error);
    throw error;
  }
};

export const APIGetClinicalReportsSummary = async (
  appointmentIds = [],
  requestOptions = {}
) => {
  let transactionID;
  try {
    const normalizedIds = Array.from(
      new Set(
        (appointmentIds || [])
          .map((id) => (id === undefined || id === null ? '' : String(id).trim()))
          .filter(Boolean)
      )
    );

    if (normalizedIds.length === 0) {
      return {};
    }

    transactionID = startApiTransaction(
      "Get Clinical Reports Summary",
      "GET /api/clinical_reports/summary params",
      { appointmentIds: normalizedIds }
    );

    const response = await axios.get(`/api${API_ROUTES.clinicalReportsSummary}`, {
      signal: requestOptions?.signal,
      params: {
        appointmentIds: normalizedIds.join(","),
      },
      paramsSerializer: (params) => new URLSearchParams(params).toString(),
    });

    const summaryByAppointment = response?.data?.summaryByAppointment || {};
    finishApiTransactionSuccess(
      transactionID,
      "Clinical reports summary response",
      summaryByAppointment
    );
    return summaryByAppointment;
  } catch (error) {
    if (isApiRequestCanceled(error)) {
      throw error;
    }
    finishApiTransactionError(transactionID, "Error loading clinical reports summary", error);
    console.error("Error loading clinical reports summary:", error);
    return {};
  }
};

export const APIVectorSearchReports = async (
  query,
  vectorizedPrompt,
  preferences,
  currentAppUser
) => {
  try {

    const pipeline = [];

    const responseVectorSearch = await axios.get(`/api/${API_ROUTES.vectorSearch}`, {
      params: {
        userQuery: query,
        embeddingModel: preferences.advancedSearch.LLMModel,
        searchStageString: preferences.advancedSearch.vectorSearchStageNew
      },
      paramsSerializer: (params) => {
        return new URLSearchParams(params).toString();
      }
    });

    const searchStageString = preferences.advancedSearch.vectorSearchStageNew;
    const searchStage = JSON.parse(searchStageString);
    
    searchStage.queryVector = vectorizedPrompt;
    
    pipeline.push({
      $vectorSearch: searchStage,
    });

    pipeline.push({
      $project: {
        vectorEmbedding: 0,
        _id: 0,
        score: {
          $meta: "vectorSearchScore",
        },
      },
    });
    
    const transactionID = logActivity.start(
      "Loading vector search results",
      "User",
      "Cluster",
      "API"
    );
    logActivity.addEntry(
      transactionID,
      "code-request",
      "Query to Atlas - collection.aggregate()",
      pipeline
    );

    // const matchingReports = await collection.aggregate(pipeline);
    const matchingReports = responseVectorSearch.data
    logActivity.addEntry(
      transactionID,
      "code-response",
      "Response",
      matchingReports
    );
    logActivity.end(transactionID);

    return matchingReports;
  } catch (error) {
    console.error("Error executing vector search query to Atlas:", error);
    throw error;
  }
};

export const APIVoyageHybridSearch = async (
  query,
  semanticQuery,
  structuredFilter,
  preferences,
  options = {}
) => {
  let transactionID;
  try {
    const payload = {
      query,
      semanticQuery: semanticQuery || query,
      structuredFilter: structuredFilter || null,
      postFilter: options.postFilter || null,
      searchStageString: preferences?.advancedSearch?.vectorSearchStageNew,
      vectorIndex: options.vectorIndex || null,
      embeddingModel: options.embeddingModel || "voyage-3.5",
      rerankModel: options.rerankModel || "rerank-2.5",
      candidatePool: options.candidatePool || 40,
      numCandidates: options.numCandidates || 160,
      limit: options.limit || 10,
    };

    transactionID = startApiTransaction(
      "Voyage Hybrid Search",
      "POST /api/search/voyage/hybrid payload",
      payload
    );

    const response = await axios.post(`/api${API_ROUTES.voyageHybridSearch}`, payload);
    finishApiTransactionSuccess(
      transactionID,
      "Voyage Hybrid Search response",
      response?.data || {}
    );
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error executing Voyage hybrid search", error);
    console.error("Error executing Voyage hybrid search:", error);
    throw error;
  }
};

export const APIGetVoyageHybridExamples = async (limit = 8) => {
  let transactionID;
  try {
    const params = { limit };
    transactionID = startApiTransaction(
      "Voyage Hybrid Example Prompts",
      "GET /api/search/voyage/examples params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.voyageHybridExamples}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(
      transactionID,
      "Voyage hybrid example prompts response",
      response?.data || {}
    );
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading Voyage hybrid example prompts", error);
    throw error;
  }
};

export const APIGetAnnotationSeries = async ({
  appointmentId,
  imageKey,
  patientId = null,
  limit = 5,
}) => {
  let transactionID;
  try {
    const queryParams = {
      appointmentId,
      imageKey,
      patientId,
      limit,
    };
    transactionID = startApiTransaction(
      "Load Annotation Series",
      "GET /api/annotations/series params",
      queryParams
    );
    const response = await axios.get(`/api${API_ROUTES.annotationSeries}`, {
      params: queryParams,
      paramsSerializer: (params) => new URLSearchParams(params).toString(),
    });
    finishApiTransactionSuccess(
      transactionID,
      "Annotation series response",
      response?.data || {}
    );
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading annotation series", error);
    console.error("Error loading annotation series:", error);
    throw error;
  }
};

export const APISaveAnnotationSeries = async (payload) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Save Annotation Series",
      "POST /api/annotations/series payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.annotationSeries}`, payload);
    finishApiTransactionSuccess(
      transactionID,
      "Saved annotation series response",
      response?.data || {}
    );
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error saving annotation series", error);
    console.error("Error saving annotation series:", error);
    throw error;
  }
};

export const APIInterpretAnnotationPrompt = async (prompt, context = {}) => {
  let transactionID;
  try {
    const payload = { prompt, context };
    transactionID = startApiTransaction(
      "Interpret Annotation Prompt",
      "POST /api/annotations/interpret payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.annotationInterpret}`, payload);
    finishApiTransactionSuccess(
      transactionID,
      "Annotation interpretation response",
      response?.data || {}
    );
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error interpreting annotation prompt", error);
    console.error("Error interpreting annotation prompt:", error);
    throw error;
  }
};

export const APIVectorSearchWithFiltersReports = async (
  vectorSearchQuery,
  preferences
) => {
  try {
    let pipeline = [];

    if (Array.isArray(vectorSearchQuery)) {
      pipeline = [...vectorSearchQuery];
    } else if (vectorSearchQuery?.$vectorSearch) {
      pipeline = [vectorSearchQuery];
    } else {
      pipeline = [{ $vectorSearch: vectorSearchQuery }];
    }

    const hasProjectStage = pipeline.some((stage) => stage.$project);
    if (!hasProjectStage) {
      pipeline.push({
        $project: {
          vectorEmbedding: 0,
          _id: 0,
          score: {
            $meta: "vectorSearchScore",
          },
        },
      });
    }

    const transactionID = logActivity.start(
      "Loading vector search results",
      "User",
      "Cluster",
      "API"
    );
    logActivity.addEntry(
      transactionID,
      "code-request",
      "Query to Atlas - collection.aggregate()",
      pipeline
    );
    const response = await axios.post(`/api${API_ROUTES.vectorSearchPipeline}`, {
      pipeline,
    });
    const matchingReports = response?.data?.results || [];
    logActivity.addEntry(
      transactionID,
      "code-response",
      "Response",
      matchingReports
    );
    logActivity.end(transactionID);

    return matchingReports;
  } catch (error) {
    console.error(
      "Error executing vector search with filters query to Atlas:",
      error
    );
    throw error;
  }
};

export const APIProcessMammography = async (params, preferences) => {
  try {
    const transactionID = logActivity.start(
      "Call Python script to start the mammography process",
      "User",
      "Cluster",
      "API"
    );

    const dicomEndpoint = resolveDicomImageEndpoint();
    const serializedParams =
      typeof params?.toString === "function" ? params.toString() : String(params || "");
    const url = `${dicomEndpoint}?${serializedParams}`;
    

    logActivity.addEntry(transactionID, "code-request", "Fetch url", url);

    const response = await fetch(url);
    const responseText = await response.text();
    logActivity.addEntry(transactionID, "code-response", "Response", {
      ok: response.ok,
      status: response.status,
      statusText: response.statusText,
      body: responseText,
    });

    if (!response.ok) {
      throw new Error(responseText || "Error executing the Dicom extract and upload");
    }

    return responseText;
  } catch (error) {
    const fallbackErrorMessage =
      "The endpoint to execute the Dicom extract and upload is not running. Please review the documentation.";
    if (error instanceof Error && error.message) {
      throw error;
    }
    throw new Error(fallbackErrorMessage);
  }
};

export const APIGetLatestDicomStudy = async (appointmentID, seriesID = null) => {
  let transactionID;
  try {
    const requestParams = {
      appointmentID,
      seriesID: seriesID || undefined,
    };

    transactionID = startApiTransaction(
      "Loading latest DICOM study",
      "GET /api/dicom_study params",
      requestParams
    );

    const response = await axios.get(`/api/dicom_study`, {
      params: requestParams,
      paramsSerializer: (params) => new URLSearchParams(params).toString(),
    });

    finishApiTransactionSuccess(
      transactionID,
      "dicom_study response",
      response?.data || {}
    );

    if (response?.data?.success) {
      return response.data.study || null;
    }
    return null;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading latest DICOM study", error);
    console.error("Error loading latest DICOM study:", error);
    return null;
  }
};


export const APISaveClinicalReport = async (
  lastDocument,
  appointmentSnapshot,
  biopsyData,
  clinicalNotes,
  conclusions,
  conclusionsSNOMED,
  vectorizedPrompt,
  vectorEmbeddingModel,
  currentUser,
  appointmentId,
  status,
  options = {}
) => {
  try {
    // Create the request object with all necessary fields from lastDocument

    const transactionID = logActivity.start(
      "Saving clinical report",
      "User",
      "Cluster",
      "API"
    );


    // Modify the series subdocument to an array of imageMetadata objects
    const series = [];
    const lateralityViewPositions = Object.keys(lastDocument.series);
    for (const laterality of lateralityViewPositions) {
      for (const viewPosition of Object.keys(lastDocument.series[laterality])) {
        const subdocument = lastDocument.series[laterality][viewPosition];
        const imageMetadata = subdocument.imageMetadata;
        series.push({ imageMetadata: imageMetadata });
      }
    }

    const clinicalReport = {
      user: currentUser,
      lastDocument,
      appointmentSnapshot,
      syntheaPatientRef:
        lastDocument?.syntheaPatientRef ||
        lastDocument?.patientID ||
        appointmentSnapshot?.syntheaPatientRef ||
        appointmentSnapshot?.resource?.subject?.reference ||
        null,
      biopsyData,
      biopsyArtifact: options.biopsyArtifact || biopsyData?.biopsyArtifact || null,
      clinicalNotes,
      conclusions,
      conclusionsSNOMED,
      vectorEmbedding: vectorizedPrompt,
      vectorEmbeddingModel: vectorEmbeddingModel || null,
      vectorEmbeddingSourceText: options.vectorEmbeddingSourceText || null,
      semanticTags: options.semanticTags || null,
      series: series,  
      numberImages: lastDocument.numberImages || 0,
      lastUpdatedLaterality: lastDocument.lastUpdatedLaterality || "",
      lastUpdatedViewPosition: lastDocument.lastUpdatedViewPosition || "",
      appointmentId,
      status
    };

    logActivity.addEntry(
      transactionID,
      "code-request",
      "Request to Atlas - collection.insertOne",
      clinicalReport
    );

    const response = await axios.post(`/api/saveClinicalReport`, clinicalReport);

    logActivity.addEntry(
      transactionID,
      "code-response",
      "Response - Inserted ID",
      1
    );
    logActivity.end(transactionID);
    

    return response;
  } catch (error) {
    console.error("APISaveClinicalReport: Error saving clinical report: ", error);
    throw error;
  }
};

export const APIUploadBiopsyArtifact = async ({
  appointmentId,
  patientId,
  imageBase64,
  fileName = "biopsy.webp",
  mimeType = "image/webp",
}) => {
  let transactionID;
  try {
    const payload = {
      appointmentId,
      patientId,
      imageBase64,
      fileName,
      mimeType,
    };
    transactionID = startApiTransaction(
      "Uploading biopsy artifact",
      "POST /api/biopsy/upload_artifact payload",
      {
        appointmentId,
        patientId,
        fileName,
        mimeType,
        base64Length: String(imageBase64 || "").length,
      }
    );
    const response = await axios.post(`/api${API_ROUTES.biopsyUploadArtifact}`, payload);
    finishApiTransactionSuccess(
      transactionID,
      "Biopsy artifact upload response",
      response?.data || {}
    );
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error uploading biopsy artifact", error);
    throw error;
  }
};

export const APIGenerateAndGetVector = async (
  query,
  preferences,
  currentAppUser,
  forcedModel = null
) => {
  try {
    const model = forcedModel || preferences.advancedSearch.LLMModel;
    // const endpoint = preferences.advancedSearch.LLMAPI;
    const endpoint = `/api/getVector`; 
    
    const transactionID = logActivity.start(
      "Calling python script to get vector",
      "User",
      "Cluster",
      "API"
    );
    logActivity.addEntry(
      transactionID,
      "html",
      "Data flow",
      "vectorSearchArch/index.html"
    );
    logActivity.addEntry(
      transactionID,
      "code-request",
      `Request to endpoint: ${endpoint}?query=${query}&model=${model}`,
      query
    );

    const response = await axios.get(
      `${endpoint}?query=${query}&model=${model}`
    );
    const vectorizedPrompt = response.data;
    logActivity.addEntry(
      transactionID,
      "code-response",
      "Response - Vectorized Prompt",
      vectorizedPrompt
    );
    logActivity.end(transactionID);

    return vectorizedPrompt;
  } catch (error) {
    console.error("Error generating and obtaining vector:", error);
    throw error;
  }
};

export const APIGetCompletion = async (query, preferences, currentAppUser) => {
  let transactionID;
  try {
    console.log("APIGetCompletion: Starting", {
      query,
      preferences,
      currentAppUser,
    });
    console.log("Im here");
    const configuredEndpoint = preferences?.advancedSearch?.fineTunedSearch?.endpoint;
    const endpoint =
      configuredEndpoint && configuredEndpoint !== "undefined"
        ? configuredEndpoint
        : `/api${API_ROUTES.generateClinicalNotes}`;
    const apikey = preferences?.advancedSearch?.fineTunedSearch?.apikey;
    const model =
      preferences?.advancedSearch?.fineTunedSearch?.model ||
      process.env.REACT_APP_NLP_MODEL ||
      "gpt-4o-mini";

    transactionID = logActivity.start(
      "Calling tuned model to get assistantAnswer",
      "User",
      "Cluster",
      "API"
    );

    logActivity.addEntry(
      transactionID,
      "html",
      "Data flow",
      "vectorSearchArch/index.html"
    );

    const normalizedQuery =
      query && query.includes("<query>") ? query : `<query>${query}</query>`;

    const data = {
      model: model,
      response_format: { type: "json_object" },
      messages: [
        {
          role: "system",
          content: "You are a helpful assistant designed to output JSON.",
        },
        { role: "user", content: normalizedQuery },
      ],
      max_tokens: 256,
    };

    const headers = {
      "Content-Type": "application/json",
    };
    if (!endpoint.startsWith("/api") && apikey) {
      headers.Authorization = "Bearer " + apikey;
    }

    logActivity.addEntry(
      transactionID,
      "code-request",
      "Completion request payload",
      {
        endpoint,
        headers: endpoint.startsWith("/api")
          ? headers
          : { ...headers, Authorization: headers.Authorization ? "[redacted]" : undefined },
        data,
      }
    );

    const response = await axios.post(endpoint, data, { headers: headers });
    const responseData = response.data;
    logActivity.addEntry(
      transactionID,
      "code-response",
      "Completion raw response",
      responseData
    );

    console.log("APIGetChatCompletion: Completion received", responseData);

    // Supports two response shapes:
    // 1) OpenAI-compatible: { choices: [{ message: { content: \"{...}\" }}] }
    // 2) Direct JSON object: { assistantAnswer: {...} } or pipeline object itself.
    if (responseData?.choices?.[0]?.message?.content) {
      const parsed = JSON.parse(responseData.choices[0].message.content);
      console.log("assistantAnswer(parsed from choices)", parsed);
      logActivity.addEntry(
        transactionID,
        "code-response",
        "Completion parsed response",
        parsed
      );
      logActivity.end(transactionID);
      return parsed;
    }

    if (responseData?.assistantAnswer || responseData?.pipeline) {
      logActivity.end(transactionID);
      return responseData;
    }

    throw new Error("Unexpected completion response shape");
  } catch (error) {
    finishApiTransactionError(transactionID, "Error retrieving assistant answer", error);
    console.error(
      "APIGetCompletion: Error retrieving assistant answer:",
      error
    );
    throw error;
  }
};

export const APIStoreEmbeddings = async (
  data,
  preferences,
  _currentAppUser,
  onProgress
) => {
  if (!preferences.screening.generateTextService.authorization) {
    throw new Error(
      "OpenAI API key is not configured. Please check your preferences."
    );
  }

  try {
    const totalChunks = data.length;

    // Process in smaller batches to avoid overwhelming the API
    const batchSize = 10;
    for (let i = 0; i < data.length; i += batchSize) {
      const batch = data.slice(i, i + batchSize);

      onProgress({
        stage: "embedding",
        current: i + batch.length,
        total: totalChunks,
        message: `Processing batch ${Math.floor(i / batchSize) + 1
          }: Generating embeddings for chunks ${i + 1}-${Math.min(
            i + batchSize,
            totalChunks
          )}`,
      });

      // Process batch
      const insertPromises = batch.map(async (chunk) => {
        const response = await fetch("https://api.openai.com/v1/embeddings", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${preferences.screening.generateTextService.authorization}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            model: "text-embedding-ada-002",
            input: chunk.text,
          }),
        });

        if (!response.ok) {
          throw new Error(`OpenAI API error: ${response.statusText}`);
        }

        const result = await response.json();
        const vector = result.data[0].embedding;

        return {
          text_chunk: chunk.text,
          vector_embedding: vector,
          source: {
            filename: chunk.filename,
            page_number: chunk.page_number,
          },
        };
      });

      const documents = await Promise.all(insertPromises);

      // Store batch
      onProgress({
        stage: "storing",
        current: i + batch.length,
        total: totalChunks,
        message: `Storing batch ${Math.floor(i / batchSize) + 1} in database`,
      });

      await axios.post(`/api${API_ROUTES.guidesEmbeddings}`, {
        documents,
      });

      // Small delay between batches
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  } catch (error) {
    console.error("Error storing embeddings:", error);
    throw new Error("Failed to store document embeddings. Please try again.");
  }
};

export const APIAnswerQuestion = async (
  question,
  preferences
) => {
  try {
    const transactionID = logActivity.start(
      "Answering Question",
      "User",
      "Cluster",
      "API"
    );
    const response = await axios.post(`/api${API_ROUTES.guidesAnswer}`, {
      question,
      topK: 10,
      numCandidates: 200,
      embeddingModel: "text-embedding-3-small",
      answerModel: preferences?.openai?.answerModel || "gpt-4o-mini",
      apiKey: preferences?.screening?.generateTextService?.authorization || null,
    });
    const answer = response?.data?.answer || "";
    const sources = Array.isArray(response?.data?.sources) ? response.data.sources : [];

    logActivity.end(transactionID);

    return {
      answer,
      sources,
    };
  } catch (error) {
    console.error("Error answering question:", error);
    throw new Error("Failed to get an answer. Please try again.");
  }
};

export const APIGetMBClassification = async (data, performer = null) => {
  try {
    const configuredEndpoint = (process.env.REACT_APP_CLASSIFICATION_API || "").trim();
    let classificationApiUrl = configuredEndpoint || "/api/getClassification";

    // Normalize relative endpoints to the app proxy namespace.
    if (!/^https?:\/\//i.test(classificationApiUrl)) {
      if (!classificationApiUrl.startsWith("/")) {
        classificationApiUrl = `/${classificationApiUrl}`;
      }
      if (!classificationApiUrl.startsWith("/api/")) {
        classificationApiUrl = `/api${classificationApiUrl}`;
      }
    }

    const response = await axios.get(classificationApiUrl, {
      params: {
        data,
        performer: performer || undefined,
      },
      paramsSerializer: (params) => new URLSearchParams(params).toString(),
    });
    console.log("Classification response:", response.data);
    return response;
  } catch (error) {
    console.error("APIGetMBClassification: Error predicting:", error);
    throw error;
  }
};

export const APIBootstrapPharmaData = async (options = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Bootstrap Pharma Data",
      "POST /api/pharma/bootstrap payload",
      options
    );
    const response = await axios.post(`/api${API_ROUTES.pharmaBootstrap}`, options);
    finishApiTransactionSuccess(transactionID, "Pharma bootstrap response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error bootstrapping pharma data", error);
    throw error;
  }
};

export const APIListPharmaTrials = async (params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "List Pharma Trials",
      "GET /api/pharma/trials/list params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.pharmaTrialsList}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Pharma trials list response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error listing pharma trials", error);
    throw error;
  }
};

export const APIGetPharmaSourceStatus = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Pharma Source Status",
      "GET /api/pharma/source/status",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.pharmaSourceStatus}`);
    finishApiTransactionSuccess(transactionID, "Pharma source status response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading pharma source status", error);
    throw error;
  }
};

export const APIGetPharmaControlPlane = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Pharma Control Plane",
      "GET /api/pharma/control_plane",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.pharmaControlPlane}`);
    finishApiTransactionSuccess(transactionID, "Pharma control plane response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading pharma control plane", error);
    throw error;
  }
};

export const APIRunPharmaControlPlaneEvaluation = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Pharma Control Evaluation",
      "POST /api/pharma/control_plane/evaluations/run payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.pharmaControlPlaneEvalRun}`, payload);
    finishApiTransactionSuccess(transactionID, "Pharma control evaluation response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error running pharma control-plane evaluation", error);
    throw error;
  }
};

export const APISyncPharmaClinicalTrials = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Sync ClinicalTrials.gov",
      "POST /api/pharma/sync/clinicaltrials payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.pharmaSyncClinicalTrials}`, payload);
    finishApiTransactionSuccess(transactionID, "ClinicalTrials.gov sync response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error syncing ClinicalTrials.gov studies", error);
    throw error;
  }
};

export const APIGetPharmaPatientContext = async (appointmentId, patientId = null) => {
  let transactionID;
  try {
    const params = {
      appointmentId,
      patientId: patientId || undefined,
    };
    transactionID = startApiTransaction(
      "Get Pharma Patient Context",
      "GET /api/pharma/patient_context params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.pharmaPatientContext}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Pharma patient context response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading pharma patient context", error);
    throw error;
  }
};

export const APIGetRandomPharmaPatientContext = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Get Random Pharma Patient Context",
      "GET /api/api/v1/patients/random",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.productPatientRandom}`);
    finishApiTransactionSuccess(transactionID, "Random patient workspace response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading random patient workspace", error);
    throw error;
  }
};

export const APIGetPharmaPatientEvidenceProfile = async (params = {}) => {
  let transactionID;
  try {
    const patientRef = String(params?.patientRef || "").trim();
    if (!patientRef) {
      throw new Error("patientRef is required for product patient evidence.");
    }
    transactionID = startApiTransaction(
      "Get Pharma Patient Evidence Profile",
      `GET /api/api/v1/patients/${patientRef}/evidence params`,
      { patientRef, ...params }
    );
    const response = await axios.get(
      `/api${API_ROUTES.productPatientWorkspace}/${encodeURIComponent(patientRef)}/evidence`,
      {
      params: {
        includeRaw: params?.includeRaw,
      },
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Product patient evidence response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading product patient evidence", error);
    throw error;
  }
};

export const APIGetPharmaPatientMatchProfile = async (params = {}) => {
  let transactionID;
  try {
    const patientRef = String(params?.patientRef || "").trim();
    if (!patientRef) {
      throw new Error("patientRef is required for product patient match profile.");
    }
    transactionID = startApiTransaction(
      "Get Pharma Patient Match Profile",
      `GET /api/api/v1/patients/${patientRef}/match-profile params`,
      { patientRef, ...params }
    );
    const response = await axios.get(
      `/api${API_ROUTES.productPatientWorkspace}/${encodeURIComponent(patientRef)}/match-profile`,
      {
      params: {
        includeRaw: params?.includeRaw,
      },
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Product patient match profile response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading product patient match profile", error);
    throw error;
  }
};

export const APIListProductPatients = async (params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "List Product Patients",
      "GET /api/api/v1/patients/catalog params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.productPatientCatalog}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Product patient catalog response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading product patient catalog", error);
    throw error;
  }
};

export const APIGetProductPatientWorkspace = async (patientRef, params = {}) => {
  let transactionID;
  try {
    const normalizedRef = String(patientRef || "").trim();
    if (!normalizedRef) {
      throw new Error("patientRef is required for product patient workspace.");
    }
    transactionID = startApiTransaction(
      "Get Product Patient Workspace",
      `GET /api/api/v1/patients/${normalizedRef}/workspace params`,
      { patientRef: normalizedRef, ...params }
    );
    const response = await axios.get(
      `/api${API_ROUTES.productPatientWorkspace}/${encodeURIComponent(normalizedRef)}/workspace`,
      {
        params,
        paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
      }
    );
    finishApiTransactionSuccess(transactionID, "Product patient workspace response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading product patient workspace", error);
    throw error;
  }
};

export const APIGetSyntheaPatientEmr = async (patientRef, limit = 120) => {
  let transactionID;
  try {
    const params = { patientRef, limit };
    transactionID = startApiTransaction(
      "Get Synthea Patient EMR",
      "GET /api/pharma/synthea/patient_emr params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.pharmaSyntheaPatientEmr}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Synthea patient EMR response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading Synthea patient EMR", error);
    throw error;
  }
};

export const APIMatchPharmaTrials = async (payload) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Match Pharma Trials",
      "POST /api/pharma/trials/match payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.pharmaTrialMatch}`, payload);
    finishApiTransactionSuccess(transactionID, "Pharma trial match response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error matching pharma trials", error);
    throw error;
  }
};

export const APIRunPharmaTrialLlmAdjudication = async (payload) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Run Pharma LLM Adjudication",
      "POST /api/pharma/trials/llm_adjudicate payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.pharmaTrialLlmAdjudicate}`, payload);
    finishApiTransactionSuccess(transactionID, "Pharma LLM adjudication response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error running pharma LLM adjudication", error);
    throw error;
  }
};

export const APIGetPharmaTrialLlmAdjudicationStatus = async (jobId) => {
  let transactionID;
  try {
    const params = { jobId };
    transactionID = startApiTransaction(
      "Get Pharma LLM Adjudication Status",
      "GET /api/pharma/trials/llm_adjudicate/status params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.pharmaTrialLlmAdjudicateStatus}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Pharma LLM adjudication status response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading pharma LLM adjudication status", error);
    throw error;
  }
};

export const APIMatchPharmaTrialCandidatePatients = async (payload) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Match Trial Candidate Patients",
      "POST /api/pharma/trials/candidate_patients payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.pharmaTrialCandidatePatients}`, payload);
    finishApiTransactionSuccess(transactionID, "Trial candidate patients response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error matching selected trial to patient candidates", error);
    throw error;
  }
};

export const APIBackfillPharmaEmbeddings = async (options = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Backfill Pharma Embeddings",
      "POST /api/pharma/trials/backfill_embeddings payload",
      options
    );
    const response = await axios.post(`/api${API_ROUTES.pharmaBackfillEmbeddings}`, options);
    finishApiTransactionSuccess(transactionID, "Pharma backfill embeddings response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error backfilling pharma embeddings", error);
    throw error;
  }
};

export const APIReconcilePharmaLinkage = async (options = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Reconcile Pharma Linkage",
      "POST /api/pharma/linkage/reconcile payload",
      options
    );
    const response = await axios.post(`/api${API_ROUTES.pharmaLinkageReconcile}`, options);
    finishApiTransactionSuccess(transactionID, "Pharma linkage reconcile response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error reconciling pharma linkage", error);
    throw error;
  }
};

export const APIGetClinicalTrialsHealth = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Clinical Trials Service Health",
      "GET /api/clinical-trials/health",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsHealth}`);
    finishApiTransactionSuccess(transactionID, "Clinical trials health response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trials service health", error);
    throw error;
  }
};

export const APISearchClinicalTrials = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Search Clinical Trials Ingestion Catalog",
      "POST /api/clinical-trials/search payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.clinicalTrialsSearch}`, payload);
    finishApiTransactionSuccess(transactionID, "Clinical trials search response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error searching clinical trials", error);
    throw error;
  }
};

export const APIIngestClinicalTrial = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Ingest Clinical Trial",
      "POST /api/clinical-trials/ingest payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.clinicalTrialsIngest}`, payload);
    finishApiTransactionSuccess(transactionID, "Clinical trial ingest response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error ingesting clinical trial", error);
    throw error;
  }
};

export const APIBulkIngestClinicalTrials = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Bulk Ingest Clinical Trials",
      "POST /api/clinical-trials/ingest/bulk payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.clinicalTrialsBulkIngest}`, payload);
    finishApiTransactionSuccess(transactionID, "Clinical trials bulk ingest response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error starting clinical trials bulk ingest", error);
    throw error;
  }
};

export const APICompileClinicalTrialPlayground = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Compile Clinical Trial Playground Rule",
      "POST /api/clinical-trials/playground/compile payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.clinicalTrialsPlaygroundCompile}`, payload);
    finishApiTransactionSuccess(transactionID, "Clinical trial playground compile response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error compiling playground clinical trial rule", error);
    throw error;
  }
};

export const APIListClinicalTrialRuns = async (params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "List Clinical Trial Runs",
      "GET /api/clinical-trials/runs params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsRuns}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Clinical trial runs response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error listing clinical trial runs", error);
    throw error;
  }
};

export const APIGetClinicalTrialRun = async (runId) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Get Clinical Trial Run",
      `GET /api/clinical-trials/runs/${runId}`,
      { runId }
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsRuns}/${encodeURIComponent(runId)}`);
    finishApiTransactionSuccess(transactionID, "Clinical trial run response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trial run", error);
    throw error;
  }
};

export const APIListClinicalTrials = async (params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "List Ingested Clinical Trials",
      "GET /api/clinical-trials/trials params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsTrials}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Ingested clinical trials response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error listing ingested clinical trials", error);
    throw error;
  }
};

export const APIGetClinicalTrial = async (trialId) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Get Ingested Clinical Trial",
      `GET /api/clinical-trials/trials/${trialId}`,
      { trialId }
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsTrials}/${encodeURIComponent(trialId)}`);
    finishApiTransactionSuccess(transactionID, "Clinical trial detail response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trial detail", error);
    throw error;
  }
};

export const APIGetClinicalTrialCriteria = async (trialId, params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Get Clinical Trial Criteria",
      `GET /api/clinical-trials/trials/${trialId}/criteria params`,
      { trialId, ...params }
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsTrials}/${encodeURIComponent(trialId)}/criteria`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Clinical trial criteria response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trial criteria", error);
    throw error;
  }
};

export const APIGetClinicalTrialCanonicalTree = async (trialId, params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Get Clinical Trial Canonical Tree",
      `GET /api/clinical-trials/trials/${trialId}/canonical-tree params`,
      { trialId, ...params }
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsCanonicalTree}/${encodeURIComponent(trialId)}/canonical-tree`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Clinical trial canonical tree response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trial canonical tree", error);
    throw error;
  }
};

export const APIListClinicalTrialCriteria = async (params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "List Clinical Trial Criteria",
      "GET /api/clinical-trials/criteria params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsCriteria}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Clinical trial criteria list response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error listing clinical trial criteria", error);
    throw error;
  }
};

export const APIGetClinicalTrialReviewQueue = async (params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Clinical Trial Review Queue",
      "GET /api/clinical-trials/review-queue params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsReviewQueue}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Clinical trial review queue response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trial review queue", error);
    throw error;
  }
};

export const APIApplyClinicalTrialReview = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Apply Clinical Trial Review",
      "POST /api/clinical-trials/reviews/apply payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.clinicalTrialsReviewApply}`, payload);
    finishApiTransactionSuccess(transactionID, "Clinical trial review apply response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error applying clinical trial review", error);
    throw error;
  }
};

export const APIRecompileClinicalTrial = async (trialId, payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Recompile Clinical Trial",
      `POST /api/clinical-trials/recompile/trial/${trialId} payload`,
      { trialId, ...payload }
    );
    const response = await axios.post(`/api${API_ROUTES.clinicalTrialsRecompileTrial}/${encodeURIComponent(trialId)}`, payload);
    finishApiTransactionSuccess(transactionID, "Clinical trial recompile response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error recompiling clinical trial", error);
    throw error;
  }
};

export const APIRecompileClinicalTrialFamily = async (familyId, payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Recompile Clinical Trial Family",
      `POST /api/clinical-trials/recompile/family/${familyId} payload`,
      { familyId, ...payload }
    );
    const response = await axios.post(`/api${API_ROUTES.clinicalTrialsRecompileFamily}/${encodeURIComponent(familyId)}`, payload);
    finishApiTransactionSuccess(transactionID, "Clinical trial family recompile response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error recompiling clinical trial family", error);
    throw error;
  }
};

export const APIListClinicalTrialRuleFamilies = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "List Clinical Trial Rule Families",
      "GET /api/clinical-trials/rule-families",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsRuleFamilies}`);
    finishApiTransactionSuccess(transactionID, "Clinical trial rule families response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trial rule families", error);
    throw error;
  }
};

export const APIGetClinicalTrialUnresolvedTop = async (params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Clinical Trial Unresolved Backlog",
      "GET /api/clinical-trials/analytics/unresolved-top params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsAnalyticsUnresolvedTop}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Clinical trial unresolved backlog response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trial unresolved backlog", error);
    throw error;
  }
};

export const APIGetClinicalTrialFamilyCoverage = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Clinical Trial Family Coverage",
      "GET /api/clinical-trials/analytics/family-coverage",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsAnalyticsFamilyCoverage}`);
    finishApiTransactionSuccess(transactionID, "Clinical trial family coverage response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trial family coverage", error);
    throw error;
  }
};

export const APIGetClinicalTrialMicroBenchmarkLatest = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Get Clinical Trials Micro Benchmark Latest",
      "GET /api/clinical-trials/benchmarks/micro/latest",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsMicroBenchmarkLatest}`);
    finishApiTransactionSuccess(transactionID, "Clinical trials micro benchmark latest response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading clinical trials micro benchmark latest", error);
    throw error;
  }
};

export const getClinicalTrialMicroBenchmarkReviewSheetUrl = () =>
  `/api${API_ROUTES.clinicalTrialsMicroBenchmarkReviewSheet}`;

const normalizeClinicalTrialMatchPayload = (payload = {}) => {
  const nextPayload = { ...payload };
  if (Object.prototype.hasOwnProperty.call(nextPayload, "populationMode")) {
    nextPayload.population_mode = nextPayload.populationMode;
    delete nextPayload.populationMode;
  }
  if (Object.prototype.hasOwnProperty.call(nextPayload, "trialIds")) {
    nextPayload.trial_ids = nextPayload.trialIds;
    delete nextPayload.trialIds;
  }
  if (Object.prototype.hasOwnProperty.call(nextPayload, "patientRefs")) {
    nextPayload.patient_refs = nextPayload.patientRefs;
    delete nextPayload.patientRefs;
  }
  if (Object.prototype.hasOwnProperty.call(nextPayload, "benchmarkPackId")) {
    nextPayload.benchmark_pack_id = nextPayload.benchmarkPackId;
    delete nextPayload.benchmarkPackId;
  }
  if (Object.prototype.hasOwnProperty.call(nextPayload, "benchmarkLabel")) {
    nextPayload.benchmark_label = nextPayload.benchmarkLabel;
    delete nextPayload.benchmarkLabel;
  }
  if (Object.prototype.hasOwnProperty.call(nextPayload, "runType")) {
    nextPayload.run_type = nextPayload.runType;
    delete nextPayload.runType;
  }
  if (Object.prototype.hasOwnProperty.call(nextPayload, "stageTargetWindow")) {
    nextPayload.stage_target_window = nextPayload.stageTargetWindow;
    delete nextPayload.stageTargetWindow;
  }
  if (Object.prototype.hasOwnProperty.call(nextPayload, "includeConflictPatients")) {
    nextPayload.include_conflict_patients = nextPayload.includeConflictPatients;
    delete nextPayload.includeConflictPatients;
  }
  return nextPayload;
};

export const APIMatchClinicalTrialsForPatient = async (patientRef, payload = {}) => {
  let transactionID;
  try {
    const normalizedPayload = normalizeClinicalTrialMatchPayload(payload);
    transactionID = startApiTransaction(
      "Canonical Match Patient To Trials",
      `POST /api/clinical-trials/match/patient/${patientRef} payload`,
      { patientRef, ...normalizedPayload }
    );
    const response = await axios.post(
      `/api${API_ROUTES.clinicalTrialsMatchPatient}/${encodeURIComponent(patientRef)}`,
      normalizedPayload
    );
    finishApiTransactionSuccess(transactionID, "Canonical patient-to-trials match response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error running canonical patient-to-trials match", error);
    throw error;
  }
};

export const APIMatchClinicalTrialToPatients = async (trialId, payload = {}) => {
  let transactionID;
  try {
    const normalizedPayload = normalizeClinicalTrialMatchPayload(payload);
    transactionID = startApiTransaction(
      "Canonical Match Trial To Patients",
      `POST /api/clinical-trials/match/trial/${trialId} payload`,
      { trialId, ...normalizedPayload }
    );
    const response = await axios.post(
      `/api${API_ROUTES.clinicalTrialsMatchTrial}/${encodeURIComponent(trialId)}`,
      normalizedPayload
    );
    finishApiTransactionSuccess(transactionID, "Canonical trial-to-patients match response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error running canonical trial-to-patients match", error);
    throw error;
  }
};

async function* _readSSEStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            yield JSON.parse(line.slice(6));
          } catch {
            // skip malformed lines
          }
        }
      }
    }
  } finally {
    reader.cancel();
  }
}

export async function* APIMatchClinicalTrialsForPatientStream(patientRef, payload = {}) {
  const normalizedPayload = normalizeClinicalTrialMatchPayload(payload);
  const response = await fetch(
    `/api${API_ROUTES.clinicalTrialsMatchPatient}/${encodeURIComponent(patientRef)}/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(normalizedPayload),
      cache: "no-store",
    }
  );
  if (!response.ok) {
    const text = await response.text().catch(() => String(response.status));
    throw new Error(`Match stream failed: ${response.status} ${text}`);
  }
  yield* _readSSEStream(response);
}

export async function* APIMatchClinicalTrialToPatientsStream(trialId, payload = {}) {
  const normalizedPayload = normalizeClinicalTrialMatchPayload(payload);
  const response = await fetch(
    `/api${API_ROUTES.clinicalTrialsMatchTrial}/${encodeURIComponent(trialId)}/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(normalizedPayload),
      cache: "no-store",
    }
  );
  if (!response.ok) {
    const text = await response.text().catch(() => String(response.status));
    throw new Error(`Match stream failed: ${response.status} ${text}`);
  }
  yield* _readSSEStream(response);
}

export const APIListClinicalTrialMatchRuns = async (params = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "List Canonical Match Runs",
      "GET /api/clinical-trials/match-runs params",
      params
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsMatchRuns}`, {
      params,
      paramsSerializer: (queryParams) => new URLSearchParams(queryParams).toString(),
    });
    finishApiTransactionSuccess(transactionID, "Canonical match runs response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error listing canonical match runs", error);
    throw error;
  }
};

export const APIGetClinicalTrialMatchRun = async (runId) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Get Canonical Match Run",
      `GET /api/clinical-trials/match-runs/${runId}`,
      { runId }
    );
    const response = await axios.get(`/api${API_ROUTES.clinicalTrialsMatchRuns}/${encodeURIComponent(runId)}`);
    finishApiTransactionSuccess(transactionID, "Canonical match run response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading canonical match run", error);
    throw error;
  }
};

export const APIAskAgenticQuestion = async (payload) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Agentic Diagnostic Chat",
      "POST /api/chatbot/agentic payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.chatbotAgentic}`, payload);
    finishApiTransactionSuccess(transactionID, "Agentic diagnostic chat response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error in agentic diagnostic chat", error);
    throw error;
  }
};

export const APIListCopilotSessions = async (limit = 20, scope = "") => {
  try {
    const scopePart = scope ? `&scope=${encodeURIComponent(scope)}` : "";
    const response = await axios.get(`/api/copilot/sessions?limit=${limit}${scopePart}`);
    return response.data;
  } catch (error) {
    console.error("Error listing copilot sessions:", error);
    return { success: false, sessions: [] };
  }
};

export const APILoadCopilotSession = async (conversationId, scope = "") => {
  try {
    const scopePart = scope ? `?scope=${encodeURIComponent(scope)}` : "";
    const response = await axios.get(`/api/copilot/sessions/${encodeURIComponent(conversationId)}${scopePart}`);
    return response.data;
  } catch (error) {
    console.error("Error loading copilot session:", error);
    return { success: false };
  }
};

export const APIGetAgentTools = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "List Agent Tools",
      "GET /api/agent/tools",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.agentToolsList}`);
    finishApiTransactionSuccess(transactionID, "Agent tools list response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error listing agent tools", error);
    throw error;
  }
};

const attachControlPlaneMeta = (payload, meta = {}) => {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return payload;
  }
  return {
    ...payload,
    __meta: {
      ...(payload.__meta || {}),
      ...meta,
    },
  };
};

const requestControlPlane = async (method, route, options = {}) => {
  const proxyUrl = `/api${route}`;
  const response = await axios({
    method,
    url: proxyUrl,
    ...options,
  });
  return attachControlPlaneMeta(response.data, { transport: "proxy", url: proxyUrl });
};

export const APIGetCopilotControlOverview = async () => {
  try {
    return await requestControlPlane("get", API_ROUTES.copilotControlOverview);
  } catch (error) {
    console.error("Error loading copilot control overview:", error);
    return { success: false, error: error?.message || "Failed to load control plane overview." };
  }
};

export const APIGetCopilotControlArchitecture = async () => {
  try {
    return await requestControlPlane("get", API_ROUTES.copilotControlArchitecture);
  } catch (error) {
    console.error("Error loading copilot control architecture:", error);
    return { success: false, error: error?.message || "Failed to load control plane architecture." };
  }
};

export const APIGetCopilotQuestionFactory = async () => {
  try {
    return await requestControlPlane("get", API_ROUTES.copilotControlQuestionFactory);
  } catch (error) {
    console.error("Error loading question factory:", error);
    return { success: false, error: error?.message || "Failed to load question factory." };
  }
};

export const APIListCopilotPlanTraces = async (limit = 25) => {
  try {
    return await requestControlPlane("get", API_ROUTES.copilotControlTraces, {
      params: { limit },
    });
  } catch (error) {
    console.error("Error loading copilot plan traces:", error);
    return { success: false, traces: [], error: error?.message || "Failed to load plan traces." };
  }
};

export const APIListCopilotUnresolvedDemand = async (limit = 25) => {
  try {
    return await requestControlPlane("get", API_ROUTES.copilotControlUnresolved, {
      params: { limit },
    });
  } catch (error) {
    console.error("Error loading unresolved copilot demand:", error);
    return { success: false, items: [], error: error?.message || "Failed to load unresolved demand." };
  }
};

export const APIListCopilotEvaluationRuns = async (limit = 10) => {
  try {
    return await requestControlPlane("get", API_ROUTES.copilotControlEvalRuns, {
      params: { limit },
    });
  } catch (error) {
    console.error("Error loading copilot evaluation runs:", error);
    return { success: false, runs: [], error: error?.message || "Failed to load evaluation runs." };
  }
};

export const APIRunCopilotEvaluation = async (payload = {}) => {
  try {
    return await requestControlPlane("post", API_ROUTES.copilotControlEvalRun, {
      data: payload,
    });
  } catch (error) {
    console.error("Error running copilot evaluation:", error);
    return { success: false, error: error?.message || "Failed to run evaluation." };
  }
};

export const APIRunCopilotPlayground = async (payload = {}) => {
  try {
    return await requestControlPlane("post", API_ROUTES.copilotControlPlaygroundRun, {
      data: payload,
    });
  } catch (error) {
    console.error("Error running copilot playground:", error);
    return { success: false, error: error?.message || "Failed to run playground." };
  }
};

export const APIGenerateCopilotQuestionFactory = async (payload = {}) => {
  try {
    return await requestControlPlane("post", API_ROUTES.copilotControlQuestionFactoryGenerate, {
      data: payload,
    });
  } catch (error) {
    console.error("Error generating question factory batch:", error);
    return { success: false, error: error?.message || "Failed to generate question factory batch." };
  }
};

export const APIPromoteCopilotQuestionFactoryCandidate = async (candidateId, payload = {}) => {
  try {
    return await requestControlPlane("post", `${API_ROUTES.copilotControlQuestionFactory}/candidates/${candidateId}/promote`, {
      data: payload,
    });
  } catch (error) {
    console.error("Error promoting question factory candidate:", error);
    return { success: false, error: error?.message || "Failed to promote question factory candidate." };
  }
};

export const APIUpsertCopilotQuestionContract = async (payload = {}) => {
  try {
    return await requestControlPlane("post", API_ROUTES.copilotControlQuestionUpsert, {
      data: payload,
    });
  } catch (error) {
    console.error("Error saving copilot question contract:", error);
    return { success: false, error: error?.message || "Failed to save question contract." };
  }
};

export const APIGetCopilotControlProductDetail = async (productId) => {
  try {
    return await requestControlPlane(
      "get",
      `${API_ROUTES.copilotControlProductDetail}/${encodeURIComponent(productId)}`,
    );
  } catch (error) {
    console.error("Error loading copilot product detail:", error);
    return { success: false, error: error?.message || "Failed to load product detail." };
  }
};

export const APIInvokeAgentTool = async (payload) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Invoke Agent Tool",
      "POST /api/agent/tools/invoke payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.agentToolsInvoke}`, payload);
    finishApiTransactionSuccess(transactionID, "Agent tool invoke response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error invoking agent tool", error);
    throw error;
  }
};

export const APIGetAnalyticsStreamObservatory = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Load Stream Observatory",
      "GET /api/analytics/stream_observatory",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.analyticsStreamObservatory}`);
    finishApiTransactionSuccess(transactionID, "Stream observatory response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading stream observatory", error);
    throw error;
  }
};

export const APIDemoChangeAppointmentStatus = async (payload) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Bootstrap Appointment Status Projection",
      "POST /api/analytics/demo/appointment_status payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.analyticsDemoAppointmentStatus}`, payload);
    finishApiTransactionSuccess(transactionID, "Appointment status projection response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error changing appointment status projection", error);
    throw error;
  }
};

export const APILiveChangeAppointmentStatus = async (payload) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Update Operational Appointment Workflow",
      "POST /api/analytics/live/appointment_status payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.analyticsLiveAppointmentStatus}`, payload);
    finishApiTransactionSuccess(transactionID, "Operational appointment workflow response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error updating operational appointment workflow", error);
    throw error;
  }
};

export const APIGetLiveAppointmentStatusSummary = async () => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Load Live Appointment Status Summary",
      "GET /api/analytics/live/appointment_status_summary",
      {}
    );
    const response = await axios.get(`/api${API_ROUTES.analyticsLiveAppointmentStatusSummary}`);
    finishApiTransactionSuccess(transactionID, "Live appointment status summary response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error loading live appointment status summary", error);
    throw error;
  }
};

export const APISimulateDeviceEvent = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Simulate Device Event",
      "POST /api/analytics/device_events/simulate payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.analyticsSimulateDeviceEvent}`, payload);
    finishApiTransactionSuccess(transactionID, "Synthetic device event response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error simulating device event", error);
    throw error;
  }
};

export const APIDemoTerminologyBinding = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Run Terminology Binding Demo",
      "POST /api/analytics/demo/terminology_binding payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.analyticsDemoTerminologyBinding}`, payload);
    finishApiTransactionSuccess(transactionID, "Terminology binding demo response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error running terminology binding demo", error);
    throw error;
  }
};

export const APISimulateDeviceSession = async (payload = {}) => {
  let transactionID;
  try {
    transactionID = startApiTransaction(
      "Simulate Device Session Window",
      "POST /api/analytics/device_events/simulate_session payload",
      payload
    );
    const response = await axios.post(`/api${API_ROUTES.analyticsSimulateDeviceSession}`, payload);
    finishApiTransactionSuccess(transactionID, "Device session window response", response?.data || {});
    return response.data;
  } catch (error) {
    finishApiTransactionError(transactionID, "Error simulating device session window", error);
    throw error;
  }
};
