"use client";

import React, { useState, useEffect, useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import PharmaLanding from "./spaces/PharmaLanding";
import TrialIngestionStudio from "./spaces/TrialIngestionStudio";
import PatientCurationStudio from "./spaces/PatientCurationStudio";
import MatchingStudio from "./spaces/MatchingStudio";
import ControlPlaneStudio from "./spaces/ControlPlaneStudio";
import PatternArticle from "./spaces/PatternArticle";
import AtlasWorkbenchShell from "../../../../components/AtlasWorkbenchShell";

const VALID_SPACES = [
  "landing",
  "ingestion",
  "patient",
  "matching",
  "control",
  "pattern",
];
const VALID_POPULATION_MODES = ["authoritative_oncology", "pharmacy_prescreen"];
const VALID_DEMO_LENSES = ["discovery", "breast_oncology_ai"];
const VALID_STAGE_TARGET_WINDOWS = [
  "stage_ii_iii_or_locally_advanced",
  "advanced_or_metastatic",
  "mixed_or_manual",
  "not_stage_specific",
];
const DEFAULT_DEMO_LENS = "breast_oncology_ai";

function parseSpaceFromSearch(search) {
  const params = new URLSearchParams(search || "");
  const raw = params.get("space") || "";
  return VALID_SPACES.includes(raw) ? raw : "landing";
}

function normalizeDirection(raw) {
  const value = String(raw || "")
    .trim()
    .toLowerCase();
  if (value === "trial" || value === "trial_to_patients") {
    return "trial_to_patients";
  }
  if (value === "patient" || value === "patient_to_trials") {
    return "patient_to_trials";
  }
  return "patient_to_trials";
}

function normalizePopulationMode(raw) {
  const value = String(raw || "")
    .trim()
    .toLowerCase();
  return VALID_POPULATION_MODES.includes(value)
    ? value
    : "authoritative_oncology";
}

function normalizeStageTargetWindow(raw) {
  const value = String(raw || "")
    .trim()
    .toLowerCase();
  return VALID_STAGE_TARGET_WINDOWS.includes(value) ? value : "";
}

function normalizeDemoLens(raw) {
  const value = String(raw || "")
    .trim()
    .toLowerCase();
  return VALID_DEMO_LENSES.includes(value) ? value : DEFAULT_DEMO_LENS;
}

function parseNavigatorContext(search) {
  const params = new URLSearchParams(search || "");
  const patientRef = params.get("patientRef") || "";
  const hasPatientRef = Boolean(String(patientRef).trim());
  return {
    appointmentId: hasPatientRef
      ? ""
      : (params.get("appointmentId") || params.get("appointmentID") || ""),
    patientId: hasPatientRef
      ? ""
      : (params.get("patientId") || params.get("patientID") || ""),
    patientRef,
    selectedTrialId:
      params.get("trialId") || params.get("selectedTrialId") || "",
    matchDirection: normalizeDirection(params.get("direction")),
    populationMode: normalizePopulationMode(params.get("populationMode")),
    stageTargetWindow: normalizeStageTargetWindow(
      params.get("stageTargetWindow") || params.get("stage_target_window"),
    ),
    benchmarkPackId: params.get("benchmarkPackId") || "",
    showcaseCase: params.get("showcaseCase") || "",
    launchSource: params.get("launchSource") || "",
    demoLens: normalizeDemoLens(params.get("lens")),
  };
}

function PharmaNavigatorRouter() {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchString = useMemo(() => {
    const qs = searchParams?.toString() || "";
    return qs ? `?${qs}` : "";
  }, [searchParams]);
  const parsedContext = parseNavigatorContext(searchString);
  const navigatorBasePath = pathname?.startsWith("/pharmaNavigator/studio")
    ? "/pharmaNavigator/studio"
    : pathname?.startsWith("/pharmaNavigator")
      ? "/pharmaNavigator"
      : "/";

  const [space, setSpace] = useState(() => parseSpaceFromSearch(searchString));
  const [appointmentId, setAppointmentId] = useState(
    () => parsedContext.appointmentId,
  );
  const [patientId, setPatientId] = useState(() => parsedContext.patientId);
  const [selectedPatientRef, setSelectedPatientRef] = useState(
    () => parsedContext.patientRef,
  );
  const [selectedTrialId, setSelectedTrialId] = useState(
    () => parsedContext.selectedTrialId,
  );
  const [matchDirection, setMatchDirection] = useState(
    () => parsedContext.matchDirection,
  );
  const [populationMode, setPopulationMode] = useState(
    () => parsedContext.populationMode,
  );
  const [stageTargetWindow, setStageTargetWindow] = useState(
    () => parsedContext.stageTargetWindow,
  );
  const [benchmarkPackId, setBenchmarkPackId] = useState(
    () => parsedContext.benchmarkPackId,
  );
  const [showcaseCase, setShowcaseCase] = useState(
    () => parsedContext.showcaseCase,
  );
  const [launchSource, setLaunchSource] = useState(
    () => parsedContext.launchSource,
  );
  const [demoLens, setDemoLensState] = useState(() => parsedContext.demoLens);

  // Sync space from URL when browser back/forward is used
  useEffect(() => {
    const parsed = parseSpaceFromSearch(searchString);
    const context = parseNavigatorContext(searchString);
    setSpace(parsed);
    setAppointmentId(context.appointmentId);
    setPatientId(context.patientId);
    setSelectedPatientRef(context.patientRef);
    setSelectedTrialId(context.selectedTrialId);
    setMatchDirection(context.matchDirection);
    setPopulationMode(context.populationMode);
    setStageTargetWindow(context.stageTargetWindow);
    setBenchmarkPackId(context.benchmarkPackId);
    setShowcaseCase(context.showcaseCase);
    setLaunchSource(context.launchSource);
    setDemoLensState(context.demoLens);
  }, [searchString]);

  const navigateSpace = useCallback(
    (spaceName, params) => {
      const target = VALID_SPACES.includes(spaceName) ? spaceName : "landing";
      const searchParams = new URLSearchParams();
      const resolvedLens = normalizeDemoLens(params?.lens || demoLens);
      const resolvedPatientRef = String(
        params?.patientRef ?? selectedPatientRef ?? "",
      ).trim();

      if (target !== "landing") {
        searchParams.set("space", target);
      }
      if (resolvedLens !== DEFAULT_DEMO_LENS) {
        searchParams.set("lens", resolvedLens);
      }

      const preservedContext = {
        appointmentId: resolvedPatientRef ? "" : appointmentId,
        patientId: resolvedPatientRef ? "" : patientId,
        patientRef: resolvedPatientRef,
        trialId: selectedTrialId,
        direction: matchDirection,
        populationMode,
        stageTargetWindow,
        benchmarkPackId,
        showcaseCase,
        launchSource,
      };

      Object.entries(preservedContext).forEach(([k, v]) => {
        if (v === undefined || v === null || v === "") return;
        if (k === "direction") {
          searchParams.set(k, normalizeDirection(v));
        } else if (k === "populationMode") {
          searchParams.set(k, normalizePopulationMode(v));
        } else if (k === "stageTargetWindow") {
          const normalizedStageTargetWindow = normalizeStageTargetWindow(v);
          if (normalizedStageTargetWindow) {
            searchParams.set("stageTargetWindow", normalizedStageTargetWindow);
          }
        } else {
          searchParams.set(k, v);
        }
      });

      // Merge any extra params passed by the caller
      if (params) {
        Object.entries(params).forEach(([k, v]) => {
          if (v !== undefined && v !== null && v !== "") {
            if (k === "direction") {
              searchParams.set(k, normalizeDirection(v));
            } else if (k === "populationMode") {
              searchParams.set(k, normalizePopulationMode(v));
            } else if (
              k === "stageTargetWindow" ||
              k === "stage_target_window"
            ) {
              const normalizedStageTargetWindow = normalizeStageTargetWindow(v);
              if (normalizedStageTargetWindow) {
                searchParams.set(
                  "stageTargetWindow",
                  normalizedStageTargetWindow,
                );
              }
            } else if (k === "lens") {
              const normalizedLens = normalizeDemoLens(v);
              if (normalizedLens !== DEFAULT_DEMO_LENS) {
                searchParams.set("lens", normalizedLens);
              }
            } else {
              searchParams.set(k, v);
            }
          }
        });
      }

      if (String(searchParams.get("patientRef") || "").trim()) {
        searchParams.delete("appointmentId");
        searchParams.delete("appointmentID");
        searchParams.delete("patientId");
        searchParams.delete("patientID");
      }

      const qs = searchParams.toString();
      router.push(`${navigatorBasePath}${qs ? `?${qs}` : ""}`);
      setSpace(target);
    },
    [
      appointmentId,
      benchmarkPackId,
      demoLens,
      launchSource,
      matchDirection,
      navigatorBasePath,
      patientId,
      populationMode,
      router,
      selectedPatientRef,
      selectedTrialId,
      showcaseCase,
      stageTargetWindow,
    ],
  );

  const setDemoLens = useCallback(
    (nextLens) => {
      const normalizedLens = normalizeDemoLens(nextLens);
      navigateSpace(space, {
        lens: normalizedLens,
        appointmentId,
        patientId,
        patientRef: selectedPatientRef,
        trialId: selectedTrialId,
        direction: matchDirection,
        populationMode,
        stageTargetWindow,
        benchmarkPackId,
        showcaseCase,
        launchSource,
      });
      setDemoLensState(normalizedLens);
    },
    [
      appointmentId,
      benchmarkPackId,
      launchSource,
      matchDirection,
      navigateSpace,
      patientId,
      populationMode,
      selectedPatientRef,
      selectedTrialId,
      showcaseCase,
      space,
      stageTargetWindow,
    ],
  );

  const sharedProps = {
    navigateSpace,
    appointmentId,
    setAppointmentId,
    patientId,
    setPatientId,
    selectedPatientRef,
    setSelectedPatientRef,
    selectedTrialId,
    setSelectedTrialId,
    matchDirection,
    setMatchDirection,
    populationMode,
    setPopulationMode,
    stageTargetWindow,
    setStageTargetWindow,
    benchmarkPackId,
    setBenchmarkPackId,
    showcaseCase,
    setShowcaseCase,
    launchSource,
    setLaunchSource,
    demoLens,
  };

  let activeSpace = null;
  if (space === "landing") activeSpace = <PharmaLanding {...sharedProps} />;
  if (space === "ingestion")
    activeSpace = <TrialIngestionStudio {...sharedProps} />;
  if (space === "patient")
    activeSpace = <PatientCurationStudio {...sharedProps} />;
  if (space === "matching") activeSpace = <MatchingStudio {...sharedProps} />;
  if (space === "control")
    activeSpace = <ControlPlaneStudio {...sharedProps} />;
  if (space === "pattern") activeSpace = <PatternArticle {...sharedProps} />;

  return (
    <AtlasWorkbenchShell
      space={space}
      lens={demoLens}
      selectedPatientRef={selectedPatientRef}
      selectedTrialId={selectedTrialId}
      matchDirection={matchDirection}
      populationMode={populationMode}
      stageTargetWindow={stageTargetWindow}
      benchmarkPackId={benchmarkPackId}
      navigateSpace={navigateSpace}
      setDemoLens={setDemoLens}
    >
      <div className="pds-fade-in" key={`${space}:${demoLens}`}>
        {activeSpace}
      </div>
    </AtlasWorkbenchShell>
  );
}

export default PharmaNavigatorRouter;
