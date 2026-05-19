import React, { createContext, useMemo, useState } from "react";

const DEFAULT_PREFERENCES = Object.freeze({
  dataSets: [
    {
      screening: {
        selected: {
          HCProviderApi: "MongoDB",
          HCAgencyApi: "MongoDB",
        },
      },
    },
  ],
  screening: {
    loadInitialAppointments: 24,
  },
  branding: {
    whitelabel: false,
    colour: "#15803D",
    colourSecondary: "#0f766e",
    logo: "",
  },
  locale: {
    lng: "en",
  },
});

const DEFAULT_TRANSLATIONS = Object.freeze({
  general: {
    loading: "Loading...",
  },
});

const AppContext = createContext({
  preferences: DEFAULT_PREFERENCES,
  translations: DEFAULT_TRANSLATIONS,
  currentAppUser: "hc-provider",
  currentCenter: "Mayo Clinic Hospital - Phoenix",
  selectedFilters: {
    searchQuery: "",
    facets: [],
  },
  setPreferences: () => {},
  setCurrentAppUser: () => {},
  setCurrentCenter: () => {},
  setSelectedFilters: () => {},
  fetchUserPreferences: async () => DEFAULT_PREFERENCES,
});

const AppProvider = ({ children }) => {
  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES);
  const [currentAppUser, setCurrentAppUser] = useState("hc-provider");
  const [currentCenter, setCurrentCenter] = useState("Mayo Clinic Hospital - Phoenix");
  const [selectedFilters, setSelectedFilters] = useState({
    searchQuery: "",
    facets: [],
  });

  const contextValue = useMemo(
    () => ({
      preferences,
      setPreferences,
      translations: DEFAULT_TRANSLATIONS,
      activityLog: [],
      setActivityLog: () => {},
      currentAppUser,
      setCurrentAppUser,
      currentCenter,
      setCurrentCenter,
      selectedFilters,
      setSelectedFilters,
      fetchUserPreferences: async () => preferences,
    }),
    [preferences, currentAppUser, currentCenter, selectedFilters]
  );

  return <AppContext.Provider value={contextValue}>{children}</AppContext.Provider>;
};

export { AppContext, AppProvider };
