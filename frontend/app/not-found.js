export default function NotFound() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 32,
        background: "#f8fafc",
        color: "#0f172a",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <div
        style={{
          maxWidth: 520,
          width: "100%",
          background: "#ffffff",
          border: "1px solid #dbe5f0",
          borderRadius: 24,
          padding: 32,
          boxShadow: "0 24px 48px rgba(15, 23, 42, 0.08)",
        }}
      >
        <div
          style={{
            fontSize: 12,
            fontWeight: 700,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "#00684A",
            marginBottom: 10,
          }}
        >
          Clinical Trial Matching Agent
        </div>
        <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.1 }}>
          Page not found
        </h1>
        <p style={{ margin: "12px 0 0", fontSize: 16, lineHeight: 1.6, color: "#475569" }}>
          The Quinico standalone demo could not find that route. Return to the
          launcher and open one of the trial ingestion, patient curation,
          matching, or control-plane spaces.
        </p>
      </div>
    </main>
  );
}
